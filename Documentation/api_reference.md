# Prediction API — reference

**Kenya maize district yield model, served over HTTP.**

The service wraps the district-level model described in
[kenya_maize_district_model_deployment.md](kenya_maize_district_model_deployment.md).
That document is the authority on what the model can and cannot do; this one
covers the HTTP contract.

---

## 1 · Running it

```bash
pip install -r requirements.txt
python scripts/build_api_defaults.py        # once, and after any feature rebuild
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

## 3 · `GET /api/v1/model/summary`

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

## 4 · Reference and health

| Endpoint | Returns |
|---|---|
| `GET /api/v1/reference/districts` | The 51 districts the model knows, and the seasons available. |
| `GET /api/v1/reference/seed-types` | Seed categories, varieties, primary varieties, intercrop species. |
| `GET /api/v1/reference/input-schema` | Every accepted field with type, unit, allowed values and observed range — enough to build and validate a form. |
| `GET /health` | Liveness. Answers `ok`/`degraded` even with no model. Use for platform health checks. |
| `GET /ready` | Readiness: `200` only when defaults are present and the model loads. Use to gate traffic. |
| `GET /` | Service index. |

---

## 5 · Regenerating the fill values

`data/api/feature_defaults.json` holds per-district and per-district-season
medians and modes for all 143 model columns, the category vocabularies, the
observed ranges and the winsorization ceilings.

```bash
python scripts/build_api_defaults.py
```

Rebuild it whenever the feature file or the recommended-feature list changes.
It needs only pandas and numpy — not the model stack — and stores no
target-derived quantity, so it carries no leakage.

---

## 6 · Tests

```bash
pytest tests/test_api.py                              # model-dependent tests skip
MODEL_DIR=data/models/district_v2 pytest tests/test_api.py   # full suite
```
