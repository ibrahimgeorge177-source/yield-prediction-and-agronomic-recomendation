# Kenya Maize Data: Feature Engineering — Documentation

**Input:** `data/cleaned/kenya_maize_cleaned.csv` (23,674 × 120) — the output of the cleaning stage
**Scope:** Kenya, maize only. CRISP-DM §3.3 *Feature Construction* and §3.5 *Train/Validation/Test Split Strategy*
**Pipeline:** `scripts/build_features.py` (also walked through, stage by stage, in `notebooks/02_kenya_maize_eda_and_feature_engineering.ipynb`)
**Outputs:** `data/features/kenya_maize_features_full.csv` (23,674 × 70), `kenya_maize_features_core.csv` (19,798 × 81), `kenya_maize_feature_manifest.csv` (137 rows), `kenya_maize_feature_stats.json`
**Author context:** Follows `kenya_maize_cleaning_documentation.md`. Where a decision here overrides or extends what the original CRISP-DM scoping report (`project1_crispdm_report.docx`) specified, it is called out explicitly.

---

## 1. What this stage is for, and how its rule differs from cleaning

The cleaning stage operated under one governing rule: **never silently discard or overwrite a source value.** Every correction was non-destructive, every judgment reversible, every original preserved alongside its flag.

That rule is the right one for cleaning and the wrong one for feature engineering. This stage operates under a narrower rule:

> **Never emit a feature that cannot mean the same thing in the training years and in the holdout year.**

The reason is the validation design. The CRISP-DM report (§3.5) specifies an out-of-time holdout — train on the earlier survey rounds, report the headline number on the most recent one (Kenya 2020). That is the correct design for a model whose job is to forecast *next* season, and it is precisely what makes survey-instrument drift dangerous rather than merely inconvenient.

Kenya's questionnaire rotates modules year to year. Under an out-of-time split, a column that is 100% present in 2018 and 0% present in 2016 is not a feature with missing values — **it is a year label wearing a feature's name.** A gradient-boosted tree will happily learn *"`fertility` is null ⇒ this is 2016 ⇒ predict 2,822 kg/ha"*, which is a real effect (2016 genuinely is the lowest-yielding year) attributed to entirely the wrong cause. It scores well in training and transfers to nothing.

So where cleaning preserved everything, this stage's core output is a set of **exclusions** and an explicit, documented **contract** — the manifest — recording for every column what it is, when it becomes knowable, and why it was kept or dropped.

The headline result: **maximum correlation between any emitted feature and the target falls from `0.9999999999999933` to `0.271`.**

---

## 2. What the audit found

Six findings, each of which would have survived a conventional "drop the sparse columns and model the rest" approach. Each is reproduced with its evidence in notebook 02 §1.

### 2.1 `yield_kg_pa` is the target

`corr(yield_kg_pa, yield_kg_ph) = 0.9999999999999933`. The ratio between them has a standard deviation of `2.3 × 10⁻⁷` around exactly `2.471050` — the same acre→hectare constant the cleaning stage verified and used for its 2020 backfill. It is not a correlated predictor; it is the target divided by a constant.

The cleaning stage was right to keep it (it is a genuine source column, and the per-acre figure is the one 2020 actually collected). It simply must not reach a model.

**Resolution:** dropped, along with the three columns the cleaning pipeline computed *from* the target — `yield_kg_ph_winsorized`, `yield_kg_ph_flagged_outlier`, `yield_kg_ph_crop_failure`. The pipeline carries an `assert` on the ratio's standard deviation, so a future data pull that changes this relationship fails loudly rather than silently altering what the screen means.

### 2.2 2016 is a reduced survey instrument, not a normal year

`fertility`, `pest_disease`, `weed`, `chickens`, `goats`, `hh_num_under5`, `FAW` and `growing_season_days` are **0% present in 2016** (`flood` is 0.9%). Their pooled 2016–2019 coverage still reads 70–75%, because the other three training years carry them in full.

This is why a single pooled train-coverage screen is not merely imprecise but actively misleading: it averages over exactly the thing being asked about. Any of these columns would enter a model as a free 2016 indicator.

**Resolution:** two structural changes to the screen. **Gate B** requires presence in ≥2 *individual* training years rather than ≥60% pooled — two years being the minimum at which a model can distinguish a variable from a year. **Gate C** handles the columns whose only failing year is 2016 by dropping the 3,876 **rows** (16.4% of the data) instead of the columns, and both feature files are emitted so the modeling package can benchmark that trade rather than inherit it.

### 2.3 `hybrid` is contradicted by its own row

`hybrid` reads `True` for **all 6,190** rows of 2020, against ~83% in every training year. But those same 2020 rows carry 884 `local` and 705 `mixed` values in `seed_category`, and **1,162 rows with `localseed_kg_ph > 0`**. The column contradicts the seed-quantity columns in the same row.

It is a defaulted field, not a genuine 100% adoption event. Two consequences follow, both bad:

- It passes every coverage screen (100% train, 100% holdout) and would enter the model as a **pure year proxy** — the single cleanest way to encode "this row is from 2020".
- It has **zero variance in the holdout**, so the report's §5.1 mandated hybrid-seed-advantage domain check cannot be run on it there at all.

**Resolution:** rebuild the concept from the seed *quantities*, which are populated and variable in all five years — `uses_hybrid_seed` (`hybridseed_kg_ph > 0`; 82.2 / 67.7 / 84.6 / 82.5 / 84.5 % by year) and `hybrid_seed_share`. The hybrid advantage is real and survives the split on these columns. `hybrid` is renamed `hybrid_reported_raw` and tagged `excluded`, so the contradiction stays inspectable without being usable.

This finding also motivated a general screen, **Gate D**: any column with a single value across every holdout row is dropped, since it can contribute nothing to an out-of-time prediction and can only encode training-year structure.

### 2.4 `plants_sqm_spacing` is a harvest measurement named like a planting decision

2020 introduced `plants_sqm_spacing` at 98.6% coverage, while the training years carry `row_spacing` and `plant_spacing`, from which a design density `10000 / (row × plant)` follows. Same nominal units — plants per m². Unifying them into one continuous density lever spanning all five years is the obvious move, and it is wrong.

| | corr with `yield_kg_ph` |
|---|---|
| 2020 `plants_sqm_spacing` (reported) | **0.534** |
| 2016–19 `10000/(row × plant)` (derived) | **0.097** |

A 5.5× difference in explanatory power between quantities that supposedly measure the same thing is not a data-quality artifact — it means they measure different things. The decisive evidence: **all 26 rows where `plants_sqm_spacing == 0` have exactly zero yield.** A planting *design* cannot be zero plants per m² — nobody plans an empty field. A *counted stand* can be, if the crop failed. `plants_sqm_spacing` is a realized plant count from the harvest quadrat: a post-season measurement, partially mechanical with the target.

**Resolution:** quarantined (`availability_tier = ex_post`, `role = excluded`) rather than unified. Unifying would have injected a harvest-time measurement into a feature the recommendation layer treats as a controllable lever, and made the holdout look artificially predictable — this is 2020's strongest non-obvious correlate. This is the clearest worked example of why the manifest carries an availability tier at all, rather than just a keep/drop decision.

### 2.5 `site` cannot be target-encoded; `district` can

| grouping | levels | train↔2020 correlation of group means |
|---|---|---|
| `site` | 2,133 | **0.286** |
| `district` | 51 | **0.717** |

Site-level training means have a standard deviation of 1,161 against a global target standard deviation of 1,689 — in-sample, a site encoding *looks* like it explains a large share of variance. But with a median of 9 training rows per site and 17.5% of sites carrying ≤3 rows, most of that apparent signal is small-sample noise being memorized, and it does not survive to the holdout.

**Resolution:** `site` is used as the **cross-validation grouping key only** (satisfying §3.5's requirement that splits be grouped by site, since multiple plots from one location across seasons are correlated). `district` — 51 levels, minimum 21 training rows, one district under 30 — is the defensible geographic feature. No target encoding is computed in this stage; if the modeling package wants one, it should be computed out-of-fold on `district`.

Raw GPS is excluded for a related but distinct reason: `corr(field_longitude, yield)` runs 0.297 / 0.299 / 0.164 / **0.457** across the training years and then collapses to **0.045** in 2020, because 2020 sampled 50 districts against training's 40. It is among the strongest training signals and is gone precisely where it would be scored. Combined with 5-decimal coordinates functioning as a household fingerprint, raw lat/lon is dropped and `district` carries the geography.

### 2.6 The wealth index needs its most natural companion feature withheld

§3.3 asks for a `wealth_index` over livestock counts and asset binaries. These are among the most year-fragmented columns in the dataset: **`owns_cows` is the only asset input available in all five years**, and there is no year in which all seven exist.

Two traps, one obvious and one not.

**The obvious one inverts this project's usual rule.** Everywhere else, an aggregate over partially-missing inputs should be paired with a coverage flag so the model can distinguish "not asked" from "asked and absent". Here that is catastrophic: the number of available asset inputs per row takes the values {2, 3, 5, 7}, and those values *identify the survey year* (3 ⇒ 2017, 5 ⇒ 2019, 7 ⇒ 2020; 2 covers 2016 and 2018, and even then over different asset sets — cows + electricity versus electricity + radio). A coverage flag on this feature is a year label with extra steps. **None is emitted.**

**The non-obvious one is in the scale, not the mean.** A naive globally-standardized index has a nearly year-flat *mean*, which is what makes it look safe. Its *standard deviation* is not flat — it falls monotonically as more inputs become available, purely because averaging more z-scores shrinks variance:

| | 2016 | 2017 | 2018 | 2019 | 2020 |
|---|---|---|---|---|---|
| naive index sd | 0.737 | 0.633 | 0.484 | 0.503 | 0.445 |
| after within-year re-standardization | **1.000** | **1.000** | **1.000** | **1.000** | **1.000** |

A tree reads the year off that spread.

**PCA is not the answer**, and the reason is structural rather than practical: PC1 fitted on 2017's available assets and PC1 fitted on 2018's are different linear combinations of different variables. They are not the same quantity and cannot share a column. §3.3 explicitly permits *"first principal component **or** additive score"*; the additive route is taken on that ground. (That `sklearn` is absent from one of the two Python interpreters on this machine is a secondary convenience, not the argument.)

**Resolution:** harmonize each asset to an `owns_X` boolean (`count > 0` where a count exists, else the binary — verified to agree 100% on the 6,024 holdout rows carrying both, with an `assert` guarding it) → z-score each input **within year** → row-mean the available z-scores → **re-standardize within year**.

Reported honestly: `corr(wealth_index, yield_kg_ph) = 0.053`. It is a weak feature. It is built because §3.3 mandates it and because `owns_cows` carries genuine agronomic meaning (draft power for land preparation, manure access) — not because it is expected to carry the model.

---

## 3. Full accounting of the pipeline

Every stage is a named function in `scripts/build_features.py`, re-run one at a time with printed diagnostics in notebook 02 §2. The notebook imports the script's functions rather than re-implementing them, so the two cannot drift apart.

| # | Stage | What it does | Effect |
|---|---|---|---|
| 3.1 | `load_cleaned` | Read the cleaned CSV and restore dtypes lost to CSV round-tripping | 44 boolean columns restored |
| 3.2 | `drop_unusable_columns` | Drop by four separately-recorded reasons | leakage 4, identifier 2, zero-variance 4, unit-incoherent 2 |
| 3.3 | `encode_ordinals` | Map text-bin columns to numeric midpoints | 4 columns (`distance_meter`, `seed_depth_num`, `fertility`, `slope_angle_des`) |
| 3.4 | `build_nutrient_features` | N / P₂O₅ / K₂O / total / `basal_n_share` / `topdress_applied`, from the `*_winsorized` columns | 6 new features; mean N 35.7 kg/ha, mean P₂O₅ 44.3 kg/ha |
| 3.5 | `build_seed_features` | `uses_hybrid_seed`, `hybrid_seed_share`; rename `hybrid` → `hybrid_reported_raw` | 2 new features, 1 quarantined |
| 3.6 | `build_wealth_index` | Harmonize 7 `owns_*` booleans; within-year z → row mean → within-year re-standardize | 8 new columns; sd = 1.000 in every year |
| 3.7 | `build_pest_disease_features` | Keep the survey's own `pest_disease`; do **not** OR the 8 fragmented pest binaries | 973-row disagreement documented |
| 3.8 | `build_agronomic_features` | `plant_date_doy`, `design_plant_density`, `compost_wb_ph`, `compost_applied`, `intercrop_is_legume` | 5 new features |
| 3.9 | `add_missingness_flags` | §3.3-mandated indicators for the hybrid replacement and nutrient/timing coverage | 3 new flags |
| 3.10 | `assign_cv_folds` | Balanced greedy GroupKFold over `site`, numpy-only, seeded | 5 folds × 3,493 rows; holdout at `-1` |
| 3.11 | `screen_feature_stability` | Gates A–D | 31 fail Gate A, 13 are Gate C, 6 fail Gate D |
| 3.12 | `assign_roles_and_tiers` | `role` × `availability_tier` from named constants | lever 47, condition 34, flag 21, shock 11 |
| 3.13 | `build_manifest` / `save` | Two CSVs, the manifest, the audit JSON, and 5 output assertions | keep 62, keep_core_only 12, drop 53, quarantine 4, infrastructure 6 |

### The four gates

Threshold choice is nearly irrelevant to the outcome — a 50% cut keeps 74 columns, a 90% cut keeps 60. What matters is the *shape* of the test.

- **Gate A — holdout viability.** `coverage_2020 ≥ 60%`. Deliberately **asymmetric**: a column absent in 2020 cannot be scored out-of-time at any training coverage. This is a different question from whether a model could learn it, and a symmetric screen conflates the two. Rejects 31 columns, including all five `prev_season_*`, `site_latitude`/`site_longitude`, `distance_meter`, `row_spacing` and `basal_method`.
- **Gate B — train learnability.** Presence in ≥2 *individual* training years. Pooled coverage can be satisfied by a single year; two is the minimum at which a variable is separable from a year effect.
- **Gate C — the rows-vs-columns trade.** 13 columns pass A+B but fail on 2016 alone. Rather than drop them, the `core` output drops 2016's 3,876 rows and keeps them: `fertility`, `pest_disease`, `weed`, `flood`, `FAW`, `growing_season_days`, `hh_num_under5`, `chickens`, `goats` and their derivatives. Both files ship.
- **Gate D — holdout variance.** Catches what coverage cannot: six columns at 100% coverage with a single holdout value — `hybrid_reported_raw`, `hybridseed_2017_methodology_flag` (True only in 2017), `distance_meter_is_missing` (uniformly True in 2020, since `distance_meter` left that questionnaire), `comp_method_is_missing`, `lime_kg_ph_flagged_outlier`, `plant_date_flagged_implausible`. Every one is a year proxy that no coverage-based screen would reject.

**Aggregates are held to a stricter standard, not a looser one.** An aggregate's coverage is a union and therefore mechanically inflated, so coverage is the wrong statistic for it. `pest_disease_any` — the OR over the 8 specific pest binaries that §3.3 nominally asks for — was rejected on *input-set stability* despite perfect coverage: the training years carry 1–4 of those columns while 2020 carries all 8 at ~100%, so an OR over a different input set each year is a different variable each year. The survey's own coarse `pest_disease` column is used instead.

### Two places this pipeline overrides the CRISP-DM report

**Nutrients are not collapsed to one scalar.** §3.3 asks for `total_npk_equivalent_kg_ph`, "a weighted sum of DAP/urea/NPK/CAN nitrogen-equivalent content". That phrasing conflates two problems: N and P₂O₅ are not interchangeable quantities, and summing them erases the structure that matters. The report also describes "five separate sparse columns", which mischaracterizes this data — DAP is nonzero in 20,221 rows (85.4%) and CAN in 14,878 (62.8%); those are the two real columns, representing basal phosphorus at planting and topdress nitrogen at knee height. Urea (497 rows), NPK (58) and lime (949) are near-constant.

So N and P₂O₅ are emitted separately, `total_nutrient_kg_ph` is still emitted for continuity, and **`basal_n_share`** is added — the fraction of a plot's nitrogen delivered as DAP at planting rather than as a later CAN topdress. Median 0.409, with a 75th percentile of 1.0, meaning a quarter of these farmers apply *no* topdress nitrogen at all. That is a real, controllable, highly actionable lever that a single summed scalar cannot express, and it is the highest-value derived feature in this package.

Nutrient fractions: DAP 18-46-0 and urea 46-0-0 are defined specifications. CAN is taken at **26% N — the Kenyan retail grade, deliberately not the European 27%**. Lime is excluded from every nutrient sum: CaCO₃ is a pH amendment, not a nutrient, and folding it in is a common and consequential error.

**The NPK blend ambiguity is retired by measurement, not argument.** Kenyan retail carries 17-17-17, 20-20-0, 23-23-0 and 25-5-5 for maize, and the survey never records which. Rather than defend a choice agronomically, the pipeline measures how much it can possibly matter: across the entire plausible blend space, **mean applied nitrogen moves by 0.02 kg/ha**, because NPK appears in 58 of 23,674 rows. 17-17-17 is adopted as a named constant, recorded in the manifest, and the sensitivity table is reproduced in the notebook.

---

## 4. The manifest — the contract

`data/features/kenya_maize_feature_manifest.csv` carries one row per candidate column (137 rows, including every dropped one with its reason). The two axes that make it a contract rather than a log are `role` and `availability_tier`, which are **orthogonal** and both required.

**An essential framing point:** the entire survey is administered *post-harvest*, so every variable in this dataset was physically recorded after the outcome it describes. The availability tier is about what a variable **refers to**, not when it was written down. Without that distinction the taxonomy reads as arbitrary.

| tier | meaning |
|---|---|
| `ex_ante` | Knowable at or committed at planting — the input bundle a farmer takes on credit, plot conditions, seed choice, timing |
| `mid_season` | Realized during the season — weather shocks, pest pressure, weeding, pesticide |
| `ex_post` | Only knowable after harvest — `harvest_date`, `growing_season_days`, `plants_sqm_spacing` |
| `leaky` | Derived from the target; never emitted |

Each downstream consumer selects its own inputs from these two columns:

| surface | selection | features available |
|---|---|---|
| Recommendation layer (report §4.1) | `role == "lever" AND tier == "ex_ante"` | 34 |
| Underwriting / loan sizing (§1.1a #1) | `tier == "ex_ante"` | 67 |
| Mid-season re-forecast (§1.1a #2) | `tier in {ex_ante, mid_season}` | 72 |
| Post-season yield estimation | `tier != "leaky"` | 74 |

Note that CAN is tiered `ex_ante` although it is physically applied as a mid-season topdress: it is committed at planting as part of the input loan, which is the decision the underwriting surface is actually making. `weed` is tiered `mid_season` because the number of weedings is genuinely accumulated across the season.

The manifest also carries per-year coverage (`cov_2016` … `cov_2020`), each gate's result, `near_zero_variance`, `constant_in_holdout`, and a one-sentence `decision_reason` for every column.

**Using the feature files.** The manifest is a *catalog*, not a prescribed model matrix. It deliberately retains near-duplicate columns — `dap_kg_ph` alongside `dap_kg_ph_winsorized`, `seed_type` / `seed_type_clean` / `seed_type_primary`, `n_kg_ph` alongside `total_nutrient_kg_ph` — so the modeling package can choose. Feeding a model both members of such a pair is redundant, not harmful for a GBM, but should be a deliberate choice.

---

## 5. Known limitations

- **Gate B tolerates one weak training year by design.** Requiring two good years means admitting columns with one bad one. Nine columns in the `core` file still have a training year below threshold: `cows`, `chickens`, `goats` and `FAW` (0% in 2018), `electricity` and `hh_num_under5` (0% in 2017), `plant_date_doy` and `growing_season_days` (~39% in 2017). They are admitted because two clean years suffice to separate a variable from a year effect, but the per-year coverage columns are in the manifest so a modeler can tighten this if a diagnostic suggests it.
- **The 2016 trade is not resolved, only made explicit.** 2016 is both the lowest-yielding year (mean 2,822 kg/ha) and the year with the reduced questionnaire. Those facts are confounded and nothing in this dataset separates them. That is exactly why both feature files ship — the modeling package should benchmark them, and reporting only the more favourable one would be a results-driven choice.
- **The domain-review curves are confounded.** The diminishing-returns and hybrid-advantage checks required by §5.1 both pass directionally: yield rises with DAP to ~100–125 kg/ha then plateaus, and hybrid users out-yield local-seed users by 500–1,000 kg/ha at *every* nitrogen level. But hybrid users also apply more nitrogen on average, so the marginal curves are confounded. These establish that the relationships are agronomically sane; they do **not** establish the marginal value of a kilogram of nitrogen holding seed choice constant. Only the fitted model with SHAP attribution can do that.
- **No imputation is performed.** Missing stays missing, consistent with the cleaning stage. GBMs handle `NaN` natively and §3.3 explicitly wants missingness treated as signal. Imputation is a modeling-time decision and doing it here would bake in an assumption that belongs downstream.
- **`compost` is never converted to nutrient units, deliberately.** Converting `comp_wb_pa` (wheelbarrows per acre) to nitrogen requires a wheelbarrow mass (50–70 kg wet) times a manure N fraction (0.5–1.5% of dry matter) — a product spanning an order of magnitude — and `comp_quality` is only 8% populated, so the guess cannot even be conditioned on decomposition state. Compost is carried in its own units. Declining a conversion that cannot be defended is a stronger methodological position than making one.
- **`site` is not target-encoded, and no geographic encoding is computed here at all.** If the modeling package wants one, it should be computed out-of-fold on `district`, never on `site`, and never on the holdout partition.
- **The 2017 `hybridseed` methodology flag was dropped by Gate D.** The cleaning stage flagged 3,991 rows where 2017's hybrid-seed weights came from two incompatible estimation methods. That flag is `True` only in 2017, making it a perfect year indicator, so Gate D removes it. The underlying data-quality concern is real and unresolved — it is simply not expressible as a feature under an out-of-time split.
- **Survey-instrument drift will recur.** Per the report §6.2 and the cleaning documentation, any new survey round must have its coverage matrix regenerated and this screen re-run before the feature set is assumed stable. The gates are re-runnable precisely for that reason.

---

## 6. Reproducing this pipeline

```bash
python scripts/build_features.py
```

Reads `data/cleaned/kenya_maize_cleaned.csv`; writes both feature CSVs, the manifest and the audit-stats JSON. The script depends only on `numpy` and `pandas` (matching the cleaning script), so it runs under either Python interpreter present on this machine; plotting lives in the notebook, which needs the Jupyter kernel's `matplotlib`.

`notebooks/02_kenya_maize_eda_and_feature_engineering.ipynb` reproduces every finding above with inline diagnostics and figures, and can be re-run top to bottom to regenerate this document's numbers from scratch.

---

## 7. What comes next

The modeling package, per report §4.1: a district×year median baseline → a per-crop GLM → LightGBM with SHAP → the constrained optimization layer over the fitted response surface. Evaluated per §4.2 (MAE and RMSE, grouped-by-site CV for tuning, the 2020 out-of-time holdout for the headline number) against the §1.5 success criterion of a **≥15–20% MAE reduction over the median baseline**.

That package's inputs are the `cv_fold` column (so the grouping requirement cannot be accidentally violated by a random row split) and the manifest's `role` / `availability_tier` axes (so each consumer surface trains on the features it will actually have at prediction time).
