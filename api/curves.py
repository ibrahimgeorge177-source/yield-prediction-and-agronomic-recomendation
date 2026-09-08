"""Fitted lever response curves, and the maths for reading a lift off them.

The recommendation layer does not invert the yield model. Per-plot R2 is ~0.16
(Documentation/kenya_maize_district_model_deployment.md), so a constrained
search over that surface would produce confident-looking advice the model
cannot support. What the data *does* support is the average gradient of yield
against a decision variable: with ~21,000 plots and district x season fixed
effects, estimating that gradient is a far easier statistical problem than
predicting any single plot -- the argument made in notebook 03 §6, where it
produced the planting-window result.

So each controllable lever gets its own fixed-effects response curve, fitted
offline by `scripts/build_lever_curves.py` and stored in
data/api/lever_curves.json as:

  * a **linear-spline (hinge) basis** -- `1{x>0}`, `x`, `(x-k)+` for each knot --
    which is flexible enough for diminishing returns and cheap enough to
    evaluate here with numpy alone, and
  * the fitted coefficients with their **district-clustered covariance**, so the
    uncertainty of any contrast f(to) - f(from) is exact rather than assumed.

This module is the single definition of that basis: the fitting script imports
the same functions, so a curve can never be evaluated differently from how it
was fitted.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from api.config import get_settings


class CurvesUnavailable(RuntimeError):
    """The lever-curve artefact is missing or unreadable."""


# ---------------------------------------------------------------------------
# basis
# ---------------------------------------------------------------------------
def clamp(spec: dict, x: float) -> float:
    """Hold a value inside the fitted support.

    Extrapolation is the failure mode that matters here: the upper tail of
    every fertiliser column is a handful of plots, and an unclamped spline
    happily recommends 400 kg/ha of DAP off the back of twelve observations.
    """
    lo, hi = spec["basis"]["domain"]
    return float(min(max(float(x), lo), hi))


def basis(spec: dict, x: Any) -> np.ndarray:
    """Design row for one value of the lever."""
    kind = spec["kind"]

    if kind == "categorical":
        levels = spec["levels"]
        out = np.zeros(len(levels) - 1)
        label = str(x)
        if label in levels and label != spec["reference_level"]:
            out[levels.index(label) - 1] = 1.0
        return out

    value = clamp(spec, x)
    if kind == "binary":
        return np.array([value])

    b = spec["basis"]
    cols = [1.0 if value > 0 else 0.0] if b.get("zero_indicator") else []
    cols.append(value)
    cols.extend(max(0.0, value - k) for k in b["knots"])
    return np.asarray(cols, dtype=float)


def value_at(spec: dict, x: Any) -> float:
    """f(x), on the curve's own arbitrary origin. Only differences mean anything."""
    return float(basis(spec, x) @ np.asarray(spec["coefs"], dtype=float))


def contrast(spec: dict, to: Any, frm: Any) -> tuple[float, float]:
    """Expected lift of moving the lever from `frm` to `to`, and its standard error.

    The covariance is clustered on district, so the standard error already
    carries the fact that plots in one place are not independent draws.
    """
    d = basis(spec, to) - basis(spec, frm)
    coefs = np.asarray(spec["coefs"], dtype=float)
    cov = np.asarray(spec["cov"], dtype=float)
    var = float(d @ cov @ d)
    return float(d @ coefs), float(np.sqrt(max(var, 0.0)))


def contrast_under(spec: dict, key: str, to: Any, frm: Any) -> float | None:
    """The same contrast read off one of the lever's refits.

    `key` is "coefs_uncontrolled" (no covariates) or "coefs_to2019" (fitted on
    2016-2019 only). Computing them at the contrast actually being recommended,
    rather than at the population's median contrast, is what makes them
    comparable to the headline number they sit beside.
    """
    coefs = spec.get(key)
    if not coefs:
        return None
    d = basis(spec, to) - basis(spec, frm)
    return float(d @ np.asarray(coefs, dtype=float))


def candidates(spec: dict) -> list:
    """The values a recommendation is allowed to propose."""
    return list(spec["levels"]) if spec["kind"] == "categorical" else list(spec["grid"])


def curve_points(spec: dict) -> list[tuple[Any, float]]:
    """The whole response curve, centred on its own maximum, for display."""
    xs = candidates(spec)
    ys = [value_at(spec, x) for x in xs]
    top = max(ys)
    return [(x, y - top) for x, y in zip(xs, ys)]


# ---------------------------------------------------------------------------
# artefact
# ---------------------------------------------------------------------------
class LeverCurves:
    """Read-only view over data/api/lever_curves.json."""

    def __init__(self, payload: dict) -> None:
        self._d = payload
        self.levers: list[dict] = payload["levers"]
        self.by_name: dict[str, dict] = {l["name"]: l for l in self.levers}
        self.settings: dict = payload.get("settings", {})
        self.generated_at: str = payload.get("generated_at", "")
        self.source: dict = payload.get("source", {})
        self.method: str = payload.get("method", "")
        self.reference_yield: dict = payload.get("reference_yield", {})
        self.target: str = payload.get("target", "yield_kg_ph")

    def get(self, name: str) -> dict | None:
        return self.by_name.get(name)

    def names(self) -> list[str]:
        return [l["name"] for l in self.levers]

    def reference_mean_yield(self, district: str | None) -> float | None:
        """Observed mean yield for the district, for reading a lift as a share."""
        ref = self.reference_yield
        table = ref.get("by_district_kg_ph", {})
        if district and str(district).lower() in table:
            return table[str(district).lower()]
        return ref.get("national_mean_kg_ph")

    def district_current(self, lever: dict, district: str | None) -> Any:
        """What a typical plot in this district already does.

        Used only as a last resort: api/features.py already resolves the
        caller's own inputs and the district medians behind them.
        """
        table: dict = lever.get("district_current") or {}
        if district and str(district).lower() in table:
            return table[str(district).lower()]
        return lever["population"].get("median")


@lru_cache(maxsize=1)
def get_curves() -> LeverCurves:
    path: Path = get_settings().lever_curves_path
    if not path.exists():
        raise CurvesUnavailable(
            f"lever curves not found at {path}. "
            "Build them with: python scripts/build_lever_curves.py")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CurvesUnavailable(f"could not read lever curves at {path}: {exc}") from exc
    return LeverCurves(payload)


__all__ = ["CurvesUnavailable", "LeverCurves", "basis", "candidates", "clamp",
           "contrast", "contrast_under", "curve_points", "get_curves", "value_at"]
