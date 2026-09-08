"""Fit the response curve of yield against each controllable lever.

    python scripts/build_lever_curves.py

Writes data/api/lever_curves.json, which is what POST /api/v1/recommend serves.
Regenerate it whenever the feature file changes.

WHY THIS AND NOT THE MODEL
--------------------------
CRISP-DM report §4.1 proposes the recommendation layer as a constrained search
over the trained GBM's predicted surface. That is the wrong instrument for this
dataset and the modelling notebooks say why: per-plot out-of-time R2 is ~0.16,
and `district` alone accounts for ~60% of the model's holdout performance
(notebook 03 §5). Optimising over a surface that weak returns advice with a
precision the evidence cannot carry.

The estimand a recommendation actually needs is different from the estimand the
model targets. Not "what will this plot yield" but "how does yield move when
this decision moves" -- and that average gradient is estimable here. Notebook 03
§6 demonstrated it on planting date: within district x season cells, over 21,000
plots, the timing penalty is sharp, survives controls for inputs and wealth, and
implies ~+250 kg/ha for the third of plots sown after March 31. This script
generalises that method to every ex-ante lever the survey measures.

METHOD
------
For each lever, on all seasons:

  y_ipt = f(lever_ipt) + b'C_ipt + a_pt + e_ipt      clustered on district p

  a_pt      district x season fixed effect, absorbed by within-transformation.
            It is doing the heavy lifting: notebook 03 §2 showed that pooled
            correlations in this dataset are mostly altitude and season labels,
            and that comparing plots only against others in the same place and
            the same year is what removes them.
  f(.)      linear spline -- an indicator for any use, a linear term, and a
            hinge at each knot. Diminishing returns are fitted, not assumed,
            which is what makes the §1.3 plausibility criterion checkable.
  C         the OTHER levers, plus plot size, wealth index, cattle, household
            size. Every coefficient is therefore ceteris paribus, which is what
            lets the API add selected recommendations into a bundle without
            counting the same nitrogen twice.
  cluster   district. 51 clusters; plots in one place are not independent.

Three diagnostics are stored beside each curve, because the honest reading of
one of these numbers depends on all three: the same contrast refit WITHOUT
controls (how much of the raw association is confounded by who uses the input),
refit on 2016-2019 ONLY (does it hold out of time), and a shape classification
(does the fitted curve behave the way agronomy says it should).

CAUSAL STATUS -- read before quoting any number this produces. Farmers choose
these inputs; nobody randomised them. District x season effects absorb the
place-and-year version of that selection, and the controls absorb the visible
part of the farmer-level version, but not its unobserved part. A farmer who
plants late may be doing so because the rains were late on their own farm. These
are strong associations with plausible mechanisms and stated uncertainty, not
trial effect sizes.

Imports pandas and numpy only -- never `district_model` -- so the artefact can
be rebuilt without the model stack installed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.curves import basis, value_at                      # noqa: E402
from scripts.build_api_defaults import project_root, restore_dtypes   # noqa: E402

OUT_REL = Path("data") / "api" / "lever_curves.json"
TARGET = "yield_kg_ph"

# Levers are chosen to SPAN the farmer's decision space without OVERLAPPING it.
# The feature file carries 45 ex-ante lever columns, but most are algebraic
# restatements of one another -- n_kg_ph, p2o5_kg_ph and total_nutrient_kg_ph
# are near-deterministic functions of dap/can/urea/npk by construction
# (build_features.py §4), and uses_hybrid_seed is hybrid_seed_share thresholded.
# Recommending a set like that would credit one bag of DAP three times over.
# So each lever below is a thing a farmer separately buys or separately decides:
# the two fertilisers that are actually used at scale (DAP basal in 85% of rows,
# CAN topdress in 63%), seed genetics, seed variety within that, compost, lime,
# planting date, intercropping.
LEVERS = [
    dict(name="planting_date", column="plant_date_doy", kind="continuous",
         label="Planting date", unit="day of year", zero_indicator=False,
         valid=(1, 180), n_knots=4,
         expect="peak", direction="earlier planting within the window",
         action_more="plant later", action_less="plant earlier",
         why="The only lever a farmer can change without buying anything. "
             "Notebook 03 §6: the yield-date "
             "curve is flat to mid-March then falls monotonically."),
    dict(name="basal_fertiliser", column="dap_kg_ph_winsorized", kind="continuous",
         label="DAP at planting", unit="kg/ha", zero_indicator=True,
         valid=(0, 500), n_knots=3,
         expect="diminishing", direction="more",
         action_more="apply more DAP at planting", action_less="cut DAP back",
         why="Basal phosphorus and starter nitrogen; the single most common "
             "purchased input in the survey."),
    dict(name="topdress_fertiliser", column="can_kg_ph_winsorized", kind="continuous",
         label="CAN topdress", unit="kg/ha", zero_indicator=True,
         valid=(0, 500), n_knots=3,
         expect="diminishing", direction="more",
         action_more="topdress with more CAN", action_less="cut CAN back",
         why="Notebook 03 §2 found the binary 'did they topdress at all' as "
             "informative as the continuous nitrogen rate -- the margin is "
             "between zero and some, not between some and more."),
    dict(name="hybrid_seed", column="hybrid_seed_share", kind="continuous",
         label="Share of seed that is hybrid", unit="share of seed (0-1)",
         zero_indicator=False, valid=(0, 1), n_knots=1,
         expect="increasing", direction="more",
         action_more="plant a larger share of hybrid seed",
         action_less="plant less hybrid seed",
         why="The strongest within-district correlate of yield in the file "
             "(r = +0.205, notebook 03 §2)."),
    dict(name="seed_variety", column="seed_type_primary", kind="categorical",
         label="Seed variety", unit=None, min_level_n=250,
         expect=None, direction=None,
         action_more="switch variety", action_less="switch variety",
         why="Estimated holding hybrid share fixed, so this is which variety "
             "within a genetics class, not hybrid-versus-local again."),
    dict(name="compost", column="comp_wb_pa_winsorized", kind="continuous",
         label="Compost", unit="wheelbarrows per acre", zero_indicator=True,
         valid=(0, 140), n_knots=2,
         expect="diminishing", direction="more",
         action_more="apply more compost", action_less="apply less compost",
         why="Raw correlation (+0.03) understates it threefold: compost use is "
             "concentrated in less-productive places (notebook 03 §2)."),
    dict(name="lime", column="lime_kg_ph_winsorized", kind="continuous",
         label="Agricultural lime", unit="kg/ha", zero_indicator=True,
         valid=(0, 2000), n_knots=1,
         expect="diminishing", direction="more",
         action_more="apply lime", action_less="apply less lime",
         why="Only 4% of rows apply any. Fitted so the evidence can speak, but "
             "expected to fail the significance gate and stay unrecommended."),
    dict(name="intercropping", column="intercrop", kind="binary",
         label="Intercropping", unit=None, valid=(0, 1),
         expect=None, direction=None,
         action_more="intercrop this plot", action_less="stop intercropping",
         why="Free to change, and the sign is an empirical question rather than "
             "an agronomic given."),
]

BASE_CONTROLS = ["plot_acres", "wealth_index", "cows", "hh_num"]
GRID_POINTS = 41


# ---------------------------------------------------------------------------
# fixed-effects estimation
# ---------------------------------------------------------------------------
def _demean(M: np.ndarray, codes: np.ndarray, n_groups: int) -> np.ndarray:
    """Within-transformation: subtract each row's group mean, column by column.

    Absorbing the district x season effects this way is algebraically identical
    to including ~250 dummies and vastly cheaper.
    """
    counts = np.bincount(codes, minlength=n_groups).astype(float)
    counts[counts == 0] = 1.0
    out = np.empty_like(M)
    for j in range(M.shape[1]):
        sums = np.bincount(codes, weights=M[:, j], minlength=n_groups)
        out[:, j] = M[:, j] - (sums / counts)[codes]
    return out


def fit_fe(y: np.ndarray, Z: np.ndarray, cells: np.ndarray, n_cells: int,
           clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """OLS with absorbed fixed effects and a district-clustered covariance.

    Returns (coefficients, covariance, within-R2). `pinv` rather than `solve`:
    a control column can be collinear inside a small subsample, and a rank
    deficiency should widen the covariance, not raise.
    """
    y_dm = _demean(y.reshape(-1, 1), cells, n_cells).ravel()
    Z_dm = _demean(Z, cells, n_cells)

    xtx_inv = np.linalg.pinv(Z_dm.T @ Z_dm)
    beta = xtx_inv @ (Z_dm.T @ y_dm)
    resid = y_dm - Z_dm @ beta

    order = np.argsort(clusters, kind="stable")
    bounds = np.flatnonzero(np.diff(clusters[order])) + 1
    meat = np.zeros((Z.shape[1], Z.shape[1]))
    for idx in np.split(order, bounds):
        s = Z_dm[idx].T @ resid[idx]
        meat += np.outer(s, s)

    n, k = Z.shape
    g = len(np.unique(clusters))
    dof = max(n - k - n_cells, 1)
    scale = (g / max(g - 1, 1)) * ((n - 1) / dof)
    cov = xtx_inv @ meat @ xtx_inv * scale

    tss = float(y_dm @ y_dm)
    within_r2 = 1.0 - float(resid @ resid) / tss if tss > 0 else 0.0
    return beta, cov, within_r2


# ---------------------------------------------------------------------------
# design
# ---------------------------------------------------------------------------
def make_spec(lever: dict, series: pd.Series) -> dict:
    """The basis definition: support, knots, and the value grid we may propose."""
    spec = {"name": lever["name"], "column": lever["column"], "kind": lever["kind"],
            "label": lever["label"], "unit": lever["unit"],
            "levels": None, "reference_level": None}

    if lever["kind"] == "categorical":
        counts = series.astype(str).value_counts()
        levels = [v for v, n in counts.items()
                  if n >= lever["min_level_n"] and v not in ("nan", "None", "")]
        if len(levels) < 2:
            return {}
        spec["levels"] = levels                       # levels[0] is the reference
        spec["reference_level"] = levels[0]
        spec["basis"] = {"type": "categorical", "domain": [0, 0], "knots": []}
        spec["grid"] = levels
        return spec

    x = series.astype(float).dropna()
    lo_valid, hi_valid = lever["valid"]
    x = x[(x >= lo_valid) & (x <= hi_valid)]
    lo, hi = float(np.quantile(x, 0.01)), float(np.quantile(x, 0.99))

    if lever["kind"] == "binary":
        spec["basis"] = {"type": "binary", "domain": [0.0, 1.0],
                         "knots": [], "zero_indicator": False}
        spec["grid"] = [0.0, 1.0]
        return spec

    # Knots on the positive part: a zero-inflated column would otherwise stack
    # every knot at zero and fit a straight line through the part that matters.
    positive = x[x > 0] if lever["zero_indicator"] else x
    # Knots must be genuinely distinct. Rounding alone is not enough: a
    # fertiliser column has mass piled on retail bag rates, so two quantiles
    # land on 123.5525 and 123.5526 and produce a redundant basis column.
    qs = np.linspace(0.25, 0.80, lever["n_knots"])
    separation = (hi - lo) / 50.0
    knots: list[float] = []
    for value in sorted(float(v) for v in np.quantile(positive, qs)):
        if lo < value < hi and (not knots or value - knots[-1] > separation):
            knots.append(round(value, 2))
    spec["basis"] = {"type": "hinge", "domain": [lo, hi], "knots": knots,
                     "zero_indicator": bool(lever["zero_indicator"])}
    spec["grid"] = [round(float(v), 4) for v in np.linspace(lo, hi, GRID_POINTS)]
    return spec


def design(spec: dict, series: pd.Series) -> np.ndarray:
    return np.vstack([basis(spec, v) for v in series])


def controls(df: pd.DataFrame, lever: dict, columns: list[str]) -> np.ndarray:
    """The other levers plus the household/plot covariates, median-imputed."""
    names = [c for c in columns if c != lever["column"]] + BASE_CONTROLS
    out = []
    for name in names:
        if name not in df.columns:
            continue
        s = pd.to_numeric(df[name], errors="coerce")
        if s.notna().sum() < 100 or s.dropna().nunique() < 2:
            continue
        filled = s.fillna(s.median()).to_numpy(dtype=float)
        if np.nanstd(filled) == 0:
            continue
        out.append(filled)
        if s.isna().mean() > 0.02:
            out.append(s.isna().to_numpy(dtype=float))
    return np.column_stack(out) if out else np.zeros((len(df), 0))


# ---------------------------------------------------------------------------
# fitting one lever
# ---------------------------------------------------------------------------
def fit_lever(lever: dict, df: pd.DataFrame, numeric_levers: list[str],
              log) -> dict | None:
    col = lever["column"]
    if col not in df.columns:
        log(f"  {lever['name']:<20s} SKIPPED — {col} not in the feature file")
        return None

    frame = df[df[col].notna()].copy()
    if lever["kind"] != "categorical":
        x = pd.to_numeric(frame[col], errors="coerce")
        lo, hi = lever["valid"]
        frame = frame[x.between(lo, hi)]

    spec = make_spec(lever, frame[col])
    if not spec:
        log(f"  {lever['name']:<20s} SKIPPED — too few usable levels")
        return None

    if spec["kind"] == "categorical":
        frame = frame[frame[col].astype(str).isin(spec["levels"])]
    if len(frame) < 500:
        log(f"  {lever['name']:<20s} SKIPPED — only {len(frame)} usable rows")
        return None

    y = frame[TARGET].to_numpy(dtype=float)
    cells, n_cells = _codes(frame.district.astype(str) + "|" + frame.year.astype(str))
    clusters, _ = _codes(frame.district.astype(str))
    X = design(spec, frame[col])
    C = controls(frame, lever, numeric_levers)
    k = X.shape[1]

    beta, cov, r2 = fit_fe(y, np.hstack([X, C]), cells, n_cells, clusters)
    spec["coefs"] = [float(v) for v in beta[:k]]
    spec["cov"] = [[float(v) for v in row] for row in cov[:k, :k]]

    spec["n"] = int(len(frame))
    spec["n_clusters"] = int(len(np.unique(clusters)))
    spec["n_cells"] = int(n_cells)
    spec["within_r2"] = round(float(r2), 5)
    spec["why"] = lever["why"]
    spec["action"] = {"more": lever["action_more"], "less": lever["action_less"]}
    spec["population"] = _population(frame[col], spec)
    spec["support"] = _support(frame[col], spec)
    spec["district_current"] = _district_current(frame, col, spec)
    spec["diagnostics"] = _diagnostics(lever, spec, frame, y, X, C, cells,
                                       n_cells, clusters, k)
    log(f"  {lever['name']:<20s} n={spec['n']:>6,}  "
        f"cells={n_cells:>4}  within-R2={r2:+.4f}  "
        f"headline {spec['diagnostics']['headline_lift_kg_ph']:+.0f} kg/ha "
        f"(SE {spec['diagnostics']['headline_se_kg_ph']:.0f})  "
        f"shape={spec['diagnostics']['shape']}  "
        f"{'PLAUSIBLE' if spec['diagnostics']['agronomic_check']['passed'] else 'CHECK'}")
    return spec


def _codes(series: pd.Series) -> tuple[np.ndarray, int]:
    codes, uniques = pd.factorize(series, sort=False)
    return codes.astype(np.intp), len(uniques)


def _population(series: pd.Series, spec: dict) -> dict:
    if spec["kind"] == "categorical":
        counts = series.astype(str).value_counts(normalize=True)
        return {"median": str(counts.index[0]),
                "shares": {str(k): round(float(v), 4) for k, v in counts.items()}}
    x = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    return {"median": round(float(x.median()), 4),
            "mean": round(float(x.mean()), 4),
            "p10": round(float(x.quantile(0.10)), 4),
            "p90": round(float(x.quantile(0.90)), 4),
            "share_zero": round(float((x <= 0).mean()), 4)}


def _support(series: pd.Series, spec: dict) -> list[int]:
    """Plots observed near each grid value.

    The gate that stops the curve being read where the data runs out. Only 4%
    of rows apply any lime and 16% any compost, so the fitted optimum for those
    levers sits in a region holding a few dozen plots -- an upward slope there
    is a shape drawn through noise, and the API refuses to propose a value
    whose support is thin rather than quoting its confident-looking lift.
    """
    if spec["kind"] == "categorical":
        counts = series.astype(str).value_counts()
        return [int(counts.get(level, 0)) for level in spec["levels"]]
    x = pd.to_numeric(series, errors="coerce").dropna().astype(float).to_numpy()
    grid = np.asarray(spec["grid"], dtype=float)
    if spec["kind"] == "binary":
        return [int((x <= 0.5).sum()), int((x > 0.5).sum())]
    half = (grid[-1] - grid[0]) / 20.0
    return [int(((x >= g - half) & (x <= g + half)).sum()) for g in grid]


def _district_current(frame: pd.DataFrame, col: str, spec: dict) -> dict:
    """What a typical plot in each district already does -- the starting point
    when a caller does not say."""
    out = {}
    for district, s in frame.groupby(frame.district.astype(str).str.lower(),
                                     observed=True)[col]:
        if len(s) < 30:
            continue
        if spec["kind"] == "categorical":
            out[district] = str(s.astype(str).mode().iloc[0])
        else:
            out[district] = round(float(pd.to_numeric(s, errors="coerce").median()), 4)
    return out


# ---------------------------------------------------------------------------
# diagnostics: the three things that decide how to read a coefficient
# ---------------------------------------------------------------------------
def _headline_contrast(spec: dict) -> tuple:
    """A single comparison per lever, used for all three diagnostics so they
    are commensurable: the population's typical value against the curve's best."""
    grid = spec["grid"]
    ys = [value_at(spec, v) for v in grid]
    best = grid[int(np.argmax(ys))]
    return spec["population"]["median"], best


def _refit(y, X, C, cells, n_cells, clusters, k) -> np.ndarray:
    """The lever block of a refit, as a coefficient vector.

    Whole vectors rather than one number, so the API can put ANY contrast --
    including the one it actually recommends for a given plot -- through the
    uncontrolled and out-of-time fits too. A diagnostic computed only at the
    population's median contrast would sit beside a per-plot recommendation
    describing a different comparison.
    """
    beta, _, _ = fit_fe(y, np.hstack([X, C]), cells, n_cells, clusters)
    return beta[:k]


def _lift(spec: dict, coefs, frm, to) -> float:
    d = basis(spec, to) - basis(spec, frm)
    return float(d @ np.asarray(coefs, dtype=float))


def _diagnostics(lever, spec, frame, y, X, C, cells, n_cells, clusters, k) -> dict:
    frm, to = _headline_contrast(spec)
    d = basis(spec, to) - basis(spec, frm)
    beta = np.asarray(spec["coefs"])
    cov = np.asarray(spec["cov"])
    lift = float(d @ beta)
    se = float(np.sqrt(max(float(d @ cov @ d), 0.0)))

    # 1. no controls: how much of the association is who chooses the input
    #    rather than the input itself.
    spec["coefs_uncontrolled"] = [
        float(v) for v in _refit(y, X, np.zeros((len(y), 0)), cells, n_cells,
                                 clusters, k)]
    bare = _lift(spec, spec["coefs_uncontrolled"], frm, to)

    # 2. out of time: refit on 2016-2019 only.
    pre = (frame.year < 2020).to_numpy()
    holdout_lift = None
    if pre.sum() > 400 and len(np.unique(clusters[pre])) > 5:
        cells_pre, n_pre = _codes(pd.Series(cells[pre]))
        spec["coefs_to2019"] = [
            float(v) for v in _refit(y[pre], X[pre], C[pre], cells_pre, n_pre,
                                     clusters[pre], k)]
        holdout_lift = _lift(spec, spec["coefs_to2019"], frm, to)

    # The headline is what the MEDIAN plot would gain, so for a lever the median
    # plot already gets right it is legitimately zero. The full-range lift is
    # the separate question of how much the lever moves yield at all, and it is
    # the one the plausibility check needs.
    grid = spec["grid"]
    ys = [value_at(spec, v) for v in grid]
    full_range = float(max(ys) - min(ys))
    if spec["kind"] != "categorical" and ys[-1] < ys[0]:
        full_range = -full_range

    shape = _shape(spec)
    return {
        "full_range_lift_kg_ph": round(full_range, 1),
        "headline_contrast": {"from": frm, "to": to},
        "headline_lift_kg_ph": round(lift, 1),
        "headline_se_kg_ph": round(se, 1),
        "headline_t": round(lift / se, 2) if se > 0 else None,
        "uncontrolled_lift_kg_ph": round(bare, 1),
        "control_absorbed_share": (round(1 - lift / bare, 3)
                                   if abs(bare) > 1e-9 else None),
        "trained_to_2019_lift_kg_ph": (round(holdout_lift, 1)
                                       if holdout_lift is not None else None),
        "shape": shape,
        "agronomic_check": _agronomic_check(lever, spec, shape, full_range),
    }


def _shape(spec: dict) -> str:
    if spec["kind"] == "categorical":
        return "categorical"
    ys = np.array([value_at(spec, v) for v in spec["grid"]])
    slopes = np.diff(ys)
    if np.all(slopes >= -1e-9):
        base = "monotone_increasing"
    elif np.all(slopes <= 1e-9):
        base = "monotone_decreasing"
    elif int(np.argmax(ys)) not in (0, len(ys) - 1):
        base = "interior_peak"
    else:
        base = "non_monotone"
    if base == "monotone_increasing" and np.all(np.diff(slopes) <= 1e-9):
        return "concave_increasing"          # diminishing returns
    return base


def _agronomic_check(lever, spec, shape, full_range) -> dict:
    """CRISP-DM §1.3: a recommendation must be directionally consistent with
    known agronomics or field staff will not trust it. Machine-checkable here
    because the curve shape is fitted rather than imposed."""
    expect = lever["expect"]
    if expect is None:
        return {"expected": "no prior", "passed": True,
                "note": "sign left to the data"}
    if expect == "diminishing":
        passed = shape in ("concave_increasing", "interior_peak",
                           "monotone_increasing")
        return {"expected": "positive with diminishing returns", "passed": passed,
                "note": f"fitted shape is {shape}"}
    if expect == "increasing":
        passed = full_range > 0
        return {"expected": "positive", "passed": passed,
                "note": f"fitted shape is {shape}"}
    if expect == "peak":
        passed = shape in ("interior_peak", "monotone_decreasing")
        return {"expected": "a favourable window, penalty outside it",
                "passed": passed, "note": f"fitted shape is {shape}"}
    return {"expected": expect, "passed": True, "note": f"fitted shape is {shape}"}


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--features", default="data/features/kenya_maize_features_full.csv")
    ap.add_argument("--out", default=str(OUT_REL))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    root = project_root()
    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a))

    needed = ({TARGET, "district", "year"} | {l["column"] for l in LEVERS}
              | set(BASE_CONTROLS))
    path = root / args.features
    header = pd.read_csv(path, nrows=0).columns
    df = restore_dtypes(pd.read_csv(path, usecols=[c for c in header if c in needed],
                                    low_memory=False))
    df = df[df[TARGET].notna() & df.district.notna() & df.year.notna()]
    log(f"Fitting lever curves on {len(df):,} rows, "
        f"seasons {sorted(df.year.unique().tolist())}\n")

    # Observed yields, so the API can express a lift as a share of what the
    # district actually produces. A +2,400 kg/ha bundle on a 2,900 kg/ha district
    # is arithmetically what the curves say and still worth flagging out loud.
    by_district = df.groupby(df.district.astype(str).str.lower(),
                             observed=True)[TARGET].agg(["mean", "size"])
    reference_yield = {
        "national_mean_kg_ph": round(float(df[TARGET].mean()), 1),
        "by_district_kg_ph": {d: round(float(r["mean"]), 1)
                              for d, r in by_district.iterrows() if r["size"] >= 30},
    }

    numeric_levers = [l["column"] for l in LEVERS if l["kind"] != "categorical"]
    fitted = [s for s in (fit_lever(l, df, numeric_levers, log) for l in LEVERS)
              if s]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": TARGET,
        "unit": "kg/ha",
        "method": ("Linear-spline response curve per lever, district x season fixed "
                   "effects absorbed, other levers and household covariates held "
                   "fixed, standard errors clustered on district. Associations "
                   "with plausible mechanisms, not randomised effect sizes."),
        "source": {"features_file": args.features, "rows": int(len(df)),
                   "seasons": sorted(int(y) for y in df.year.unique())},
        "controls": BASE_CONTROLS,
        "reference_yield": reference_yield,
        "levers": fitted,
    }
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, allow_nan=False), encoding="utf-8")
    log(f"\nWrote {out.relative_to(root)} — {len(fitted)} levers, "
        f"{out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
