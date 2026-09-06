# Kenya Maize Data: Understanding & Cleaning — Documentation

**Source dataset:** One Acre Fund MEL Agronomic Survey, 2021
(`data/raw/Copy of Final 2021_One_Acre_Fund_Agronomic_Survey_Data_2021.xlsx`)
**Scope of this document:** Kenya, maize only (a sub-population of the full 7-country, 4-crop survey)
**Pipeline:** `scripts/clean_kenya_maize.py` (also walked through, cell by cell, in `notebooks/01_kenya_maize_data_understanding_and_cleaning.ipynb`)
**Output:** `data/cleaned/kenya_maize_cleaned.csv` (23,674 rows × 120 columns)
**Author context:** Prepared as a narrower, production-ready follow-on to `project1_crispdm_report.docx`, the original 7-country CRISP-DM scoping report, after the team decided to focus modeling effort on Kenya maize specifically.

---

## 1. Why Kenya maize, specifically

The source workbook covers Burundi, Kenya, Rwanda, Tanzania, Malawi, Zambia, and Uganda, across four crops (maize, bush beans, climbing beans, potato), 2015–2020, 81,411 rows total. That coverage is **not a full grid** — it is close to block-diagonal:

| Country | Beans | Climbing beans | Maize | Potato |
|---|---|---|---|---|
| Burundi | 1,003 | 15,148 | 5,873 | 1,932 |
| Kenya | 0 | 0 | **23,674** | 0 |
| Malawi | 0 | 0 | 2,594 | 0 |
| Rwanda | 7,746 | 7,803 | 5,604 | 2,202 |
| Tanzania | 0 | 0 | 5,881 | 0 |
| Uganda | 0 | 0 | 246 | 0 |
| Zambia | 0 | 0 | 1,697 | 0 |

Maize is the only crop grown in every country. Kenya is the only country where maize is the *only* crop recorded — all 23,674 Kenya rows are maize, confirmed by direct crosstab against the raw data (not merely asserted from the report). That makes Kenya maize the cleanest possible slice to build a rigorous, production-grade pipeline against: there is no cross-crop harmonization decision to make, and no cross-country survey-instrument reconciliation either. Everything below concerns exactly one country, one crop, one survey lineage (2016–2020).

This document assumes the CRISP-DM scoping already done in `project1_crispdm_report.docx` and narrows it; where a finding here confirms or corrects something from that report, it's called out explicitly.

---

## 2. What's actually in the Kenya maize data

**Shape:** 23,674 rows × 117 raw columns → 23,674 × 120 after cleaning (columns dropped, then re-added as harmonized/derived/flag columns — see §4 for the full accounting).

**Unit of observation:** one farmer's maize plot in one growing season (long rains only — Kenya's survey never recorded a short-rains round).

**Temporal coverage:** 2016–2020. No 2015 data (Kenya joined the survey one year after Burundi). Row counts per year: 2016 = 3,876; 2017 = 3,991; 2018 = 4,245; 2019 = 5,372; 2020 = 6,190.

**Geographic coverage:** 53 raw district labels (51 after harmonization, see §4.2), 2,133 unique sites.

**Target variable, `yield_kg_ph` (kg harvested per hectare):** 100% complete for Kenya — unlike the pooled 7-country dataset, which is missing 2.7% of its target values. Per the source's own `Description` sheet, this variable is physically measured (two randomly placed harvest boxes, weighed), not self-reported, and it shows: mean 3,150 kg/ha, median 2,930 kg/ha, std 1,689 kg/ha, range [0, 10,131] kg/ha. That max is far more plausible than the pooled dataset's 37,667 kg/ha (almost certainly a potato row) — Kenya-maize-only framing removes that cross-crop distortion entirely. 326 rows (1.4%) record an exact `0`, consistent with genuine total crop failure rather than a data artifact.

**Predictor completeness** is not uniform, and understanding *why* it isn't is most of the work in this document (§3). In short: 18 columns are entirely empty for Kenya (never asked here), a cluster of otherwise-important columns sit at ~74% complete for one specific structural reason, and the rest range from fully populated to sparse depending on which survey year asked the question — consistent with the source description's warning that *"survey questions were also not always uniform across countries [and years]."*

---

## 3. Findings from the data-understanding pass

Each finding below is stated with the evidence behind it, then the cleaning decision it led to (fully detailed in §4).

### 3.1 `unique_id` is inconsistently typed in the source workbook

Before touching any agronomic column, a basic check on the row identifier turned up a genuine defect: 747 of 23,674 Kenya rows (3.2%) have `unique_id` stored as a Python `int` or, in one case, a `datetime`, instead of the expected opaque hex-like string (e.g. `"b801f443"`). This is an artifact of Excel/openpyxl auto-typing cells that happen to look numeric — and for the longer values, it visibly corrupts precision (one raw value reads `703199999999999984889878330021295977618232655886971772664638087950778280343318281846784`, clearly not the original ID). This affects every survey year (2016: 119, 2017: 120, 2018: 135, 2019: 151, 2020: 221 int-typed + 1 datetime-typed), so it's a workbook-wide quirk, not an isolated glitch or something introduced by this pipeline.

**Resolution:** cast `unique_id` to string. Checked for collisions after casting — there are none (0 duplicates among 23,674 IDs) — so every row, corrupted ID or not, remains uniquely addressable. The original numeric/date values are not recoverable from this file (the precision loss already happened upstream, in how the workbook was produced), but uniqueness for joins and deduplication is fully preserved.

### 3.2 18 columns are structurally empty for Kenya

Column-by-column completeness against the Kenya-maize subset (not the pooled dataset) shows 18 columns at exactly 0%:

`seed_kg`, `seed_kg_pa`, `seed_kg_ph`, `hybridseed_kg`, `termites`, `gls`, `anthracnose`, `hail`, `stalk_rot`, `kelnel_rot`, `distance_min`, `dap_method`, `urea_method`, `npk_method`, `planting_method`, `heavy_rain`, `sitename`, `comp_kg_ph`.

Cross-referencing the `Variables` sheet's per-country-year question roster explains each: `dap_method`/`urea_method`/`npk_method`/`planting_method` were only ever collected in Rwanda; `distance_min` is Rwanda/Tanzania's version of what Kenya records as `distance_meter`; `seed_kg*`/`hybridseed_kg` are superseded in Kenya by the separate local/hybrid seed columns; several pest/shock flags (`termites`, `gls`, `anthracnose`, `hail`, `stalk_rot`, `kelnel_rot`, `heavy_rain`) were simply never asked in Kenya's survey instrument; `comp_kg_ph` is unused because Kenya measures compost in wheelbarrows-per-acre (`comp_wb_pa`) instead of kg/ha.

**Resolution:** drop all 18 outright. There is nothing to clean in a column with zero observations, and keeping them would misleadingly suggest they're usable.

### 3.3 A structural ~26% gap in every per-hectare column — with a clean fix

After dropping the empty columns, completeness for the remaining fields clusters into visible tiers. One tier stood out: `plot_hectares` and every `*_kg_ph` fertilizer/seed column (`dap_kg_ph`, `urea_kg_ph`, `npk_kg_ph`, `can_kg_ph`, `lime_kg_ph`, `localseed_kg_ph`, `hybridseed_kg_ph`) all sit at 57.5–73.9% complete — visibly worse than their per-acre (`*_kg_pa`) counterparts, most of which are ≥99.5% complete.

The `Variables` sheet explains this precisely: against `plot_hectares` (and every `_ph` column) for **Kenya 2020**, the roster carries the comment *"Only collect acreage data."* Kenya's 2020 round — 6,190 of 23,674 rows, 26.1% — never recorded hectares at all. That 26% lines up almost exactly with the completeness gap on every affected column.

This would ordinarily mean losing an entire — and the most recent — survey year's worth of per-hectare figures, right when they matter most for an out-of-time validation split. Before accepting that loss, we tested whether hectares could be reliably reconstructed from the always-present acreage fields.

**What we found:** for every row where `plot_hectares` and `plot_acres` are *both* present (17,484 rows), `plot_hectares / plot_acres` equals `0.404686` — the standard acre-to-hectare conversion — to within floating-point error (std ≈ 5×10⁻⁹). More tellingly, `yield_kg_ph / yield_kg_pa` equals the reciprocal, `2.471050`, to the same precision **even for the 6,190 rows where `plot_hectares` is completely absent** (std ≈ 8×10⁻⁸, n=6,103). In other words, One Acre Fund's own pipeline computed `yield_kg_ph` using a fixed conversion constant, not a separately measured hectare figure — the "missing" hectare field was never actually needed to produce the per-hectare target.

The same exact-constant relationship holds for every other `_pa`/`_ph` pair checked: `dap_kg_ph` (n=14,946), `urea_kg_ph` (n=88), `npk_kg_ph` (n=30), `can_kg_ph` (n=11,255), `lime_kg_ph` (n=803), and `localseed_kg_ph` (n=4,333) all reproduce the 2.471050 ratio to 7+ decimal places. One exception: `hybridseed_kg_ph`.

**The `hybridseed` exception (documented, not silently backfilled):** splitting the ratio check by year shows 2016, 2018, and 2019 follow the same exact constant, but **2017 does not** (mean ratio 3.79, std 2.98 — scattered across multiples of the base constant). The `Variables` sheet's own footnote on 2017 `hybridseed_kg_pa` explains why: that year's hybrid-seed weight was *"estimated based on the kg of hybrid seed that 1AF farmers took. For non-1AF farmers, we estimated kgs based on the \$ spent on seed and assumed it was hybrid if store bought"* — two different, incompatible estimation methods sharing one column, for farmers whose land-area denominators may also differ. No 2017 rows are actually missing `hybridseed_kg_ph` (the gap is 2020-only, same as every other column), so this doesn't block backfilling 2020; it does mean 2017's existing hybrid-seed figures are less trustworthy than the other four years.

**Resolution:**
- Backfill `plot_hectares` and every `*_kg_ph` column for rows where the `_ph` value is missing but the corresponding `_pa`/`plot_acres` value is present. **The two kinds of column convert in opposite directions and must not share a factor:** a *rate* (kg per unit area) is multiplied by `1 / 0.404686 ≈ 2.471050`, because the same physical application is a larger number per hectare than per acre; an *area* is multiplied by `0.404686`, because a half-acre plot is a smaller number in hectares. **Correction (base-model stage):** `plot_acres`→`plot_hectares` originally sat in the rate list and received the rate factor, inflating 2020's plot sizes by `2.471050 / 0.404686 = 6.107×`. Because only 2020 required backfilling, the error landed exactly on the out-of-time holdout boundary. Fixed in `backfill_per_hectare_fields`, which now carries an assertion on the finished ratio; see `kenya_maize_base_models_documentation.md` §3.1.
- Recovered cell counts: `plot_hectares` 6,190 rows (73.9%→100.0% complete), `dap_kg_ph`/`urea_kg_ph`/`npk_kg_ph`/`hybridseed_kg_ph` 6,096 rows each (73.9%→99.6%), `can_kg_ph` 6,286 rows (73.1%→99.6%), `lime_kg_ph` 9,931 rows (57.5%→99.4%), `localseed_kg_ph` 7,699 rows (67.0%→99.5%) — **54,490 previously-null cells recovered in total**, across 8 columns, using arithmetic the source data already implicitly relies on rather than an invented assumption.
- Flag all 2017 `hybridseed_kg_pa`/`_ph` rows (3,991 rows) with `hybridseed_2017_methodology_flag`, without altering their values — a downstream user decides whether to trust or exclude them.
- Once backfilled, `_kg_pa`/raw `_kg` columns became pure linear duplicates of the `_kg_ph` versions (correlation = 1.0), so they're dropped as redundant (§4.4) rather than carried forward alongside their now-complete `_ph` counterpart.

This single finding — that a "missing" survey year's derived fields could be reconstructed exactly from a verified relationship already present in the rest of the data — is the highest-value result of the data-understanding pass, and the main reason this cleaning pass adds real value beyond what a naive "drop what's missing" approach would produce.

### 3.4 Categorical labels: two harmless-looking inconsistencies

- **`season`:** every Kenya row is long-rains, but it's spelled two ways across years — `"lr"` for 2016–2019 (17,484 rows) and `"long rain"` for 2020 (6,190 rows). Harmonized to a single value, `"long_rains"`. (Since Kenya's survey never records anything else, this column carries zero variance for Kenya specifically — kept for lineage/provenance, e.g. if this table is later joined back into the multi-country dataset, but a modeler should know not to expect it to be predictive here.)
- **`district`:** 53 raw labels collapse to 51 once a genuine naming collision is fixed. `kakamega(south)`/`kakamegab(north)` (used 2016–2019) and `kakamegasouth`/`kakameganorth` (used in 2020, no punctuation) are the same two districts spelled two different ways — left alone, they would silently fragment into 4 categories instead of 2, corrupting any district-level aggregate or a group-by-site train/test split. All other 51 district labels were checked and found to be genuinely distinct places (no further collisions found). 1,628 rows were remapped.

### 3.5 One GPS coordinate on the wrong continent

`field_latitude`/`field_longitude` should fall within Kenya's borders (~lat -4.9 to 5.1, lon 33.5 to 42.0). Of 23,160 rows with coordinates, exactly one sits at approximately lat 29°, lon 76° — near the India/Pakistan border, thousands of kilometers from Kenya. `site_latitude`/`site_longitude` are also null for this row, so there's no fallback coordinate to substitute.

**Resolution:** null both `field_latitude` and `field_longitude` for this row and set `field_gps_flagged_invalid = True`, rather than guess a replacement.

### 3.6 Date fields: a small number of clearly mistyped years, and a resulting distortion in growing-season length

`plant_date` is 88.6% complete, `harvest_date` 76.5%. Checking each `plant_date`'s year against the survey's own `year` column (should match, or be one year prior for late-year plantings) turns up 38 rows with an implausible year — including a literal `plant_date` of `2100-04-30`, and several values that read like digit transpositions (`2012` where `2016` was meant, `2024`/`2025` where `2019` was meant). These 38 rows then corrupt any growing-season-length calculation downstream: `harvest_date − plant_date` (in days) ranges from a nonsensical −29,492 to 2,001 days when computed naively, even though the middle 90% of the distribution (105th to 95th percentile: 106–222 days) is entirely believable for Kenyan long-rains maize (typically planted March, harvested July–September).

**Resolution:** a two-stage screen. First, null `plant_date` wherever its year falls outside `[survey_year − 1, survey_year]` (38 rows), flagged via `plant_date_flagged_implausible`. Second, compute `growing_season_days` from the (now-cleaner) dates, and null it — flagged via `growing_season_days_flagged` — wherever it still falls outside a **[60, 300]-day** plausibility window (a generous margin around the observed 106–222-day middle 90%); 111 rows fall outside this window after the first-stage fix. `growing_season_days` ends up available for 16,926 rows (71.5%). Both raw date columns are left completely untouched — only the derived gap and two flags are added.

### 3.7 Fertilizer and compost outliers need domain ceilings, not blind percentiles

Per-hectare fertilizer columns are heavily zero-inflated (most smallholders apply 0 or a modest amount of any given fertilizer type in a season), which makes a naive statistical winsorization threshold actively misleading. Concretely, `npk_kg_ph`'s 99.5th percentile is `0` — because so few Kenyan farmers in this dataset use NPK at all — so a percentile-based cap would flag every single real NPK application as an "outlier," which is the opposite of what winsorization should do.

**Resolution:** use an agronomic plausibility ceiling instead. Single-nutrient fertilizers (DAP, urea, NPK, CAN) rarely exceed 150 kg/ha even under intensive smallholder management, so a **500 kg/ha** ceiling is used (already >3x the plausible high end). Lime is a soil amendment applied in much larger bulk (often 1–2+ t/ha for acidity correction), so it gets a separate **2,000 kg/ha** ceiling. Rows above these ceilings: `dap_kg_ph` 31, `urea_kg_ph` 2, `npk_kg_ph` 0, `can_kg_ph` 36, `lime_kg_ph` 1 — small, specific counts consistent with genuinely exceptional entries rather than an artifact of an arbitrarily chosen cutoff.

`comp_wb_pa` (wheelbarrows of compost per acre — a Kenya-specific unit with no equivalent agronomic reference range in the literature) is heavily right-skewed: median 0, mean 6.15, max 3,200. With no domain ceiling available, this one *is* handled statistically — winsorized at the 99.5th percentile (140 wheelbarrows/acre), flagging 112 rows.

In every case, the raw value is preserved; a `*_flagged_outlier` boolean and a `*_winsorized` companion column are added alongside it. `yield_kg_ph` itself is treated the same way as `comp_wb_pa` (no domain ceiling for yield the way there is for a fertilizer rate — hybrid maize under strong management can plausibly exceed common rule-of-thumb ceilings): winsorized at the 99.5th percentile (8,701 kg/ha), flagging 118 rows, with the raw value and a `yield_kg_ph_winsorized` companion both retained.

### 3.8 `weed` (times weeded per season) has a small number of impossible values

The `Variables` sheet documents this question's Kenya response range as 0–5. 7 rows fall outside it (values of 22, 23, 15, and 26) — not plausible weeding counts, almost certainly entry errors (e.g. a miskeyed date fragment or an unrelated number landing in the wrong field).

**Resolution:** these 7 values are nulled (not winsorized/capped — a weeding count of "22" isn't a capped version of a real observation, it's simply wrong), flagged via `weed_flagged_invalid`.

### 3.9 `plot_acres`: flagged, not altered, because it's a denominator

`plot_acres` (100% complete) feeds `plot_hectares` and, transitively, every `_kg_ph` column. Its 99.9th percentile is 8 acres; the max is 30 acres. Given Kenya's plot definition in this survey — *"all land area dedicated to that crop/field type (program + non-program land)"*, per the source description, not just the single physically-harvested plot as in Rwanda — a genuinely large consolidated land holding is plausible, not automatically an error.

**Resolution:** flag rows above the 99.9th percentile (`plot_acres_flagged_outlier`, 23 rows) for downstream review, but do not alter or cap the value — capping a denominator silently would distort every field derived from it.

### 3.10 `seed_type` is farmer-reported free text, not a clean category

`seed_type` looks 100% complete, but it's unstructured text: 295 distinct raw values, many of which are the same underlying seed variety spelled differently across years (`local_maize` / `localmaize` / `local`; `other_hybrid` / `otherhybrid`; `sc_punda_milia_53` / `punda_milia_53` / `scpundamilia53`), and 1,487 rows list more than one variety space-separated in a single cell (e.g. `"dk_8031 local_maize"`) — genuinely mixed planting, not a data error.

**Resolution:** rather than force this into one clean category (which would either discard the mixed-planting signal or need an unbounded synonym dictionary), cleaning normalizes whitespace and a documented set of known synonyms token-by-token, then derives three new columns without touching the original:
- `seed_type_clean` — the normalized text (295 → 238 distinct values after synonym harmonization)
- `seed_type_primary` — the first listed variety
- `seed_type_is_mixed` — boolean, true for the 1,487 multi-variety rows
- `seed_category` — a coarse bucket: `hybrid_branded` (16,234 rows), `local` (4,010), `other_hybrid` (1,849), `mixed` (1,487), with 94 rows null (matching `seed_type`'s own 94 null rows)

---

## 4. Full accounting of the cleaning pipeline

Every step below is implemented as a named function in `scripts/clean_kenya_maize.py` and re-run, one stage at a time with printed diagnostics, in `notebooks/01_kenya_maize_data_understanding_and_cleaning.ipynb` §2. The two are the same code — the notebook imports and calls the script's functions rather than re-implementing them, so they cannot drift apart.

**Guiding principle:** never silently discard or overwrite a source value. Every correction either (a) fixes a demonstrable typing/entry error, (b) harmonizes labels that clearly denote the same thing, or (c) adds a new flag/derived column next to the untouched original.

| # | Stage | What it does | Rows/cells affected |
|---|---|---|---|
| 4.1 | `load_kenya_maize` | Load `Data` sheet, filter to `country=='Kenya' & crop=='maize'`, assert this equals all-of-Kenya (a guard against the assumption silently changing in a future data pull) | 23,674 rows selected from 81,411 |
| 4.2 | `fix_unique_id_dtype` | Cast `unique_id` to string; verify no post-cast collisions | 747 rows (3.2%) had non-string IDs in source |
| 4.3 | `drop_empty_columns` | Drop the 18 columns that are 100% null for Kenya (§3.2), with a guard that warns if the empty-column set ever differs from what's expected | 117 → 99 columns |
| 4.4 | `harmonize_categoricals` | Standardize `season` labels to `"long_rains"`; standardize the 4 Kakamega district spelling variants to 2 (§3.4) | 23,674 season rows relabeled; 1,628 district rows remapped |
| 4.5 | `validate_gps` | Null `field_latitude`/`field_longitude` outside Kenya's bounding box; add `field_gps_flagged_invalid` | 1 row |
| 4.6 | `clean_dates` | Parse `plant_date`/`harvest_date`; null implausible-year `plant_date` values; compute `growing_season_days` and null it outside [60,300] days; add `plant_date_flagged_implausible` and `growing_season_days_flagged` | 38 rows (implausible plant year); 111 rows (implausible season length) |
| 4.7 | `backfill_per_hectare_fields` | Backfill 7 `*_kg_ph` rate columns from their `*_pa` counterparts (×2.471050) and `plot_hectares` from `plot_acres` (×0.404686) — opposite directions, asserted (§3.3); flag 2017 `hybridseed` rows as lower-confidence | 54,490 cells backfilled across 8 columns; 3,991 rows flagged (2017 hybridseed) |
| 4.8 | `drop_redundant_columns` | Drop raw `_kg` totals and `_kg_pa` columns now perfectly collinear with their backfilled `_kg_ph` counterparts | 13 columns dropped (`can_kg`, `dap_kg`, `urea_kg`, `lime_kg`, `npk_kg`, `dap_kg_pa`, `urea_kg_pa`, `npk_kg_pa`, `can_kg_pa`, `lime_kg_pa`, `localseed_kg_pa`, `localseed_kg`, `hybridseed_kg_pa`) |
| 4.9 | `clean_seed_type` | Normalize `seed_type` whitespace/synonyms; derive `seed_type_clean`, `seed_type_primary`, `seed_type_is_mixed`, `seed_category` (§3.10) | 295 → 238 distinct normalized values; 4 new columns |
| 4.10 | `handle_outliers` | Flag + winsorize (or null, for `weed`) yield, 5 fertilizer-rate columns, `comp_wb_pa`, `plot_acres`, `weed` (§3.6–3.9) | See §3.6–3.9 for per-column counts; 9 columns get flags, 6 get winsorized companions |
| 4.11 | `add_missingness_flags` | Add explicit `<col>_is_missing` indicators for 7 structurally sparse columns (`fertility`, `slope_angle_num`, `slope`, `distance_meter`, `pest_disease`, `comp_method`, `comp_quality`), so "not asked this year" is distinguishable from "asked but silently missing" without imputing anything | 7 new columns |
| 4.12 | `finalize_dtypes` | Cast 22 binary 0/1 columns to nullable `boolean` dtype, preserving `NaN` as a real "unknown" state distinct from `False` | 22 columns re-typed |
| 4.13 | `save` | Sort by `year`, `unique_id`; write CSV; write a JSON audit-stats file with every number in this document | 23,674 rows × **120 columns** |

**Net column accounting:** 117 raw → 99 after dropping empty columns (§4.3) → 86 after also dropping redundant `_kg`/`_kg_pa` duplicates (§4.8) → **120 final**, after adding back: 1 `district_raw` audit column, 2 GPS/date flags + `growing_season_days`, 4 seed-type columns, 15 outlier flag/winsorized columns, 7 missingness flags, 2 hybridseed/methodology flags. See `data/cleaned/kenya_maize_cleaning_stats.json` → `final_columns` for the literal final column list.

**What was deliberately *not* done, and why:**
- **No imputation.** Missing values are left as `NaN` throughout. The downstream modeling plan (per `project1_crispdm_report.docx` §4.1) is gradient-boosted trees, which handle missing values natively and can exploit "missingness as signal" (e.g. `fertility_is_missing` correlating with survey year) far better than an imputed placeholder would. Imputation is a modeling-time decision, not a cleaning-time one.
- **No row deletion.** Every quality issue found was fixable via a flag or a targeted null, not grounds to drop a row. This preserves the entire physically-weighed yield record — including the 326 true crop-failure rows, which the wider project's credit-risk framing (CRISP-DM report §1.1a) specifically needs visible, not filtered out.
- **No destructive overwrites.** Winsorization always produces a new `*_winsorized` column; the raw value is never replaced. The one deliberate exception is `weed`, where out-of-range values are nulled rather than capped, because a weeding count of "22" isn't a valid observation clipped to a boundary — it's simply wrong, and no meaningful "capped" version of it exists.

---

## 5. Known limitations & what remains uncertain

- **`hybridseed_kg_ph` for 2017** (3,991 rows) uses a different, internally inconsistent estimation methodology than the other four years (§3.3). It is flagged, not corrected — there is no way to retroactively reconcile the two estimation approaches from this workbook alone. A modeler should consider excluding or down-weighting this column for 2017 rows, or treating `hybridseed_2017_methodology_flag` as a feature in its own right.
- **747 `unique_id` values were mistyped in the source workbook** (§3.1); the original string values are not recoverable for the subset that suffered visible float-precision loss. Uniqueness is preserved for all internal joins/deduplication, but if this table is ever joined against another OAF system by `unique_id`, those 747 rows may not match correctly.
- **`plot_acres` values above the 99.9th percentile (23 rows) are flagged but not verified.** Kenya's plot definition legitimately allows for large consolidated land holdings, so these were not assumed to be errors, but they were not independently confirmed as correct either.
- **Growing-season length is unavailable for 28.5% of rows** (`growing_season_days` populated for 16,926 of 23,674) — a combination of missing raw dates (`harvest_date` is only 76.5% complete to begin with) and the 149 rows nulled for date-quality reasons (§3.6).
- **Zero-inflation in most fertilizer columns is real, not a cleaning artifact.** A large share of Kenyan smallholders in this data simply don't apply a given fertilizer type in a given season. This is agronomically expected, not a completeness problem, but a naive mean/median summary of these columns will understate typical application rates among farmers who *do* use them — segment on non-zero values when analyzing fertilizer intensity.
- **Data collection years (2016–2020) predate this cleaning effort by several seasons.** Per the source's own description, the survey instrument itself changes year to year — any new survey round should be re-audited against the `Variables` sheet roster before assuming this pipeline's column list and completeness patterns still hold, per the monitoring guidance already given in `project1_crispdm_report.docx` §6.2.

---

## 6. Reproducing this pipeline

```bash
python scripts/clean_kenya_maize.py
```

Reads `data/raw/Copy of Final 2021_One_Acre_Fund_Agronomic_Survey_Data_2021.xlsx`, writes `data/cleaned/kenya_maize_cleaned.csv` and `data/cleaned/kenya_maize_cleaning_stats.json`. The notebook (`notebooks/01_kenya_maize_data_understanding_and_cleaning.ipynb`) reproduces the same steps with narrated exploration and inline diagnostics, and can be re-run top-to-bottom to regenerate everything, including this document's numbers, from scratch.
