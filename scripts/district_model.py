"""District-level maize yield prediction — deployable pipeline.

The unit of prediction is a **district-season mean yield (kg/ha)**. Notebooks 05 and 06
established why: plot-level R2 is capped near 0.18 by within-district variance the survey
does not measure, while averaging plots within a district cancels most of that noise.

Pipeline
--------
    plot models          the notebook 06 recipe — LightGBM, XGBoost, RandomForest,
                         ExtraTrees and an entity-embedding MLP, each represented by the
                         average of its top configurations, blended with equal weights
    aggregation          plot predictions are averaged within (year, district)
    intervals            district-mean prediction intervals whose width is fitted on
                         validation seasons as sd(n) = sqrt(a + b / n_plots)

Everything the pipeline needs to score unseen rows is fitted on the training seasons and
stored in the artefact: the weather PCA basis, target-encoding maps, category codes,
imputation medians, the fitted estimators and the interval parameters.

Usage
-----
    from district_model import DistrictPipeline
    pipe = DistrictPipeline.load("data/models/district_v1")
    districts = pipe.predict_districts(new_rows_dataframe)

Train with `python scripts/train_district_model.py`, score with
`python scripts/predict_district.py`. See
Documentation/kenya_maize_district_model_deployment.md for measured accuracy and limits.
"""
from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))

import joblib
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.preprocessing import StandardScaler

import lightgbm as lgb
import xgboost as xgb

warnings.filterwarnings("ignore")

TARGET = "yield_kg_ph"
DEFAULT_RECIPE = "data/models/06_final_model_config.json"
NATIVE_CATS = {"LightGBM": True, "XGBoost": False, "RandomForest": False, "ExtraTrees": False}


# --------------------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------------------
def project_root(start: Path | None = None) -> Path:
    p = (start or Path(__file__)).resolve()
    for cand in [p, *p.parents]:
        if (cand / "data" / "features").exists():
            return cand
    raise FileNotFoundError("could not locate the project root (data/features)")


def restore_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """Booleans round-trip through CSV as strings; normalise the whole block to float."""
    for c in df.columns:
        if df[c].dtype == bool:
            df[c] = df[c].astype(float)
        elif df[c].dtype == object:
            v = set(df[c].dropna().unique())
            if v and v <= {True, False, "True", "False"}:
                df[c] = df[c].map({True: 1.0, False: 0.0, "True": 1.0, "False": 0.0}).astype(float)
    return df


@dataclass
class Schema:
    """The columns the pipeline consumes, resolved once from the feature manifest."""
    feats: list[str]
    cats: list[str]
    weather: list[str]
    non_weather: list[str]


def load_schema(root: Path | None = None) -> Schema:
    root = root or project_root()
    feat_dir = root / "data" / "features"
    rec = pd.read_csv(feat_dir / "kenya_maize_recommended_features.csv")
    man = pd.read_csv(feat_dir / "kenya_maize_feature_manifest.csv").set_index("feature")
    head = pd.read_csv(feat_dir / "kenya_maize_features_full.csv", nrows=200, low_memory=False)
    head = restore_dtypes(head)
    feats = rec.feature.tolist()
    cats = [c for c in feats if head[c].dtype == object]
    weather = [c for c in man.index if c in head.columns
               and pd.api.types.is_numeric_dtype(head[c])
               and man.loc[c, "source"] == "external" and man.loc[c, "decision"] == "keep"]
    non_weather = [c for c in feats if c not in weather]
    return Schema(feats=feats, cats=cats, weather=weather, non_weather=non_weather)


def load_frame(root: Path | None = None, schema: Schema | None = None) -> pd.DataFrame:
    root = root or project_root()
    df = restore_dtypes(pd.read_csv(root / "data" / "features" / "kenya_maize_features_full.csv",
                                    low_memory=False))
    schema = schema or load_schema(root)
    for c in schema.cats:
        df[c] = df[c].astype("category")
    return df


# --------------------------------------------------------------------------------------
# design matrix: fitted once on the training rows, then applied to any new frame
# --------------------------------------------------------------------------------------
@dataclass
class FittedDesign:
    """Weather PCA + target encoding + category codes, all fitted on training rows."""
    schema: Schema
    k: int = 0
    te: int = 0
    native_cats: bool = False
    te_alpha: float = 20.0

    weather_imputer_: SimpleImputer | None = None
    weather_scaler_: StandardScaler | None = None
    pca_: PCA | None = None
    te_maps_: dict[str, dict] = field(default_factory=dict)
    te_global_: float = np.nan
    cat_codes_: dict[str, dict] = field(default_factory=dict)
    num_imputer_: SimpleImputer | None = None
    columns_: list[str] = field(default_factory=list)

    # ---- fitting -----------------------------------------------------------------
    def fit(self, train: pd.DataFrame) -> "FittedDesign":
        s = self.schema
        if self.k:
            self.weather_imputer_ = SimpleImputer(strategy="median").fit(train[s.weather])
            z = self.weather_imputer_.transform(train[s.weather])
            self.weather_scaler_ = StandardScaler().fit(z)
            self.pca_ = PCA(n_components=self.k, random_state=0).fit(
                self.weather_scaler_.transform(z))
        if self.te:
            self.te_global_ = float(train[TARGET].mean())
            for c in s.cats:
                agg = train.groupby(c, observed=True)[TARGET].agg(["sum", "count"])
                enc = (agg["sum"] + self.te_global_ * self.te_alpha) / (agg["count"] + self.te_alpha)
                self.te_maps_[c] = {str(k): float(v) for k, v in enc.items()}
        if not self.native_cats:
            for c in s.cats:
                levels = pd.Index(train[c].astype(object).dropna().unique())
                self.cat_codes_[c] = {str(v): i for i, v in enumerate(sorted(map(str, levels)))}

        x = self._assemble(train, te_frame=self._oof_target_encoding(train))
        if not self.native_cats:
            self.num_imputer_ = SimpleImputer(strategy="median").fit(x)
            x = pd.DataFrame(self.num_imputer_.transform(x), columns=x.columns, index=x.index)
        self.columns_ = list(x.columns)
        return self

    def fit_transform(self, train: pd.DataFrame) -> pd.DataFrame:
        self.fit(train)
        x = self._assemble(train, te_frame=self._oof_target_encoding(train))
        return self._finish(x)

    # ---- application -------------------------------------------------------------
    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        x = self._assemble(df, te_frame=self._apply_target_encoding(df))
        return self._finish(x)

    def _finish(self, x: pd.DataFrame) -> pd.DataFrame:
        x = x.reindex(columns=self.columns_)
        if not self.native_cats and self.num_imputer_ is not None:
            x = pd.DataFrame(self.num_imputer_.transform(x), columns=self.columns_, index=x.index)
        return x

    def _pcs(self, df: pd.DataFrame) -> pd.DataFrame:
        z = self.weather_scaler_.transform(self.weather_imputer_.transform(df[self.schema.weather]))
        return pd.DataFrame(self.pca_.transform(z), index=df.index,
                            columns=[f"wpc{i + 1}" for i in range(self.k)])

    def _oof_target_encoding(self, train: pd.DataFrame) -> pd.DataFrame | None:
        """Out-of-fold encodings for the training rows, grouped by site (`cv_fold`),
        so a plot never contributes to the encoding it is scored with."""
        if not self.te:
            return None
        a, gm = self.te_alpha, self.te_global_
        out = {}
        folds = sorted(train.cv_fold.dropna().unique()) if "cv_fold" in train else []
        for c in self.schema.cats:
            col = pd.Series(np.nan, index=train.index)
            for f in folds:
                inn, held = train.cv_fold != f, train.cv_fold == f
                ag = train.loc[inn].groupby(c, observed=True)[TARGET].agg(["sum", "count"])
                e = (ag["sum"] + gm * a) / (ag["count"] + a)
                col.loc[held] = train.loc[held, c].map(e).astype(float)
            if col.isna().all():                       # no cv_fold column: fall back
                col = train[c].astype(object).astype(str).map(self.te_maps_[c]).astype(float)
            out[f"te_{c}"] = col.fillna(gm)
        return pd.DataFrame(out, index=train.index)

    def _apply_target_encoding(self, df: pd.DataFrame) -> pd.DataFrame | None:
        if not self.te:
            return None
        out = {f"te_{c}": df[c].astype(object).astype(str).map(self.te_maps_[c]).astype(float)
               .fillna(self.te_global_) for c in self.schema.cats}
        return pd.DataFrame(out, index=df.index)

    def _assemble(self, df: pd.DataFrame, te_frame: pd.DataFrame | None) -> pd.DataFrame:
        s = self.schema
        parts = [df[s.feats] if self.k == 0 else df[s.non_weather]]
        if self.k:
            parts.append(self._pcs(df))
        if te_frame is not None:
            parts.append(te_frame)
        x = pd.concat(parts, axis=1)
        if self.native_cats:
            for c in s.cats:                      # LightGBM consumes pandas categoricals
                if c in x and not isinstance(x[c].dtype, pd.CategoricalDtype):
                    x[c] = x[c].astype("category")
        else:
            for c in s.cats:
                if c in x:
                    x[c] = x[c].astype(object).astype(str).map(self.cat_codes_[c]).astype(float)
        return x


def build_estimator(name: str, params: dict):
    q = {k: v for k, v in params.items() if k not in ("k", "te", "log")}
    if name == "LightGBM":
        return lgb.LGBMRegressor(random_state=0, verbose=-1, n_jobs=-1, **q)
    if name == "XGBoost":
        return xgb.XGBRegressor(random_state=0, tree_method="hist", n_jobs=-1, **q)
    if name == "RandomForest":
        return RandomForestRegressor(random_state=0, n_jobs=-1, **q)
    if name == "ExtraTrees":
        return ExtraTreesRegressor(random_state=0, n_jobs=-1, **q)
    raise ValueError(f"unknown estimator {name}")


# --------------------------------------------------------------------------------------
# the recipe (notebook 06 §11 artefact)
# --------------------------------------------------------------------------------------
@dataclass
class PlotRecipe:
    families: dict[str, list[dict]]
    lever: dict
    clip: tuple[float, float]
    nn_params: dict | None = None
    nn_seeds: int = 3

    @classmethod
    def from_json(cls, path: Path, top_configs: int | None = None) -> "PlotRecipe":
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        nn = dict(cfg.get("neural_net", {}))
        seeds = int(nn.pop("seeds", 3))
        fams = {f: v["top_configs"][:top_configs] if top_configs else v["top_configs"]
                for f, v in cfg["families"].items()}
        return cls(families=fams, lever=cfg["protocol"]["target_treatment"],
                   clip=tuple(cfg["protocol"]["prediction_clip"]),
                   nn_params=nn or None, nn_seeds=seeds)


# --------------------------------------------------------------------------------------
# one fitted plot-level member
# --------------------------------------------------------------------------------------
@dataclass
class FittedMember:
    name: str
    params: dict
    design: FittedDesign
    model: object
    back: float = 0.0          # added back when the target was year-demeaned
    log: bool = False
    clip: tuple[float, float] = (0.0, 1e9)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        x = self.design.transform(df)
        p = self.model.predict(x)
        if self.log:
            p = np.expm1(p)
        else:
            p = p + self.back
        return np.clip(p, *self.clip)


class PlotEnsemble:
    """Fits every family/configuration in the recipe and averages their predictions."""

    def __init__(self, schema: Schema, recipe: PlotRecipe, use_nn: bool = True):
        self.schema, self.recipe, self.use_nn = schema, recipe, use_nn
        self.members_: list[FittedMember] = []
        self.nn_member_ = None

    def _target(self, train: pd.DataFrame, params: dict) -> tuple[np.ndarray, float, bool]:
        lever = self.recipe.lever
        t = train[TARGET].to_numpy(float)
        back, use_log = 0.0, bool(params.get("log"))
        if lever.get("wins"):
            q = float(lever["wins"])
            t = np.clip(t, np.quantile(t, q), np.quantile(t, 1 - q))
        if use_log:
            return np.log1p(t), 0.0, True
        if lever.get("demean"):
            back = float(t.mean())
            t = t - train.groupby("year")[TARGET].transform("mean").to_numpy()
        return t, back, False

    def fit(self, train: pd.DataFrame, verbose: bool = False) -> "PlotEnsemble":
        for name, configs in self.recipe.families.items():
            for params in configs:
                design = FittedDesign(self.schema, k=int(params.get("k", 0)),
                                      te=int(params.get("te", 0)),
                                      native_cats=NATIVE_CATS[name])
                x = design.fit_transform(train)
                t, back, use_log = self._target(train, params)
                model = build_estimator(name, params).fit(x, t)
                self.members_.append(FittedMember(name, params, design, model, back, use_log,
                                                  self.recipe.clip))
            if verbose:
                print(f"    {name:13s} {len(configs)} configuration(s) fitted")
        if self.use_nn and self.recipe.nn_params:
            from district_nn import FittedNN
            self.nn_member_ = FittedNN(self.schema, self.recipe).fit(train)
            if verbose:
                print(f"    NeuralNet     {self.recipe.nn_seeds}-seed bag fitted")
        return self

    def family_predictions(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        out: dict[str, list[np.ndarray]] = {}
        for m in self.members_:
            out.setdefault(m.name, []).append(m.predict(df))
        preds = {k: np.mean(v, axis=0) for k, v in out.items()}
        if self.nn_member_ is not None:
            preds["NeuralNet"] = self.nn_member_.predict(df)
        return preds

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        preds = self.family_predictions(df)
        return np.mean(list(preds.values()), axis=0)


# --------------------------------------------------------------------------------------
# aggregation, intervals and the full pipeline
# --------------------------------------------------------------------------------------
def aggregate_districts(df: pd.DataFrame, pred: np.ndarray, min_plots: int = 1,
                        with_actual: bool = True) -> pd.DataFrame:
    cols = {"district": df["district"].astype(str).to_numpy(), "pred_plot": pred}
    if with_actual and TARGET in df:
        cols["actual"] = df[TARGET].to_numpy()
    frame = pd.DataFrame(cols, index=df.index)
    if "year" in df:
        frame["year"] = df["year"].to_numpy()
        keys = ["year", "district"]
    else:
        keys = ["district"]
    agg = {"n_plots": ("pred_plot", "size"), "pred_mean": ("pred_plot", "mean")}
    if "actual" in frame:
        agg["actual_mean"] = ("actual", "mean")
        agg["actual_sd"] = ("actual", "std")
    out = frame.groupby(keys, observed=True).agg(**agg).reset_index()
    return out[out.n_plots >= min_plots].reset_index(drop=True)


@dataclass
class IntervalModel:
    """District-mean error as sd(n) = sqrt(a + b / n), fitted on validation seasons."""
    a: float = 0.0
    b: float = 0.0
    coverage_target: float = 0.80

    def fit(self, n_plots: np.ndarray, residuals: np.ndarray) -> "IntervalModel":
        n = np.asarray(n_plots, float)
        r2 = np.asarray(residuals, float) ** 2
        design = np.column_stack([np.ones_like(n), 1.0 / n])
        coef, *_ = np.linalg.lstsq(design, r2, rcond=None)
        self.a, self.b = float(max(coef[0], 0.0)), float(max(coef[1], 0.0))
        if self.a == 0.0 and self.b == 0.0:
            self.a = float(np.mean(r2))
        return self

    def sd(self, n_plots: np.ndarray) -> np.ndarray:
        return np.sqrt(self.a + self.b / np.asarray(n_plots, float))

    def band(self, pred: np.ndarray, n_plots: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        from scipy.stats import norm
        z = float(norm.ppf(0.5 + self.coverage_target / 2))
        s = self.sd(n_plots)
        return pred - z * s, pred + z * s


def district_metrics(actual: np.ndarray, pred: np.ndarray,
                     n_plots: np.ndarray | None = None) -> dict:
    actual, pred = np.asarray(actual, float), np.asarray(pred, float)
    out = dict(n_districts=int(len(actual)),
               r2=float(r2_score(actual, pred)),
               mae=float(mean_absolute_error(actual, pred)),
               rmse=float(np.sqrt(np.mean((actual - pred) ** 2))),
               corr=float(np.corrcoef(actual, pred)[0, 1]) if len(actual) > 2 else float("nan"),
               bias=float(np.mean(pred - actual)))
    if n_plots is not None:
        w = np.asarray(n_plots, float)
        wm = float(np.average(actual, weights=w))
        out["r2_weighted"] = float(1 - np.average((actual - pred) ** 2, weights=w) /
                                   np.average((actual - wm) ** 2, weights=w))
    return out


class DistrictPipeline:
    """Fit on training seasons, predict district-mean yield for new seasons."""

    ARTEFACTS = "pipeline.joblib"

    def __init__(self, schema: Schema, recipe: PlotRecipe, use_nn: bool = True,
                 min_plots: int = 20):
        self.schema, self.recipe, self.use_nn, self.min_plots = schema, recipe, use_nn, min_plots
        self.ensemble_: PlotEnsemble | None = None
        self.intervals_ = IntervalModel()
        self.metadata_: dict = {}

    # ---- fit ---------------------------------------------------------------------
    def fit(self, frame: pd.DataFrame, train_years: Sequence[int],
            val_years: Sequence[int] | None = None, verbose: bool = True) -> "DistrictPipeline":
        train = frame[frame.year.isin(list(train_years))]
        if verbose:
            print(f"  fitting plot ensemble on {len(train):,} plots "
                  f"({min(train_years)}-{max(train_years)})")
        self.ensemble_ = PlotEnsemble(self.schema, self.recipe, self.use_nn).fit(train, verbose)

        # interval width from rolling-origin validation seasons, never from the test season
        val_years = list(val_years) if val_years else []
        res_n, res_e = [], []
        for vy in val_years:
            prior = [y for y in train_years if y < vy]
            if len(prior) < 2:
                continue
            if verbose:
                print(f"  validation pass for {vy} (fit on {min(prior)}-{max(prior)})")
            sub = PlotEnsemble(self.schema, self.recipe, self.use_nn).fit(
                frame[frame.year.isin(prior)])
            vf = frame[frame.year == vy]
            agg = aggregate_districts(vf, sub.predict(vf), self.min_plots)
            res_n.append(agg.n_plots.to_numpy())
            res_e.append((agg.pred_mean - agg.actual_mean).to_numpy())
            self.metadata_.setdefault("validation", {})[int(vy)] = district_metrics(
                agg.actual_mean.to_numpy(), agg.pred_mean.to_numpy(), agg.n_plots.to_numpy())
        if res_n:
            self.intervals_.fit(np.concatenate(res_n), np.concatenate(res_e))
        else:                                   # no validation seasons: fall back to plot spread
            self.intervals_.a = float((train.groupby("district", observed=True)[TARGET]
                                       .mean().std()) ** 2 * 0.5)
        self.metadata_.update(train_years=list(map(int, train_years)),
                              val_years=[int(v) for v in val_years],
                              n_train_plots=int(len(train)),
                              members=sorted({m.name for m in self.ensemble_.members_} |
                                             ({"NeuralNet"} if self.ensemble_.nn_member_ else set())),
                              min_plots=int(self.min_plots),
                              interval=dict(a=self.intervals_.a, b=self.intervals_.b,
                                            coverage_target=self.intervals_.coverage_target))
        return self

    # ---- predict -----------------------------------------------------------------
    def predict_plots(self, frame: pd.DataFrame) -> np.ndarray:
        if self.ensemble_ is None:
            raise RuntimeError("pipeline is not fitted")
        return self.ensemble_.predict(frame)

    def predict_districts(self, frame: pd.DataFrame, min_plots: int | None = None) -> pd.DataFrame:
        pred = self.predict_plots(frame)
        out = aggregate_districts(frame, pred, min_plots or self.min_plots,
                                  with_actual=TARGET in frame)
        lo, hi = self.intervals_.band(out.pred_mean.to_numpy(), out.n_plots.to_numpy())
        out["pred_lo"] = np.maximum(lo, self.recipe.clip[0])
        out["pred_hi"] = np.minimum(hi, self.recipe.clip[1])
        out["pred_sd"] = self.intervals_.sd(out.n_plots.to_numpy())
        if "actual_mean" in out:
            out["error"] = out.pred_mean - out.actual_mean
            out["inside_band"] = (out.actual_mean >= out.pred_lo) & (out.actual_mean <= out.pred_hi)
        return out.sort_values("pred_mean", ascending=False).reset_index(drop=True)

    # ---- persistence -------------------------------------------------------------
    def save(self, directory: Path | str) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, d / self.ARTEFACTS, compress=3)
        (d / "metadata.json").write_text(json.dumps(self.metadata_, indent=2), encoding="utf-8")
        return d / self.ARTEFACTS

    @staticmethod
    def load(directory: Path | str) -> "DistrictPipeline":
        d = Path(directory)
        path = d / DistrictPipeline.ARTEFACTS if d.is_dir() else d
        return joblib.load(path)
