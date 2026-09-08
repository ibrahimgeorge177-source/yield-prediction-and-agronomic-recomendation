# Prediction & recommendation API — reference

**Kenya maize district yield model and agronomic recommendation layer, served
over HTTP.**

The service wraps the district-level model described in
[kenya_maize_district_model_deployment.md](kenya_maize_district_model_deployment.md).
That document is the authority on what the model can and cannot do, and
[kenya_maize_recommendation_layer.md](kenya_maize_recommendation_layer.md) is
the authority on how the advice is estimated; this one covers the HTTP
contract.

---

## 1 · Running it

```bash
pip install -r requirements.txt
python scripts/build_api_defaults.py        # once, and after any feature rebuild
python scripts/build_lever_curves.py        # once, and after any feature rebuild
uvicorn api.main:app --reload
```

Interactive docs at `/docs`, the OpenAPI schema at `/openapi.json`.

### The model artefact

`pipeline.joblib` is 57–125 MB and **is not in git** — it exceeds GitHub's file
limit, so `.gitignore` excludes it. The service boots without it: `/health`,
`/api/v1/model/summary` and the reference endpoints keep working while the
prediction endpoints return `503` with an actionable message. Supply it one of
three ways:

| How | Set |
|---|---|
| Artefact already on disk | `MODEL_DIR=/path/to/district_v2` |
| Download on first use | `MODEL_URL=https://…/district_v2.zip` (also accepts `.tar.gz` or a bare `pipeline.joblib`) |
| Build it | `python scripts/train_district_model.py --train 2016-2020 --val 2019,2020 --test none --out data/models/district_v2` |

Add `--no-nn` when training to drop the neural member: no torch dependency,
roughly half the artefact size, and about 0.02 R² on the 2020 season.

### Environment

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8000` | Port to bind. |
| `MODEL_DIR` | `data/models/district_v2` | Artefact directory. |
| `MODEL_URL` | — | Archive to download when `MODEL_DIR` is empty. |
| `MODEL_VERSION` | the directory name | Reported in every response. |
| `MODEL_EAGER_LOAD` | `false` | Load the weights during startup rather than on the first request. |
| `FEATURE_DEFAULTS_PATH` | `data/api/feature_defaults.json` | Fill values. |
| `MAX_PLOTS_PER_REQUEST` | `500` | Request size cap. |
| `CORS_ORIGINS` | `*` | Comma-separated allowed origins. |
| `LOG_LEVEL` | `INFO` | Python log level. |

**Pin `pandas<3`.** The pipeline resolves its categorical columns with
`dtype == object` (`scripts/district_model.py`, `load_schema`). On pandas 3 text
columns arrive as `str`, that test matches nothing, and target encoding silently
degrades. `requirements.txt` pins it.

---

## 2 · `POST /api/v1/predict`

Scores plots and the district means they roll up into.

### Request

Only `district` is required. Every other field is optional, and anything omitted
is filled from that district's own history rather than rejected — so a sparse
request still scores. `GET /api/v1/reference/input-schema` returns every field
with its allowed values and observed range.

```json
{
  "plots": [
    {
      "plot_id": "farm-104",
      "district": "bungoma",
      "year": 2020,
      "plot_acres": 1.5,
      "seed_category": "hybrid_branded",
      "dap_kg_ph": 60,
      "can_kg_ph": 50,
      "plant_date": "2020-03-15",
      "cows": 2,
      "owns_electricity": true
    }
  ],
  "include_plot_predictions": true
}
```

| Field | Type | Notes |
|---|---|---|
| `district` | string, **required** | Case-insensitive. Unknown districts are scored and flagged, not rejected. |
| `year` | integer | Defaults to the latest training season. |
| `plot_acres` | number | |
| `seed_category` | enum | `hybrid_branded`, `other_hybrid`, `local`, `mixed`. |
| `seed_type` | string | Exact variety; falls back with a warning if outside the vocabulary. |
| `hybridseed_kg_ph`, `localseed_kg_ph` | number | kg/ha. |
| `dap_kg_ph`, `urea_kg_ph`, `can_kg_ph`, `npk_kg_ph`, `lime_kg_ph` | number | kg/ha. |
| `compost_wheelbarrows_per_acre` | number | As surveyed. |
| `plant_date` / `plant_date_doy` | date / number | Only the day of year is used. |
| `intercrop`, `intercrop_type` | boolean, string | Legumes are treated distinctly. |
| `hh_num`, `hh_num_under18`, `cows`, `owns_oxen`, `owns_electricity` | | Household context. |

Request-level options: `year` (a default season for plots without one),
`include_plot_predictions` (default `true`), `min_plots` (override the
reporting threshold).

### What happens to the request

The model consumes **143 columns** — the 66 engineered features plus the 89
weather columns feeding its PCA basis. Each plot is expanded to all of them:

1. **Anything derivable from your input is derived**, using the formulas in
   `scripts/build_features.py`. A `dap_kg_ph` of 60 sets the winsorized
   companion, `n_kg_ph`, `p2o5_kg_ph`, `total_nutrient_kg_ph`, the outlier flag
   and the missingness flag — the same columns it moved during training.
2. **Everything else is filled from that district's history**: observed weather
   for the district-season when available, otherwise its climatology, then the
   district's own medians, then national medians.

Values above the winsorization ceilings applied during cleaning (500 kg/ha for
fertiliser, for instance) are clipped and warned about, never rejected — the raw
survey columns legitimately carry entry errors up to 45,922 kg/ha.

`inputs_supplied` on each result lists exactly which model columns your request
determined, so a caller can see how much of the prediction is theirs.

### Response

```json
{
  "model_version": "district_v2",
  "unit": "kg/ha",
  "n_plots_scored": 1,
  "districts": [
    {
      "district": "bungoma", "year": 2020, "n_plots": 1,
      "predicted_mean_yield_kg_ph": 2802.7,
      "interval_low_kg_ph": 1604.8, "interval_high_kg_ph": 4000.6,
      "interval_sd_kg_ph": 934.6, "interval_coverage": 0.8,
      "below_reporting_threshold": true, "district_known": true
    }
  ],
  "plots": [
    {
      "plot_id": "farm-104", "index": 0, "district": "bungoma",
      "district_known": true, "year": 2020,
      "predicted_yield_kg_ph": 2802.7,
      "inputs_supplied": ["plot_acres", "plot_hectares", "dap_kg_ph", "..."],
      "inputs_supplied_count": 25,
      "used_season_weather": true,
      "warnings": []
    }
  ],
  "warnings": ["1 district mean(s) rest on fewer plots than the model's reporting threshold …"],
  "accuracy_note": "District means are the unit this model was validated on …"
}
```

Read the flags. `below_reporting_threshold` means fewer plots back that mean
than the model's threshold (20), which makes it largely sampling noise.
`district_known: false` means the model never saw that district in training —
measured level error for such districts is roughly twice as large.
`used_season_weather: false` means climatological averages stood in for observed
weather.

### How good are these numbers

District means are the validated unit: **out-of-time R² 0.22–0.59, MAE 300–700
kg/ha**, depending far more on the season than on the model. Per-plot estimates
are returned because a per-farm UI needs something to show, but they are much
weaker (**R² ≈ 0.16**) and deliberately shrunk toward the mean — most
plot-to-plot variance comes from soil and management detail the survey never
captured. Present them as indicative, not as a farm forecast.

**The lean request path costs almost nothing.** Scoring all 6,190 real 2020
plots through the API and comparing against the pipeline given the true
engineered rows: plot-level correlation 0.988 (mean absolute difference 68
kg/ha), district-level correlation 0.998 (34 kg/ha), and identical accuracy
against observed district means. Filling from district history rather than
demanding 143 columns does not meaningfully degrade the prediction.

### Errors

| Status | When |
|---|---|
| `422` | Malformed request: no plots, missing `district`, negative rate, unknown field, over the size cap. |
| `503` | Model artefact or feature defaults unavailable. The message says how to fix it. |
| `500` | Prediction failed unexpectedly; the exception type is in the message. |

---

## 3 · `POST /api/v1/recommend`

Ranks the changes available to one plot, each with an expected yield lift, an
interval and the evidence behind it. This is the field-agent surface of
CRISP-DM report §6.1.

**Everything is in kg/ha.** There is no costing and no budget. Report §1.4
records that this workbook carries no fertiliser or farm-gate price data and
that Project 1's recommendation layer must stay in yield units rather than
depending on price assumptions nobody supplied. Turning a lift into money is
Project 3's scope.

**It does not need the model artefact.** Lifts come from the fitted lever
curves in `data/api/lever_curves.json`, which are committed. A deployment whose
`pipeline.joblib` never arrived still gives advice; only the optional
`baseline_predicted_yield_kg_ph` is omitted, with a warning.

### Request

```json
{
  "plot": { "district": "bungoma", "plant_date_doy": 110,
            "seed_category": "local", "dap_kg_ph": 0, "can_kg_ph": 0,
            "plot_acres": 1.0 },
  "year": 2020
}
```

`plot` is exactly the `PlotInput` of `/predict`, so a frontend uses one form for
both. Everything else is optional:

| Field | Effect |
|---|---|
| `levers` | Restrict to named levers. `GET /api/v1/reference/levers` lists them. |
| `min_lift_kg_ph` | Ignore changes worth less than this (default 25). |
| `min_support` | Refuse any target with fewer comparable plots behind it (default 200). |
| `include_curve` | Return each lever's whole fitted response curve, for plotting. |

The request model forbids unknown fields, so a stray `budget`, `prices` or
`objective` is a `422` rather than a silently ignored key.

### Response

```json
{
  "district": "bungoma", "district_known": true, "year": 2020,
  "recommendations": [
    {
      "rank": 1, "lever": "hybrid_seed", "label": "Share of seed that is hybrid",
      "action": "plant a larger share of hybrid seed — 0.95 share of seed (0-1) (now 0)",
      "current_value": 0.0, "recommended_value": 0.95,
      "current_is_assumed": false,
      "expected_lift_kg_ph": 586.4,
      "lift_low_kg_ph": 545.8, "lift_high_kg_ph": 627.0,
      "lift_share_of_district_yield": 0.197,
      "evidence": {
        "n_plots": 22809, "n_districts": 51, "support_at_target": 17481,
        "confidence": "high", "t_statistic": 23.75,
        "uncontrolled_lift_kg_ph": 829.6, "control_absorbed_share": 0.293,
        "trained_to_2019_lift_kg_ph": 591.2,
        "curve_shape": "concave_increasing", "agronomically_plausible": true
      }
    }
  ],
  "bundle": { "levers": ["hybrid_seed", "..."], "total_expected_lift_kg_ph": 2447.4,
              "lift_low_kg_ph": 1806.3, "lift_high_kg_ph": 3088.5,
              "lift_share_of_district_yield": 0.821,
              "district_mean_yield_kg_ph": 2982.6, "note": "..." },
  "skipped": [{ "lever": "intercropping", "reason": "no change to this lever clears the evidence threshold..." }],
  "warnings": ["..."], "method_note": "...", "causal_note": "..."
}
```

Four fields deserve attention because they are what separate this from a
confident-looking number:

- **`current_is_assumed`** — `true` when the request did not say what the plot
  currently does, so the district's median practice stood in. The lift then
  describes a typical plot in that district, not this one.
- **`support_at_target`** — plots observed near the recommended value. Thin
  support is the main way a fitted optimum misleads: only 4% of plots apply any
  lime, so its fitted optimum sits in a region holding ~100 of them.
- **`uncontrolled_lift_kg_ph`** — the same contrast fitted with no covariates.
  The gap to the headline is how much of the raw association is *who chooses the
  input* rather than the input. It runs 29–50% across levers.
- **`trained_to_2019_lift_kg_ph`** — the same contrast refit on 2016–2019 only.

`skipped` is not an error list. "No change to this lever clears the evidence
threshold" for a plot already at its district's fitted optimum is the correct
answer, and an empty `recommendations` array is a legitimate response.

### Why the lift is not read off the yield model

Report §4.1 proposes a constrained search over the trained GBM's predicted
surface. That is the wrong instrument here and the notebooks say why: per-plot
out-of-time R² is ~0.16 and `district` alone accounts for ~60% of the model's
holdout performance, so optimising over that surface returns advice with a
precision the evidence cannot carry.

The estimand advice needs is different from the one the model targets — not
*what will this plot yield* but *how does yield move when this decision moves* —
and that average gradient is estimable from ~21,000 plots even where per-plot
prediction is weak. Notebook 03 §6 demonstrated it on planting date; the
recommendation layer generalises the same fixed-effects method to every ex-ante
lever. Full method, fitted curves and limitations:
[kenya_maize_recommendation_layer.md](kenya_maize_recommendation_layer.md).

### Errors

| Status | When |
|---|---|
| `422` | Malformed plot, or an unknown lever name (the message lists the valid ones). An unknown *district* is not an error — it is advised, with a warning. |
| `503` | `feature_defaults.json` or `lever_curves.json` is missing. Build them with the two scripts in §1. |

---

## 4 · `GET /api/v1/model/summary`

What the deployed model is and how well it does. Everything quantitative comes
from the artefact's `metadata.json`, so a retrained model reports its own
figures rather than numbers frozen in code.

Works whether or not the weights are loaded — the metadata is committed beside
the artefact — so a deployment still waiting on its model file can report what
it is meant to be serving. `status` is `ready` (weights loaded), `not_loaded`
(metadata only) or `unavailable` (neither).

Returns: `train_seasons`, `validation_seasons`, `n_training_plots`,
`ensemble_members`, `min_plots_for_reporting`, per-season `validation` metrics
(R², weighted R², MAE, RMSE, correlation, bias), a `headline` block, the
`interval` parameters, an `expected_performance` block including accuracy by
district size and why the R² ≥ 0.70 target is unreachable with this data,
`limitations` (the six stated limits from the deployment guide), and `artefact`
/ `feature_defaults` diagnostics.

---

## 5 · Reference and health

| Endpoint | Returns |
|---|---|
| `GET /api/v1/reference/districts` | The 51 districts the model knows, and the seasons available. |
| `GET /api/v1/reference/seed-types` | Seed categories, varieties, primary varieties, intercrop species. |
| `GET /api/v1/reference/input-schema` | Every accepted field with type, unit, allowed values and observed range — enough to build and validate a form. |
| `GET /api/v1/reference/levers` | The eight controllable levers `/recommend` can advise on, each with its fitted curve shape and whether it matches its agronomic prior. |
| `GET /health` | Liveness. Answers `ok`/`degraded` even with no model. Use for platform health checks. |
| `GET /ready` | Readiness: `200` only when defaults are present and the model loads. Use to gate traffic. |
| `GET /` | Service index. |

---

## 6 · Regenerating the artefacts

`data/api/feature_defaults.json` holds per-district and per-district-season
medians and modes for all 143 model columns, the category vocabularies, the
observed ranges and the winsorization ceilings.

```bash
python scripts/build_api_defaults.py
```

Rebuild it whenever the feature file or the recommended-feature list changes.
It needs only pandas and numpy — not the model stack — and stores no
target-derived quantity, so it carries no leakage.

`data/api/lever_curves.json` (~38 KB) holds the fitted response curve for each
lever: its spline basis, coefficients, district-clustered covariance, the
support behind each proposable value, and the uncontrolled and 2016–2019 refits
used as diagnostics.

```bash
python scripts/build_lever_curves.py
```

Also pandas and numpy only. Unlike the fill values this artefact **is**
target-derived — it is a set of regression coefficients — so it must be refit,
not carried forward, whenever the feature file changes.

---

## 7 · Tests

```bash
pytest tests/                                         # model-dependent tests skip
MODEL_DIR=data/models/district_v2 pytest tests/       # full suite
```

`tests/test_api.py` covers prediction; `tests/test_recommend.py` covers the
recommendation layer and needs no artefact at all, since the curves are
committed.
