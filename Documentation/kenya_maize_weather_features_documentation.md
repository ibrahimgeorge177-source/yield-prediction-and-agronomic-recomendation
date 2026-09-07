# Kenya Maize Data: Environmental Feature Extension — Documentation

**Input:** `data/cleaned/kenya_maize_cleaned.csv` (23,674 × 120) — the output of the cleaning stage
**Scope:** Kenya, maize, long rains only. Extends CRISP-DM §3.3 *Feature Construction* with external environmental covariates
**Pipeline:** three stages — `scripts/weather_locations.py` → `scripts/fetch_gee_weather.py` → `scripts/build_weather_features.py`, merged by `scripts/build_features.py`. Run end to end with `scripts/run_weather_pipeline.py` (§8)
**Sources:** [CHIRPS Daily](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY) rainfall and [ERA5-Land Daily Aggregated](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR) temperature, both via the Google Earth Engine Python API
**Outputs:** `data/weather/kenya_maize_weather_features.csv` (23,674 × 95), plus the intermediate panel, climatology and audit JSONs beside it
**Status:** extracted 2026-09-07 — 68,472 cell-months, zero nulls, all validations passed (§9)

---

## 1. What this stage adds, and the one rule it inherits

The survey records what the farmer did — seed, fertiliser, spacing, planting date. It records almost nothing about the season the crop actually grew in. `drought` and `flood` are self-reported binaries, present in only some years; there is no rainfall figure anywhere in the 120 columns.

That is a real gap for this project's purpose. Two of the four consumer surfaces in the CRISP-DM report — loan sizing before planting, and the mid-season re-forecast — are asking a question about weather whether or not the model has any weather in it. Without it, every season-to-season yield difference has to be absorbed by the year effect, which is exactly the quantity the out-of-time holdout refuses to let the model learn.

This stage inherits the governing rule of `build_features.py` unchanged:

> **Never emit a feature that cannot mean the same thing in the training years and in the holdout year.**

Weather satisfies that rule *trivially and misleadingly*, which is the central problem of this stage and the subject of §5.

---

## 2. Why Earth Engine, and why the work happens server-side

The requested approach — Method 1, the Earth Engine Python API — is the right one, and the reason is arithmetic rather than preference.

CHIRPS is a global daily rainfall grid running from 1981 to the present. The portion this project needs is 23,674 farms × 5 seasons × ~365 days. Downloading that and aggregating locally is roughly 4.2 million values across two datasets, which Earth Engine will refuse long before it finishes.

So two reductions happen before anything crosses the network, and together they are the difference between a script that runs in minutes and one that does not run at all:

**Reduction 1 — collapse the farms onto the raster grid (stage 1).** CHIRPS pixels are 0.05° ≈ 5.5 km. The survey's 22,389 distinct field coordinates fall inside just **951 CHIRPS cells**. Querying per farm would be a 23× waste asking the same pixel the same question over and over.

**Reduction 2 — aggregate inside Earth Engine, not in pandas (stage 2).** Every monthly sum, mean and degree-day accumulation is expressed as an Earth Engine computation. Only the 951-row monthly result is transferred. The whole extraction is ~672 requests and returns a 68,472-row panel.

ERA5-Land is coarser (0.1° ≈ 11 km), so several CHIRPS cells share one ERA5-Land pixel. Sampling both at the finer grid's cell centres loses nothing — it is the physically correct outcome, not an approximation.

---

## 3. Stage 1 — locating a farm (`weather_locations.py`)

A weather value is only as good as the coordinate it was read at, so the fallback ladder is explicit and every row records which rung it used.

| Rung | Source | Rows | Share |
|---|---|---:|---:|
| `field` | the farmer's own plot GPS fix | 23,159 | **97.82%** |
| `site` | enumeration site centroid (~village) | 116 | 0.49% |
| `district_centroid` | median of that district's valid field fixes | 399 | 1.69% |
| — | unlocatable | **0** | 0% |

**Every row in the dataset gets weather.** The 399 rows with no GPS of their own (371 of them in 2020) all sit in districts that have field fixes elsewhere, so the centroid fallback reaches all of them.

Four decisions worth recording:

- **Validity is checked on the coordinate *pair*, not per column.** A row with a latitude and no longitude is not half-locatable, it is unlocatable, and a column-wise `notna()` will not tell you that. The pair must also fall inside a padded Kenya bounding box and contain no exact zeros — the classic "GPS never acquired a fix" sentinel.
- **District centroids use the survey's own fixes, and the median.** No external shapefile is needed, and the centroid lands where these farmers actually are rather than at the geometric centre of an administrative polygon that may be half national park. The median rather than the mean because a single surviving coordinate typo would drag a mean across the country.
- **Snapping is to the cell *centre*, not the corner.** Sampling a raster at a pixel corner is a coin flip between four pixels that floating-point noise decides. Median snap displacement is **2.22 km**, bounded by half a cell diagonal.
- **`weather_location_source` travels downstream** as `weather_location_is_field`, so a model can condition on — or exclude — rows whose rainfall was read from a district centroid rather than from the farm.

### 3.1 The season-year assumption, checked rather than assumed

Every row here is the **long rains**, which open and close inside one calendar year: 92% of dated plantings fall in February–April, 93% of dated harvests in June–October. That is what lets weather be keyed on `year` directly.

Because the whole stage rests on it, it is asserted rather than trusted: where a `plant_date` exists, its calendar year must match `year`. It does on **20,878 of 20,934 dated rows (99.7%)**. The 56 exceptions are all December plantings recorded against the following season — a real if unusual early planting, not a data error — and they keep `year` as their season, since that is the season whose harvest was surveyed.

The pipeline fails loudly if that agreement ever drops below 99%, because the most likely cause would be a short rains season entering the file, at which point one calendar year no longer contains one season and every feature in this document silently means something else.

---

## 4. Stage 2 — the extraction (`fetch_gee_weather.py`)

| | Rainfall | Temperature |
|---|---|---|
| Collection | `UCSB-CHG/CHIRPS/DAILY` | `ECMWF/ERA5_LAND/DAILY_AGGR` |
| Resolution | 0.05° (5,566 m) | 0.1° (11,132 m) |
| Bands used | `precipitation` (mm/day) | `temperature_2m`, `_min`, `_max` (K) |
| Monthly reduction | sum; count of days ≥ 1 mm | means, converted to °C; capped GDD sum |

**Years fetched:** 2015–2020. 2015 is there for one reason — the 2016 season's pre-planting soil moisture comes from November and December 2015.

**Months fetched:** all twelve, though only January–October become row features. Two extra requests per year buys the freedom to change the season window later, or to look at the short rains, without a second trip to Earth Engine.

Three choices inside the aggregation:

- **Growing degree days are *capped*, base 10 °C, ceiling 30 °C.** Both ends of the daily range are clamped into `[10, 30]` before averaging. The uncapped form credits a 38 °C day as better for the crop than a 29 °C one, which is exactly backwards during flowering.
- **`tmax_c` is the monthly mean of the *daily* maxima**, not the single hottest day. A monthly extremum is one observation and mostly noise.
- **A "rain day" is ≥ 1 mm**, the WMO convention. Counting 0.2 mm traces would make a badly distributed month look well watered.

**Climatological normals** come from **1991–2020**, the WMO standard 30-year period. It ends in 2020 so the baseline is not largely defined by the five seasons whose anomalies are being computed against it — the survey years contribute 5 of 30 years rather than dominating.

### 4.1 Operational design

- **Resumable.** Earth Engine is a shared cluster; requests fail with transient timeouts. Each `(year, month)` result is cached to `data/weather/_gee_cache/` the moment it lands, so a re-run resumes rather than restarting. Delete that directory to force a clean refetch. It is gitignored.
- **Chunked and retried.** 300 points per request with five attempts and exponential backoff.
- **Masked cells are buffer-filled, and only masked cells.** ERA5-Land is masked over open water, so cells on the Lake Victoria shoreline can return nothing at the point; those are re-sampled as the mean over a 20 km buffer. Buffering everything would smooth away the real spatial variation the stage exists to capture.
- **Validated on physical ranges.** The checks target the failure modes that actually occur — a unit slip, a forgotten Kelvin conversion, an empty date filter summing silently to zero — not the climate. A month that is identically dry across every cell is flagged, because western Kenya has no such month and that pattern means the date filter matched no images.

---

## 5. The honest problem: weather features are substantially year labels

This is the finding that shaped the rest of the stage, and it is the same failure `build_features.py` built four gates to catch, arriving through a door those gates do not watch.

The survey is packed into a few hundred kilometres of western Kenya. Regional rainfall moves largely in unison, so **between-season variation is large next to between-farm variation within a season**. Hand a gradient-boosted tree `rain_mm_apr` and it can read the season off it, then reproduce that season's mean yield. That scores beautifully in grouped cross-validation and forecasts nothing.

**Gates A–D cannot catch this.** Every weather column is 100% present in all five years by construction and has real variance in the holdout, so all 89 pass every existing screen. The gates test *coverage*; this is a problem of *values*.

So the stage does two things about it rather than one.

### 5.1 It measures the problem — `year_variance_share`

A new column in `kenya_maize_feature_manifest.csv`, computed for every feature in the project, not just the weather ones: the share of a feature's total variance lying **between** survey years rather than within them (a one-way ANOVA η² with year as the factor). `0.0` means the feature says nothing about which year a row came from; `1.0` means it says nothing else.

Nothing is dropped on it. A genuinely year-varying quantity like season rainfall *is* mostly a year effect, and that is the truth about rainfall rather than a defect in the column. The number is reported so the modelling package weighs it deliberately against the out-of-time holdout, with a measurement instead of an intuition.

**It immediately found three pre-existing features that Gate D missed** — all `keep`, none weather:

| Feature | `year_variance_share` |
|---|---:|
| `slope_angle_num_is_missing` | 0.99 |
| `pest_disease_is_missing` | 0.98 |
| `fertility_is_missing` | 0.92 |

These are the missingness flags for columns absent in 2016. Gate D only drops columns that are *constant across the whole holdout*; these vary slightly in 2020 and so survive it, while still being near-perfect year detectors. They are flagged rather than dropped, consistent with §9 of the feature-engineering documentation, which mandates keeping missingness indicators — but the modelling package should now know what it is holding.

### 5.2 It emits anomalies alongside levels

An anomaly asks *"wetter or drier than this specific place normally is"*, which is comparable across cells and strips out the fixed geography a level smuggles in. Three tiers of weather feature result, and they behave very differently:

- **Normals** (`rain_mm_season_normal`, `tmean_c_season_normal`, …) are pure geography — constant across years by construction, so they *cannot* be a year label, and they are the only weather features that are legitimately `ex_ante`.
- **Levels** (`rain_mm_apr`, `rain_mm_season`, …) carry both the season and the place.
- **Anomalies** (`rain_anom_pct_season`, `tmean_anom_c_season`, …) remove the fixed geography but keep the genuine common shock — which is real signal and a year label at the same time.

---

## 6. What gets built — 89 features

### 6.1 Calendar months, January–October (60 columns) — the requested feature

`rain_mm_{mon}`, `rain_days_{mon}`, `tmean_c_{mon}`, `tmax_c_{mon}`, `tmin_c_{mon}`, `gdd10_{mon}` for each of `jan`–`oct`. Coverage **100%**.

October is the cutoff: the long rains harvest is over by then, and November onward belongs to the *next* season's pre-season block.

### 6.2 Pre-season (2 columns)

`rain_mm_preseason`, `rain_days_preseason` — the previous November–December short rains that fill the soil profile before anyone plants, read from `season_year - 1`. Coverage **100%**.

This is the only rainfall in the file a lender could actually have seen before disbursing, which is why it is tiered `ex_ante` while every in-season month is not.

### 6.3 Fixed season window, March–August (6 columns)

`rain_mm_season`, `rain_days_season`, `gdd10_season`, `tmean_c_season`, `tmax_c_season`, `rain_mm_driest_season_month`. Coverage **100%**.

Sums for what accumulates, means for what does not — summing a temperature produces a number that rises with window length and means nothing.

`rain_mm_driest_season_month` exists because a season can hit a perfectly normal total and still fail if one month of it was empty; for maize, that month landing on flowering is the difference between a crop and no crop. A total cannot express that.

### 6.4 Plant-date-relative window (11 columns)

`rain_mm_p0`–`p3` and `gdd10_p0`–`p3` (month of planting, then the three following), their sums, and `rain_mm_plant_to_harvest`. Coverage **88.4%** (the share of rows with a `plant_date`); 71.8% for `rain_mm_plant_to_harvest`, which also needs a harvest date.

Both windows are emitted because they answer different questions. The fixed window is defined for all 23,674 rows and is comparable across them. The relative window tracks the crop instead of the calendar — April rain means *establishment* to a March planter and *flowering* to a February one — and `p2`, the flowering and silking month, is the yield-critical one for maize.

Indexed on an **absolute** month counter (`year × 12 + month`), so a December planting reads forward into the following January without special-casing, and a window running past the fetched panel returns null rather than silently wrapping to the wrong year — which is what a naive modulo-12 implementation does, undetectably.

`rain_mm_plant_to_harvest` is **month-resolution**: it sums whole calendar months from the planting month through the harvest month, so it over-counts by up to two part-months. Kept because that error is small against a ~700 mm season and the alternative is the 23×-larger per-row daily extraction that stage 1 exists to avoid.

### 6.5 Normals and anomalies (10 columns)

Five 1991–2020 normals and five anomalies against them, seasonal and pre-season, in millimetres, percent, °C and degree days. Coverage **100%**.

---

## 7. How the merge lands in `build_features.py`

Merged **before** `drop_unusable_columns`, for a mundane but load-bearing reason: the join key is `unique_id`, which that stage drops as a memorization key.

**Optional by design.** The weather stages need Google credentials and a network round trip; nothing else in the project does. If `kenya_maize_weather_features.csv` is absent, the pipeline logs it and continues, and every output is byte-identical to the pre-weather output. This has been verified, and it is what lets a contributor without an Earth Engine account reproduce the rest of the project.

**`weather_lat` / `weather_lon` are deliberately not merged.** They are the same five-decimal household fingerprint that `ROLE_OVERRIDES` already quarantines `field_latitude` and `field_longitude` for, and re-admitting them under a new name walks straight back into the out-of-time collapse documented there.

### 7.1 Roles and tiers

| | Assignment | Reasoning |
|---|---|---|
| **Role** `shock` | anomaly features | A realized deviation from what a place normally gets is the same kind of thing as `drought` and `flood`, which is what `shock` already means here |
| **Role** `condition` | levels and normals | They describe the environment a farm sits in |
| **Role** `flag` | `weather_location_is_field` | Data-quality indicator |
| **Tier** `ex_ante` | normals, pre-season, January and February | Knowable before the modal March planting |
| **Tier** `mid_season` | everything else | |

The tier rule is deliberately conservative. March rain is already in the ground for someone planting in April, but a tier is a property of the **column**, not the row, and the column has to be safe for the earliest planter in it.

### 7.2 Effect on the outputs

| | Without weather | With weather |
|---|---:|---:|
| `kenya_maize_features_full.csv` | 23,674 × 69 | 23,674 × 158 |
| `kenya_maize_features_core.csv` | 19,798 × 80 | 19,798 × 170 |
| Manifest rows | 136 | 226 |
| Tiers | 119 `ex_ante`, 13 `mid_season` | 140 `ex_ante`, 82 `mid_season` |

All 89 weather features clear Gates A–D — which, as §5 argues, is the point rather than the reassurance.

---

## 8. Reproducing it

One-time setup:

```bash
pip install earthengine-api
```

Then the whole chain is one command:

```bash
python scripts/run_weather_pipeline.py --check   # verify credentials, ~2s
python scripts/run_weather_pipeline.py           # stages 1-4
```

### 8.1 Authentication

Two modes, selected by whether `EE_SERVICE_ACCOUNT_KEY` is set in `.env`.

**Service account (this project's default).** Preferred here because the fetch is long, unattended and re-runnable: an OAuth token from `earthengine authenticate` expires and needs a browser to renew, while a service account just works. The key is stored at `~/.config/earthengine/ontrack-356205-service-account.json` — **outside the repository** — and `.env` holds only the path to it.

**Interactive.** Blank out `EE_SERVICE_ACCOUNT_KEY` and run `earthengine authenticate`, which writes user credentials to the same directory. Anyone without the service-account key uses this.

### 8.2 Configuration, and what is and is not committed

`.env` at the repository root is **committed**, and holds only a project ID and a file path — neither is a credential. That is what makes the pipeline runnable by a teammate without a setup conversation. It is read by a ten-line parser in `fetch_gee_weather.py` rather than a `python-dotenv` dependency, since every other script in this project runs on numpy and pandas alone.

Precedence, highest first: a real shell environment variable, then `.env.local` (gitignored, per-developer), then `.env`.

**Private keys are never stored in `.env`, and two mechanisms enforce it rather than merely asking:**

1. `fetch_gee_weather.py` **refuses to start** if `EE_SERVICE_ACCOUNT_KEY` resolves to a path inside the repository, because such a key is one `git add .` from being published. It exits with the correct location rather than warning — a warning scrolls past.
2. `.gitignore` blocks `*-service-account*.json`, `*service_account*.json`, `*credentials*.json`, `*.pem`, `*.key` and `secrets/`. Nothing should ever match these, given the key lives outside the tree; they exist so that if someone drops a key into the working tree under deadline, git refuses to stage it.

A service-account key is a **bearer credential**: anyone holding the file can act as that account with no password and no second factor. It should never be pasted into a chat, an issue, a commit, or a shared document, and one that has been should be deleted in the Cloud console and reissued.

Runner flags:

| Flag | Effect |
|---|---|
| `--check` | verify credentials and project registration, then stop |
| `--refetch` | discard the resume cache and re-pull from Earth Engine |
| `--skip-features` | stop after stage 3, leaving the feature matrix alone |
| `--project ID` | override `.env` for one run |

**Stage 2 is skipped when its output already exists.** It is the only stage that costs anything — a few hundred requests against a monthly compute quota — and its output is deterministic, so re-running after editing a stage-3 feature definition should not re-pull rainfall that has not changed since 1981. `--refetch` forces it.

The stages can still be run individually, and stages 1, 3 and 4 need no credentials and no network:

```bash
python scripts/weather_locations.py            # offline, ~5s
python scripts/fetch_gee_weather.py            # the only networked stage; --dry-run prints the plan
python scripts/build_weather_features.py       # offline
python scripts/build_features.py               # picks the weather up automatically
```

If you need a project of your own: register at [console.cloud.google.com/earth-engine](https://console.cloud.google.com/earth-engine), choose noncommercial (free, no billing required), and use the generated **project ID** — not the display name.

---

## 9. What the extraction actually returned

Run on 2026-09-07: 951 cells × 6 years × 12 months = 68,472 cell-months, plus a 12-month climatology, in **581 s with zero retries and zero nulls**. All physical-range and degeneracy validations passed.

| | min | mean | max |
|---|---:|---:|---:|
| `rain_mm` (monthly) | 0.0 | 146.7 | 808.2 |
| `rain_days` | 0 | 11.8 | 30 |
| `tmean_c` | 11.30 | 19.46 | 26.50 |
| `tmax_c` | 15.36 | 24.64 | 33.51 |
| `tmin_c` | 7.07 | 14.91 | 24.41 |

Long rains (March–August) by season, averaged over survey rows:

| Season | Rainfall (mm) | vs 1991–2020 normal | Mean temp (°C) | Temp anomaly |
|---|---:|---:|---:|---:|
| 2016 | 1,045 | −1.9% | 19.9 | +0.6 |
| 2017 | 1,087 | +4.8% | 20.1 | +0.8 |
| 2018 | 1,310 | +29.0% | 18.3 | −0.6 |
| 2019 | 992 | −3.1% | 19.9 | +0.8 |
| 2020 | 1,396 | +40.9% | 19.2 | −0.0 |

### 9.1 The headline correlation is elevation, not weather

`tmin_c_sep` correlates **−0.331** with yield — the strongest single correlation in the entire feature set, against the previous maximum of 0.271. It would be easy, and wrong, to report that as a temperature effect. Four checks say otherwise:

1. **The ten monthly `tmin` columns correlate 0.90–0.99 with each other.** September and March minimum temperatures are not the same weather; they are the same *place*.
2. **`corr(tmin_c_sep, tmean_c_season_normal) = 0.954`** — it is almost perfectly explained by the 1991–2020 normal, which is constant across years by construction.
3. **`year_variance_share = 0.010`.** Essentially none of its variance is between seasons. Whatever it is measuring does not change from year to year, so it cannot be weather.
4. **Within district, the correlation collapses from −0.330 to −0.064.**

The signal is **altitude**. Western Kenya's cooler highlands out-yield the warmer lowlands — deeper soils, more reliable rainfall, cooler grain fill. That is a real and useful *condition* variable, and it is the single most informative thing this extension adds. But it must be read as an agro-ecological-zone proxy, not as a temperature response, and any recommendation phrased as "yields fall as temperature rises" would be an artefact of that misreading.

The practical consequence: the temperature block is roughly **one variable expressed thirty times**. A tree model will split those columns arbitrarily and fragment their importance across all of them. The modelling package should expect that and prefer `tmean_c_season_normal` — the honest, explicit form of the same information — over any individual month.

### 9.2 Within a district, weather explains very little

Every weather feature's apparent signal is overwhelmingly *between* districts and nearly vanishes *within* them:

| Feature | pooled *r* | within-district *r* |
|---|---:|---:|
| `tmin_c_sep` | −0.330 | −0.064 |
| `gdd10_season` | −0.297 | −0.032 |
| `rain_mm_jul` | +0.239 | **+0.069** |
| `rain_mm_p3` (grain fill) | +0.184 | **+0.065** |
| `rain_mm_p2` (flowering) | +0.077 | **+0.065** |
| `rain_mm_season` | +0.071 | +0.005 |
| `rain_anom_pct_season` | +0.039 | −0.005 |

This is §10's first limitation arriving exactly as predicted: at 5.5 km, farms in the same district frequently share a CHIRPS cell, so there is little within-district weather variation left for these features to explain.

Two things survive it, and both vindicate specific design choices:

- **The plant-date-relative window beats the fixed calendar window.** `rain_mm_p2` and `rain_mm_p3` retain *r* ≈ 0.065 within district while `rain_mm_season` retains 0.005 — a thirteen-fold difference. Aligning rainfall to each farm's own planting date, rather than to the calendar, is what preserved the signal. §6.4's argument for building both windows is confirmed by measurement.
- **July and August rainfall — grain fill — carries the real rainfall signal**, not the season total. Averaging over March–August dilutes it to nothing.

**Read honestly: this extension adds a strong geographic signal and a weak but genuine grain-fill rainfall signal.** It does not add a large amount of new within-district predictive power, and no amount of feature engineering on a 5.5 km grid will change that — only finer data would.

---

## 10. Limitations

1. **Gridded rainfall is not gauge rainfall.** CHIRPS blends satellite estimates with station data at 5.5 km. Convective storms in western Kenya are frequently smaller than one pixel, so two farms in the same cell get identical rainfall when in reality one was hit and the other missed. This puts a ceiling on how much within-cell yield variation any rainfall feature can explain, and it is a property of the data source, not of this pipeline.

2. **Five seasons is few.** The between-year signal §5 measures rests on five observations of "a season". No diagnostic fixes that; only more seasons would.

3. **ERA5-Land temperature is reanalysis at 11 km**, in terrain with real elevation gradients. It is reliable for month-to-month and place-to-place *differences*, less so as an absolute reading at any one farm.

4. **`rain_mm_plant_to_harvest` is month-resolution** (§6.4).

5. **Soil is still missing.** Rainfall and temperature are two of the three environmental legs; the third is soil, available from the same catalogue (`ISDASOIL/Africa/v1`, or SoilGrids) at 30 m — finer than either dataset used here, and static, so it carries no year-label risk at all. It is the obvious next extension and nothing in this design blocks it.

6. **The temperature block is largely one variable.** §9.1 — thirty columns expressing altitude. Kept because monthly temperature was the explicit ask and the redundancy is now measured and documented rather than hidden, but the modelling package should not read thirty independent inputs into it.
