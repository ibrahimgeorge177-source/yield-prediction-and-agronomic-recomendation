"""Turn one plot into a ranked list of changes worth making.

The pieces:

  api/features.py   resolves what this plot is doing NOW -- the caller's own
                    inputs where given, that district's median practice where
                    not. Reused wholesale, so a recommendation reads the request
                    exactly the way a prediction does.
  api/curves.py     the fitted response curve for each lever, and the exact
                    standard error of any contrast on it.
  here              choose a target on each curve, and gate it.

Everything is in kilograms of maize per hectare. There is no costing and no
budget: the CRISP-DM report (§1.4) records that this workbook carries no
fertiliser or farm-gate price data, and that Project 1's recommendation layer
must therefore stay in yield units rather than depending on price assumptions
nobody supplied. Ranking by expected lift is the whole of the ranking.

Three gates stand between a fitted curve and a recommendation, and each exists
because of a specific way this dataset lies:

  support   Only 4% of plots apply lime and 16% any compost, so the fitted
            optimum for those levers can sit in a region holding a few dozen
            plots. A target whose neighbourhood is thin is refused outright
            rather than quoted with its (confident-looking) standard error.
  evidence  The lift must clear both an absolute floor and a one-sided t-test
            against the district-clustered standard error. "Do nothing" is a
            legitimate answer and is returned as one.
  domain    Targets are clamped to the fitted 1st-99th percentile range, so no
            recommendation is an extrapolation.

Selection then applies a plateau rule rather than taking the argmax: among the
targets statistically indistinguishable from the best one (within 50 kg/ha),
the one closest to what the plot already does is chosen. This is what makes the
output read as agronomy rather than as optimiser output -- the DAP curve is flat
from ~120 kg/ha onward, so the recommendation is 120, not the 346 at the top of
the fitted range.
"""
from __future__ import annotations

import math
from typing import Any

from api import curves as C
from api.features import expand_plot

# Two targets whose lifts differ by less than this are treated as the same
# advice, and the smaller change wins. Mirrors the +/-50 kg/ha band notebook 03
# §6 used to define the favourable planting window.
PLATEAU_TOLERANCE_KG_PH = 50.0
MIN_SUPPORT = 200
Z_90 = 1.645


class Option:
    """One value a lever could be moved to, and what it buys."""

    __slots__ = ("value", "lift", "se", "support")

    def __init__(self, value: Any, lift: float, se: float, support: int) -> None:
        self.value, self.lift, self.se = value, lift, se
        self.support = support

    @property
    def t(self) -> float:
        return self.lift / self.se if self.se > 0 else 0.0


class LeverResult:
    """A lever after gating: either a recommendation, or a reason there is none."""

    def __init__(self, spec: dict, current: Any, assumed: bool) -> None:
        self.spec, self.current, self.assumed = spec, current, assumed
        self.options: list[Option] = []
        self.chosen: Option | None = None
        self.skipped: str | None = None

    @property
    def name(self) -> str:
        return self.spec["name"]


# ---------------------------------------------------------------------------
# where the plot is now
# ---------------------------------------------------------------------------
def current_value(spec: dict, row: dict, supplied: list[str], district: str | None,
                  lever_curves) -> tuple[Any, bool]:
    """This plot's present setting, and whether the caller actually told us.

    `assumed` is not a footnote. A lift computed against a district median the
    caller never confirmed is advice about the typical plot in that district,
    not about this one, and the response says so per recommendation.
    """
    column = spec["column"]
    value = row.get(column)
    assumed = column not in supplied
    if value is None or (isinstance(value, float) and math.isnan(value)):
        value = lever_curves.district_current(spec, district)
        assumed = True
    if spec["kind"] == "categorical":
        if str(value) not in spec["levels"]:
            value = lever_curves.district_current(spec, district)
            assumed = True
        return str(value), assumed
    return C.clamp(spec, float(value)), assumed


# ---------------------------------------------------------------------------
# gating and selection
# ---------------------------------------------------------------------------
def build_options(spec: dict, current: Any, *, min_lift: float, z: float,
                  min_support: int) -> LeverResult:
    result = LeverResult(spec, current, assumed=False)
    grid = C.candidates(spec)
    support = spec["support"]

    best_supported = max(support) if support else 0
    if best_supported < min_support:
        result.skipped = (f"no value of this lever has {min_support}+ comparable "
                          f"plots behind it (best is {best_supported})")
        return result

    for value, n in zip(grid, support):
        if n < min_support or value == current:
            continue
        lift, se = C.contrast(spec, value, current)
        if lift < min_lift or se <= 0 or lift / se < z:
            continue
        result.options.append(Option(value, lift, se, n))

    if not result.options:
        result.skipped = ("no change to this lever clears the evidence threshold "
                          f"({min_lift:.0f} kg/ha at t >= {z:g}) for this plot")
        return result

    # Plateau rule: among targets indistinguishable from the best, take the one
    # that asks the least of the farmer.
    best = max(o.lift for o in result.options)
    plateau = [o for o in result.options if o.lift >= best - PLATEAU_TOLERANCE_KG_PH]
    result.chosen = min(plateau, key=lambda o: _distance(spec, o.value, current))
    return result


def _distance(spec: dict, value: Any, current: Any) -> float:
    if spec["kind"] == "categorical":
        return 0.0 if value == current else 1.0
    return abs(float(value) - float(current))


def confidence(option: Option) -> str:
    if option.t >= 3.0 and option.support >= 1000:
        return "high"
    if option.t >= 2.0 and option.support >= 400:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def recommend(plot: dict, defaults, lever_curves, *, year: int | None = None,
              only: list[str] | None = None, min_lift: float = 25.0,
              z: float = Z_90, min_support: int = MIN_SUPPORT) -> dict:
    """Rank the changes available to one plot, by expected yield lift."""
    expansion = expand_plot(plot, defaults, year)
    row, supplied = expansion.row, expansion.supplied
    warnings = list(expansion.warnings)

    results: list[LeverResult] = []
    for spec in lever_curves.levers:
        if only and spec["name"] not in only:
            continue
        current, assumed = current_value(spec, row, supplied, expansion.district,
                                         lever_curves)
        result = build_options(spec, current, min_lift=min_lift, z=z,
                               min_support=min_support)
        result.assumed = assumed
        results.append(result)

    ranked = sorted((r for r in results if r.chosen),
                    key=lambda r: r.chosen.lift, reverse=True)

    if any(r.assumed for r in ranked):
        warnings.append(
            "some levers were compared against this district's median practice "
            "because the request did not say what the plot currently does; those "
            "recommendations describe a typical plot in the district, not this one")

    return {
        "expansion": expansion,
        "results": results,
        "ranked": ranked,
        "warnings": warnings,
    }
