"""Build the fill-value artefact the API uses to expand lean requests.

The deployed model consumes 143 columns: the 66 engineered features of
`kenya_maize_recommended_features.csv` plus the 89 external weather columns
that feed the pipeline's PCA basis. No API caller can supply those -- most are
monthly climate variables, missingness flags and winsorized companions produced
by the feature pipeline. This script distils the committed feature file into a
small JSON of empirical fill values so the service can take the handful of
inputs a farmer or extension officer actually knows and reconstruct a complete,
in-distribution model row.

    python scripts/build_api_defaults.py

Writes data/api/feature_defaults.json. Regenerate it whenever the feature file
or the recommended-feature list changes.

Deliberately standalone: it imports pandas and numpy only, never
`district_model`, so the artefact can be rebuilt without the model stack
(scikit-learn, LightGBM, XGBoost, torch) installed.

Everything stored is descriptive statistics of the survey data -- medians,
modes, category vocabularies, observed ranges. No target-derived quantity is
written, so the artefact carries no leakage.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

OUT_REL = Path("data") / "api" / "feature_defaults.json"

# Columns the wealth index is built from (scripts/build_features.py). Stored so
# a single incoming request can be placed on the cohort's scale.
WEALTH_ASSETS = ["owns_cows", "owns_chickens", "owns_goats", "owns_bikes",
                 "owns_oxen", "owns_electricity", "owns_radio"]

BOOL_TOKENS = {True, False, "True", "False", "true", "false"}


def project_root(start: Path | None = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for cand in [p, *p.parents]:
        if (cand / "data" / "features").exists():
            return cand
    raise FileNotFoundError("could not locate the project root (data/features)")


def is_text(series: pd.Series) -> bool:
    """True for label columns. `dtype == object` alone is wrong on pandas 3,
    where text columns arrive as `str`/StringDtype instead."""
    return (series.dtype == object
            or isinstance(series.dtype, pd.CategoricalDtype)
            or pd.api.types.is_string_dtype(series))


def restore_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Booleans round-trip through CSV as strings; normalise them to float."""
    for c in df.columns:
        if df[c].dtype == bool:
            df[c] = df[c].astype(float)
        elif is_text(df[c]):
            vals = set(df[c].dropna().unique())
            if vals and vals <= BOOL_TOKENS:
                df[c] = df[c].map({True: 1.0, False: 0.0, "True": 1.0, "False": 0.0,
                                   "true": 1.0, "false": 0.0}).astype(float)
    return df


def resolve_schema(root: Path) -> dict:
    """Mirror of district_model.load_schema, minus the model imports."""
    fdir = root / "data" / "features"
    rec = pd.read_csv(fdir / "kenya_maize_recommended_features.csv")
    man = pd.read_csv(fdir / "kenya_maize_feature_manifest.csv").set_index("feature")
    head = restore_dtypes(pd.read_csv(fdir / "kenya_maize_features_full.csv",
                                      nrows=500, low_memory=False))
    feats = rec.feature.tolist()
    cats = [c for c in feats if is_text(head[c])]
    weather = [c for c in man.index if c in head.columns
               and pd.api.types.is_numeric_dtype(head[c])
               and man.loc[c, "source"] == "external"
               and man.loc[c, "decision"] == "keep"]
    return {"feats": feats, "cats": cats, "weather": weather,
            "columns": sorted(set(feats) | set(weather))}


def _kind(series: pd.Series) -> str:
    if is_text(series):
        return "categorical"
    vals = set(pd.unique(series.dropna()))
    if vals and vals <= {0.0, 1.0, True, False}:
        return "boolean"
    return "numeric"


def _repr_value(series: pd.Series, kind: str):
    """The stand-in for a missing input: mode for labels, median otherwise."""
    s = series.dropna()
    if s.empty:
        return None
    if kind == "categorical":
        return str(s.astype(str).mode().iloc[0])
    if kind == "boolean":
        return float(s.astype(float).mean() >= 0.5)
    return float(np.median(s.astype(float)))


def _clean(obj):
    """JSON cannot hold NaN/inf. Nulls stay -- the pipeline imputes them."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(float(obj)) else float(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    return obj


def main() -> int:
    root = project_root()
    schema = resolve_schema(root)
    cols, weather = schema["columns"], schema["weather"]
    feat_path = root / "data" / "features" / "kenya_maize_features_full.csv"

    header = pd.read_csv(feat_path, nrows=0).columns
    wanted = sorted(set(cols) | {"district", "year"} | set(WEALTH_ASSETS))
    usecols = [c for c in wanted if c in header]
    missing = [c for c in cols if c not in header]
    if missing:
        print(f"warning: {len(missing)} model columns absent from the feature file: "
              f"{missing[:5]}")

    df = restore_dtypes(pd.read_csv(feat_path, usecols=usecols, low_memory=False))
    df["district"] = df["district"].astype(str)
    present = [c for c in cols if c in df.columns]
    kinds = {c: _kind(df[c]) for c in present}

    # --- global and per-district fill values -----------------------------------
    globals_ = {c: _repr_value(df[c], kinds[c]) for c in present}
    by_district = {
        str(dist): {c: _repr_value(block[c], kinds[c]) for c in present
                    if block[c].notna().any()}
        for dist, block in df.groupby("district", observed=True)
    }

    # --- weather: per district-season, with a district climatology fallback ----
    wcols = [c for c in weather if c in df.columns]
    w_by_dy = {}
    if "year" in df.columns:
        for (dist, yr), block in df.groupby(["district", "year"], observed=True):
            if pd.isna(yr):
                continue
            w_by_dy[f"{dist}|{int(yr)}"] = {c: _repr_value(block[c], "numeric")
                                            for c in wcols if block[c].notna().any()}
    w_by_d = {str(dist): {c: _repr_value(block[c], "numeric")
                          for c in wcols if block[c].notna().any()}
              for dist, block in df.groupby("district", observed=True)}

    # --- category vocabularies and observed numeric ranges ---------------------
    categories = {c: sorted(map(str, pd.unique(df[c].dropna().astype(str))))
                  for c in schema["cats"] if c in df.columns}
    ranges = {}
    for c in present:
        if kinds[c] != "numeric":
            continue
        s = df[c].dropna().astype(float)
        if not s.empty:
            ranges[c] = {"min": float(s.min()), "p01": float(s.quantile(0.01)),
                         "p50": float(s.median()), "p99": float(s.quantile(0.99)),
                         "max": float(s.max())}

    # --- winsorization ceilings, read from the cleaning run rather than guessed -
    caps = {}
    stats_path = root / "data" / "cleaned" / "kenya_maize_cleaning_stats.json"
    if stats_path.exists():
        oh = json.loads(stats_path.read_text(encoding="utf-8")).get("outlier_handling", {})
        for col, entry in oh.items():
            if isinstance(entry, dict) and entry.get("cap_value") is not None:
                caps[col] = float(entry["cap_value"])

    # --- wealth index scale, so one request lands on the cohort's units --------
    assets = [c for c in WEALTH_ASSETS if c in df.columns]
    wealth = {}
    if assets:
        num = df[assets].astype(float)
        means, sds = num.mean(), num.std(ddof=0).replace(0.0, np.nan)
        raw = ((num - means) / sds).mean(axis=1, skipna=True)
        wealth = {"assets": assets,
                  "means": {c: float(means[c]) for c in assets},
                  "sds": {c: (None if not np.isfinite(sds[c]) else float(sds[c]))
                          for c in assets},
                  "raw_mean": float(raw.mean()), "raw_sd": float(raw.std(ddof=0))}

    years = sorted(int(y) for y in pd.unique(df["year"].dropna())) if "year" in df else []
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"features_file": str(feat_path.relative_to(root)),
                   "rows": int(len(df)), "years": years},
        "model_columns": present,
        "recommended_features": [c for c in schema["feats"] if c in df.columns],
        "categorical_features": [c for c in schema["cats"] if c in df.columns],
        "weather_features": wcols,
        "kinds": kinds,
        "categories": categories,
        "ranges": ranges,
        "caps": caps,
        "global": globals_,
        "by_district": by_district,
        "weather_by_district_year": w_by_dy,
        "weather_by_district": w_by_d,
        "districts": sorted(by_district),
        "years": years,
        "wealth": wealth,
    }

    out = root / OUT_REL
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_clean(payload), indent=1), encoding="utf-8")
    print(f"wrote {out.relative_to(root)}  ({out.stat().st_size / 1024:,.0f} KB)")
    print(f"  {len(present)} model columns ({len(payload['recommended_features'])} "
          f"recommended + {len(wcols)} weather), {len(payload['districts'])} districts, "
          f"{len(w_by_dy)} district-seasons, seasons {years}")
    if not payload["categorical_features"]:
        print("  warning: no categorical features resolved -- check the pandas version")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
