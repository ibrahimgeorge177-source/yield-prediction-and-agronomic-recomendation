# District-level yield model — deployment guide

**Kenya maize (One Acre Fund MEL Agronomic Survey, 2016–2020)**

This document covers the deployable district-level model: what it predicts, how accurate
it is, how to run it, and what it cannot do. The modelling work behind it is in notebooks
04–06; this is the operational summary.

---

## 1 · What it predicts

**One number per district per season: the mean maize yield in kg/ha, with an 80%
interval and the number of plots behind it.**

The model predicts every plot and averages the predictions within a district. It does not
attempt to be accurate for an individual farm — notebook 05 measured that ceiling at
R² ≈ 0.18, because 82% of plot-to-plot variance lies *within* a district and season and is
driven by soil, management detail and recall error the survey does not capture. Averaging
to the district cancels most of that noise, which is why the district number is usable and
the plot number is not.

---

## 2 · Measured accuracy

Every figure below is **out-of-time**: the model is fitted on earlier seasons only and
scores a season it has never seen.

| Season scored | Fitted on | Districts | R² | MAE | corr |
|---|---|---|---|---|---|
| 2018 | 2016–2017 | 35 | +0.222 | 447 kg/ha | 0.561 |
| 2019 | 2016–2018 | 39 | +0.380 | 696 kg/ha | 0.653 |
| **2020** | **2016–2019** | **50** | **+0.586** | **329 kg/ha** | **0.765** |

By district size, on 2020:

| Districts with | Count | R² | MAE |
|---|---|---|---|
| ≥30 plots | 50 | +0.586 | 329 kg/ha |
| ≥50 plots | 47 | +0.607 | 301 kg/ha |
| ≥100 plots | 29 | +0.661 | 281 kg/ha |

**Plan for R² between 0.22 and 0.59, and MAE between 300 and 700 kg/ha.** The season
matters far more than the model: the spread across these three seasons (0.22 → 0.59) is
an order of magnitude larger than the spread across model families (§4). Quoting the 2020
figure alone would overstate what the next season is likely to deliver.

### The R² ≥ 0.70 target

**This target is not met, and it is not reachable with the present data.** It was tested
directly rather than assumed:

| Route tried | Best district R² achieved | Verdict |
|---|---|---|
| Plot ensemble averaged to district (shipped model) | 0.586 (2020), 0.22–0.38 earlier seasons | best available |
| Best single family, chosen after the fact (XGBoost) | 0.615 (2020) | still short, and chosen on the test season |
| District-season panel model (aggregate features + yield history) | −0.01 to +0.17 | worse; rejected on validation seasons |
| Blending the panel model in (weight fitted on validation) | weight chosen was 0.0 | no contribution |
| Equal-weighting districts during plot training | 0.525 (2020) | worse |
| Linear recalibration of district predictions | 0.577 vs 0.586 | no gain |
| Last season's district mean as the forecast | −1.32 (2020) | far worse |
| Coarser geographic zones (8–12 clusters of districts) | 0.77–0.89 in 2020, but −0.04 to +0.39 in 2018/2019 | not reliable |

The binding constraint is the **correlation** between what the features can say about a
district and what that district actually yields: it runs 0.56–0.77 across seasons. Since
R² cannot exceed corr² for a calibrated predictor, reaching 0.70 requires corr ≈ 0.84 —
above anything measured here in any season, by any method.

This is not a precision problem in the target. The observed district mean is measured
sharply: sampling noise is 157 kg/ha against a 774 kg/ha spread between districts, so the
reliability of the target is 0.96 and an R² of 0.70 would be *arithmetically* attainable
if the features supported it. They do not. Closing the gap needs better inputs — soil
tests, GPS-verified plot area, crop-cut yields instead of farmer recall — not a better
estimator.

The one framing that does clear 0.70 is the coarser-zone aggregation in 2020 (R² = 0.77–0.89
over 8–12 zones), but the same recipe scores near zero in 2018, so it is a property of
that season rather than a reportable capability.

---

## 3 · Running it

Two artefacts ship in `data/models/`:

| Artefact | Fitted on | Use it for |
|---|---|---|
| `district_v1/` | 2016–2019 | reproducing the measured accuracy above; 2020 is a clean test season |
| `district_v2/` | 2016–2020 | **predicting a new season** — it has seen every season available |

### Score a season already in the feature file

```bash
python scripts/predict_district.py --model data/models/district_v2 --year 2020
```

### Score a new season from a CSV

```bash
python scripts/predict_district.py --model data/models/district_v2 \
    --input data/features/season_2021.csv --out district_forecast_2021.csv
```

The input needs the engineered feature columns of
`data/features/kenya_maize_features_full.csv` plus `district`. Build it for a new season by
re-running `scripts/clean_kenya_maize.py`, `scripts/run_weather_pipeline.py` and
`scripts/build_features.py` on that season's survey. `yield_kg_ph` is optional: include it
and the run is scored, omit it and you get an unscored forecast.

Output columns: `district`, `n_plots`, `pred_mean`, `pred_lo`, `pred_hi`, `pred_sd`, and —
when yields are supplied — `actual_mean`, `error`, `inside_band`.

### Retrain

```bash
python scripts/train_district_model.py --train 2016-2021 --val 2020,2021 \
    --test none --out data/models/district_v3
```

Retrain when a new season arrives. `--no-nn` drops the neural member (removes the torch
dependency, roughly halves the artefact, costs a little accuracy) and `--top-configs 3`
produces a 50 MB artefact instead of 94 MB for about 0.01 R².

---

## 4 · What is inside

The plot model is the notebook 06 ensemble, unchanged: **LightGBM, XGBoost, RandomForest,
ExtraTrees and an entity-embedding neural network**, each represented by the average of its
top five configurations, combined with equal weights. Their district-level scores are close
enough to be interchangeable, which is why the blend rather than a single winner is shipped:

| Member | 2018 | 2019 | 2020 |
|---|---|---|---|
| LightGBM | 0.167 | 0.309 | 0.421 |
| XGBoost | 0.105 | 0.399 | 0.615 |
| RandomForest | 0.229 | 0.361 | 0.565 |
| ExtraTrees | 0.203 | 0.361 | 0.569 |
| NeuralNet | 0.283 | 0.388 | 0.553 |
| **Blend (shipped)** | **0.222** | **0.380** | **0.586** |

No member leads in every season. XGBoost is best on 2020 and worst on 2018; the network is
best on 2018 and fourth on 2020. Choosing one on the evidence available before 2020 would
have picked the network, which then scores 0.553 rather than the blend's 0.586.

Everything needed to score unseen rows is inside the artefact: the weather PCA basis,
target-encoding maps, category codes, imputation medians, the fitted estimators, the
network weights, and the interval parameters. Preprocessing is fitted on training rows
only, so scoring a new season introduces no leakage.

### Intervals

Interval width is fitted on validation seasons as `sd(n) = sqrt(a + b/n)`, giving wider
bands for districts with fewer plots. The current fit gives about ±1,000 kg/ha at n = 50.

**The bands are conservative: the nominal 80% interval covered 92% of districts in 2020.**
They are calibrated on 2018 and 2019, which were harder seasons than 2020. Treat them as a
"rarely wrong" bound rather than a sharp 80% band, and refit when a new season lands.

---

## 5 · Limits worth stating to any consumer of these numbers

1. **Districts new to the model are less reliable.** 11 of the 50 districts in 2020 had never
   appeared in training (14.5% of plots). Their ranking still worked (corr 0.86) but their
   level error was twice as large: MAE 543 kg/ha against 271 for known districts. The four
   worst-predicted districts in 2020 were all new ones.
2. **Do not surface per-farm numbers from this model.** Plot-level R² is ~0.16 and the
   predictions are deliberately shrunk toward the mean; notebook 06 §9 shows that
   un-shrinking them to a realistic spread costs almost all their accuracy.
3. **Below ~20 plots a district mean is mostly sampling noise** and the pipeline does not
   report it by default (`--min-plots`).
4. **A district-mean forecast still needs plot-level inputs for that season** — seed type,
   fertiliser rates, planting dates, weather. The model converts those into a yield
   estimate; it cannot forecast a district from its history alone (that route scores
   R² = −1.32).
5. **Season effects dominate.** Expect the accuracy of any single season to land anywhere in
   the 0.22–0.59 band measured here.
