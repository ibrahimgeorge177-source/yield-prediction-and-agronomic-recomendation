# The recommendation layer

**Kenya maize, One Acre Fund MEL Agronomic Survey 2016–2020.**

This is the second of the two consumer surfaces in CRISP-DM report §6.1: given
a plot, a ranked list of input and practice changes with an expected yield lift
per change. It is served by `POST /api/v1/recommend`
([api_reference.md §3](api_reference.md)) and produced by
`scripts/build_lever_curves.py`.

The short version: **the advice does not come from the yield model.** It comes
from a separate set of fixed-effects response curves, one per controllable
decision, fitted on all 23,617 plots. §1 explains why, because that departure
from the report's stated plan is the single most important thing in this
document.

---

## 1 · Why not a search over the yield model

Report §4.1 specifies the recommendation layer as *"not a separate ML model — a
constrained search/optimization over the trained GBM's predicted response
surface."* That is a reasonable plan written before any model existed. Two
findings from the modelling notebooks make it the wrong instrument here:

1. **Per-plot accuracy is weak.** Out-of-time per-plot R² is ~0.16. The model
   was validated at the district level (R² 0.22–0.59, MAE 300–700 kg/ha), and
   the deployment guide is explicit that per-plot estimates are indicative only.
2. **Most of what accuracy there is, is geography.** Notebook 03 §5: shuffling
   `district` costs ~0.077 R² on the holdout, roughly **60% of the model's
   entire out-of-time performance**. The model mostly knows the baseline
   productivity of ~51 places. That is a real capability, and it is not agronomy.

A constrained optimiser walks the *local* gradient of that surface for one plot.
On a surface where per-plot signal is 0.16 and most of it is a district effect
that no farmer can change, the walk returns a confident-looking bundle assembled
largely from noise.

**The estimand is different from the model's.** Advice does not need "what will
this plot yield". It needs "how does yield move when this decision moves" — an
average gradient, not a point prediction. Notebook 03 §6 already made the
argument and demonstrated it:

> *"predicting an individual plot's yield is hard, but estimating the average
> gradient of yield against a decision variable, with 21,000 observations and
> fixed effects, is a much easier statistical problem."*

That section quantified the planting-date penalty, showed it survived controls
for inputs and wealth, and priced the population-level opportunity at ~+98 kg/ha
on average and ~+250 kg/ha for the third of plots sown after March 31. This
layer generalises that method to every ex-ante lever the survey measures.

The choice has an operational dividend: the curve artefact is 37 KB and
committed, so `/recommend` answers on a deployment whose 125 MB
`pipeline.joblib` never arrived.

---

## 2 · The estimator

For each lever, on all seasons:

```
y_ipt  =  f(lever_ipt)  +  b'C_ipt  +  a_pt  +  e_ipt          clustered on district p
```

| Term | What it is, and why |
|---|---|
| `a_pt` | **District × season fixed effect**, absorbed by within-transformation (180 non-empty cells). Doing the heavy lifting: notebook 03 §2 showed the strongest raw correlates of yield in this file are all temperature at *r* ≈ −0.33, collapsing to −0.06 within a district — they are altitude proxies. Comparing a plot only against others in the same place and the same year removes both the geography and the season label. |
| `f(·)` | **Linear spline** — an indicator for any use, a linear term, and a hinge at each knot (knots at quantiles of the positive values, so a zero-inflated column does not stack every knot at zero). Diminishing returns are *fitted*, not imposed, which is what makes the §1.3 plausibility criterion checkable rather than assumed. |
| `C` | **The other levers**, plus plot size, wealth index, cattle and household size. Every coefficient is therefore *ceteris paribus*, which is what permits the API to add selected recommendations into a bundle without counting the same nitrogen twice. |
| cluster | **District** — 51 clusters. Plots in one place are not independent draws, and an unclustered standard error would be roughly twice too narrow. |

The API stores coefficients and their full covariance, so the standard error of
*any* contrast `f(to) − f(from)` is computed exactly rather than approximated.

### Choosing the levers

The feature file carries 45 ex-ante lever columns, but most are algebraic
restatements of one another: `n_kg_ph`, `p2o5_kg_ph` and `total_nutrient_kg_ph`
are near-deterministic functions of the fertiliser columns by construction
(`build_features.py` §4), and `uses_hybrid_seed` is `hybrid_seed_share`
thresholded. Advising on all of them would credit one bag of DAP three times.

The eight levers below are chosen to **span** the farmer's decision space
without **overlapping** it — each is something separately bought or separately
decided.

---

## 3 · The fitted curves

Lift in kg/ha, relative to each curve's own best value. `absorbed` is the share
of the raw association the covariates take away — the visible part of "who uses
this input" rather than the input.

| Lever | Column | n | Full range | Shape | Absorbed |
|---|---|---|---|---|---|
| Planting date | `plant_date_doy` | 20,862 | −351 | interior peak | 31% |
| DAP at planting | `dap_kg_ph_winsorized` | 23,523 | +454 | interior peak | 50% |
| CAN topdress | `can_kg_ph_winsorized` | 23,523 | +530 | interior peak | 44% |
| Hybrid seed share | `hybrid_seed_share` | 22,809 | +617 | concave increasing | — |
| Seed variety | `seed_type_primary` | 22,665 | +634 | categorical | 34% |
| Compost | `comp_wb_pa_winsorized` | 23,543 | +379 | interior peak | 39% |
| Lime | `lime_kg_ph_winsorized` | 23,482 | +673 | interior peak | 41% |
| Intercropping | `intercrop` | 23,523 | +6 | binary | — |

### Planting date — the free lever

```
DOY  32    47    62    77    92   107   122
     -3    -1     0   -41  -110  -204  -293   kg/ha vs. the best date
```

Flat from early February to roughly **day 62–75**, then a monotone penalty with
no recovery. This reproduces notebook 03 §6 closely (−293 at day 122 here, ~−300
there under the same controls) on an independent implementation, which is the
main external check that the estimator is doing what it claims. The modal
planting date in the data is day 70–80 — on the shoulder of the plateau, not in
it — and roughly a third of plots are sown after day 90. **This costs nothing
to change and is the layer's most defensible recommendation.**

### The two fertilisers — diminishing returns, fitted

```
DAP   kg/ha     0     52    104    156    208    260    311
              -447  -304    -82    -29    -86   -143   -200

CAN   kg/ha     0     49     99    148    198    247    297
              -529  -247    -81    -30    -91   -152   -213
```

Both rise steeply from zero, flatten by ~120–150 kg/ha, and turn slightly down
beyond it. The API's plateau rule exploits exactly this: among targets
statistically indistinguishable from the best (within 50 kg/ha), it recommends
the *smallest change*, so the advice is ~120 kg/ha rather than the 310 at the
top of the fitted range. That the curves flatten where agronomy says they should —
without a functional form imposing it — is the machine-checkable form of the
§1.3 plausibility criterion.

Note also that both curves' largest single step is from **zero to some**, which
matches notebook 03 §2's finding that the binary "did they topdress at all" is
as informative as the continuous nitrogen rate.

### Hybrid seed — the largest lever

Linear from 0 to 1, **+617 kg/ha** end to end, *t* ≈ 24, with 29% absorbed by
controls. The strongest within-district correlate in the file. 76% of plots
already sit at share 1.0, so for most requests this lever correctly returns no
recommendation — its value is in identifying the 16% using no hybrid seed at all.

### Where the layer refuses to speak

**Intercropping**: +6 kg/ha, *t* = 0.24. Never recommended. This is a result —
the practice is free to change and the data says it does not matter for yield —
not a gap.

**Lime**: fitted range +673, and the curve peaks at ~74 kg/ha. But only **4% of
plots apply any**, so that optimum sits in a region holding ~100 plots. The
support gate (§4) refuses targets whose neighbourhood is thin, and raising
`min_support` to 2,000 retires lime entirely. It is also the widest interval in
the set, and is reported at `confidence: "low"` so a caller can drop it.

---

## 4 · Three gates between a curve and a recommendation

Each exists because of a specific way this dataset misleads.

| Gate | Rule | Why |
|---|---|---|
| **Support** | The target needs ≥ 200 plots (configurable) observed near it. | Only 4% of plots apply lime and 16% any compost. A fitted optimum in a thin region has a confident-looking standard error and is a shape drawn through noise. |
| **Evidence** | Lift ≥ 25 kg/ha *and* one-sided *t* ≥ 1.645 against the clustered SE. | "Do nothing" must be an available answer, and for a plot already at its district's optimum it is the right one. |
| **Domain** | Targets clamped to the fitted 1st–99th percentile. | No recommendation is ever an extrapolation. |

Then **selection is a plateau rule, not an argmax**: among targets
statistically indistinguishable from the best, the one closest to what the plot
already does wins. This is what makes the output read as agronomy rather than as
optimiser output — the DAP curve is flat from ~120 kg/ha onward, so the advice is
120, not the 346 at the top of the fitted range.

---

## 5 · No money, on purpose

Everything this layer produces is in kilograms of maize per hectare. There is
no costing, no prices and no budget.

Report §1.4 is explicit that this workbook carries **no fertiliser price and no
farm-gate crop price**, and that Project 1's recommendation layer should stay in
yield units rather than quietly depending on price assumptions nobody supplied.
Ranking advice by "yield per shilling" would have required inventing those
numbers, and an invented price does not stay in its box: it reorders the whole
list. The cheapest lever in this dataset is lime, whose curve rests on the 4% of
plots that apply any — a price-led ranking puts the least-supported estimate at
the top, which is precisely the failure a field agent would notice first.

So the ranking is by expected lift, the gates in §4 decide what is offered at
all, and the arithmetic of affordability is left to the person who actually
knows local prices. Converting a lift into a return is Project 3's scope, which
is where the report puts it.

---

## 6 · Limitations

**These are associations, not trial effect sizes.** Farmers choose these inputs;
nobody randomised them. District × season effects absorb the place-and-year part
of that selection and the covariates absorb its visible farmer-level part — but
not its unobserved part. A farmer who plants late may be doing so *because* the
rains were late on their own farm. Every recommendation therefore reports the
same contrast fitted **without** controls, so the size of the concern is visible
rather than assumed away; it runs 31–50% across levers.

**Lifts are additive by construction, and that is weakest where it matters
most.** Each curve is fitted holding the others fixed, so the model's own claim
is that effects add. For a plot starting near zero on several levers at once the
bundle can total 80%+ of the district's mean yield — arithmetically what the
curves say, but no plot in the survey was observed making all those changes
together. The API warns whenever a bundle exceeds 50% of the district's observed
mean yield. Read it as the size of the opportunity, not as a forecast.

**One national curve per lever.** Notebook 03 §6 showed the planting-date
optimum varies by district. The curves here are national, with district × season
effects removed but no district-specific *slopes*. A district-varying curve is
the most valuable extension available and is limited by per-district sample size,
not by method.

**Response, not ROI.** Everything is in kg/ha, and §5 explains why that line is
drawn where it is. Converting to profit needs farm-gate maize prices this
workbook does not carry — Project 3's scope.

**Unvalidated as a credit signal.** Report §1.3 asks whether plots flagged
mid-season show worse actual repayment. That needs loan and collections data not
present in this workbook. The credit-facing surface of §6.1 is *not* built, and
per the report's own instruction it should not ship until that check is run.

**Refit, do not carry forward.** Unlike `feature_defaults.json`, this artefact is
target-derived — it is a set of regression coefficients. Rerun
`scripts/build_lever_curves.py` whenever the feature file changes.

---

## 7 · Reproducing

```bash
python scripts/build_lever_curves.py        # ~1 s, pandas + numpy only
pytest tests/test_recommend.py              # 25 tests, no model artefact needed
```

The estimator is implemented directly in numpy (within-transformation, OLS via
`pinv`, clustered sandwich) rather than via statsmodels, so the script's
dependency footprint matches the rest of the repo. The spline basis lives in
`api/curves.py` and is imported by both the fitter and the server, so a curve
can never be evaluated differently from how it was fitted.
