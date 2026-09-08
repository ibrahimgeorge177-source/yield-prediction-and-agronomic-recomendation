"""POST /api/v1/recommend -- ranked agronomic advice for one plot.

This is the field-agent surface of CRISP-DM report §6.1: "predicted yield plus
a ranked list of recommended input/practice changes with expected yield lift
per change." The lift comes from the fitted lever curves rather than from the
yield model (see api/recommend.py for why), which has a useful operational
consequence -- the endpoint answers on a deployment whose 125 MB model artefact
has not arrived. The baseline prediction is the only part that needs it, and it
is omitted with a warning rather than turning the call into a 503.

Everything is expressed in kilograms of maize per hectare. There is no costing
and no budget, per report §1.4: this workbook carries no price data, and advice
built on prices nobody supplied would be a guess wearing a number.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from api.config import get_settings
from api.curves import CurvesUnavailable, contrast_under, curve_points, get_curves
from api.defaults import DefaultsUnavailable, get_defaults
from api.recommend import Z_90, confidence, recommend
from api.registry import ModelUnavailable, get_registry
from api.schemas import (ErrorResponse, LeverEvidence, Recommendation,
                         RecommendationBundle, RecommendationRequest,
                         RecommendationResponse, SkippedLever)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["recommendation"])

METHOD_NOTE = (
    "Each lift is read off that lever's own response curve: a linear spline in "
    "the lever, fitted across all seasons with district x season fixed effects "
    "absorbed, the other levers and the household covariates held fixed, and "
    "standard errors clustered on district. Comparing a plot only against other "
    "plots in the same place and the same season is what removes the altitude "
    "and year effects that dominate raw correlations here. This is deliberately "
    "not an optimisation over the yield model: per-plot R2 is ~0.16, but the "
    "average gradient of yield against a decision is estimable from ~21,000 "
    "plots, and that gradient is what advice needs."
)

CAUSAL_NOTE = (
    "Associations, not trial effect sizes. Farmers choose these inputs; nobody "
    "randomised them. District x season effects absorb the place-and-year part "
    "of that selection and the covariates absorb its visible farmer-level part, "
    "but not its unobserved part -- a farmer planting late may be doing so "
    "because the rains were late on their own farm. Each recommendation reports "
    "the same contrast fitted without controls so the size of that concern is "
    "visible rather than assumed away."
)


@router.post("/recommend", response_model=RecommendationResponse,
             responses={503: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
             summary="Rank the changes available to one plot")
def recommend_endpoint(request: RecommendationRequest) -> RecommendationResponse:
    try:
        defaults = get_defaults()
    except DefaultsUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    try:
        curves = get_curves()
    except CurvesUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    if request.levers:
        unknown = sorted(set(request.levers) - set(curves.names()))
        if unknown:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"unknown lever(s) {', '.join(unknown)}. "
                f"Known levers: {', '.join(curves.names())}.")

    settings = get_settings()
    outcome = recommend(
        request.plot.model_dump(), defaults, curves,
        year=request.year, only=request.levers,
        min_lift=max(request.min_lift_kg_ph, settings.min_lift_kg_ph),
        z=settings.min_lift_z, min_support=request.min_support)

    expansion = outcome["expansion"]
    ranked = outcome["ranked"]
    warnings = outcome["warnings"]
    district_yield = curves.reference_mean_yield(expansion.district)

    recommendations = [
        _render(rank, result, district_yield, request.include_curve)
        for rank, result in enumerate(ranked, start=1)
    ]
    skipped = [
        SkippedLever(lever=r.name, label=r.spec["label"], current_value=r.current,
                     reason=r.skipped)
        for r in outcome["results"] if r.skipped
    ]

    bundle = _bundle(ranked, district_yield, warnings)

    baseline = _baseline(request, warnings)
    projected = (round(baseline + bundle.total_expected_lift_kg_ph, 1)
                 if baseline is not None else None)

    return RecommendationResponse(
        curves_version=curves.generated_at,
        district=expansion.district,
        district_known=expansion.district_known,
        year=expansion.year,
        plot_inputs_supplied=expansion.supplied,
        baseline_predicted_yield_kg_ph=baseline,
        projected_yield_kg_ph=projected,
        recommendations=recommendations,
        bundle=bundle,
        skipped=skipped,
        warnings=warnings,
        method_note=METHOD_NOTE,
        causal_note=CAUSAL_NOTE,
    )


def _render(rank: int, result, district_yield: float | None,
            include_curve: bool) -> Recommendation:
    spec, option = result.spec, result.chosen
    diagnostics = spec["diagnostics"]
    increase = (spec["kind"] == "categorical"
                or float(option.value) >= float(result.current))
    action = spec["action"]["more" if increase else "less"]
    if spec["kind"] == "categorical":
        action = f"{action} to {option.value}"
    elif spec["unit"]:
        action = f"{action} — {option.value:g} {spec['unit']} (now {result.current:g})"

    to, frm = option.value, result.current
    return Recommendation(
        rank=rank,
        lever=spec["name"],
        label=spec["label"],
        action=action,
        current_value=result.current,
        recommended_value=option.value,
        unit=spec["unit"],
        current_is_assumed=result.assumed,
        expected_lift_kg_ph=round(option.lift, 1),
        lift_low_kg_ph=round(option.lift - Z_90 * option.se, 1),
        lift_high_kg_ph=round(option.lift + Z_90 * option.se, 1),
        lift_share_of_district_yield=(round(option.lift / district_yield, 3)
                                      if district_yield else None),
        why=spec["why"],
        evidence=LeverEvidence(
            n_plots=spec["n"],
            n_districts=spec["n_clusters"],
            support_at_target=option.support,
            confidence=confidence(option),
            t_statistic=round(option.t, 2),
            uncontrolled_lift_kg_ph=_round(contrast_under(spec, "coefs_uncontrolled",
                                                          to, frm)),
            control_absorbed_share=_absorbed(
                option.lift, contrast_under(spec, "coefs_uncontrolled", to, frm)),
            trained_to_2019_lift_kg_ph=_round(contrast_under(spec, "coefs_to2019",
                                                            to, frm)),
            curve_shape=diagnostics["shape"],
            agronomically_plausible=diagnostics["agronomic_check"]["passed"],
        ),
        curve=([{"value": x, "lift_vs_best_kg_ph": round(y, 1)}
                for x, y in curve_points(spec)] if include_curve else None),
    )


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _absorbed(lift: float, uncontrolled: float | None) -> float | None:
    """Share of the raw association the covariates take away. Near 1 means the
    lever was mostly standing in for who uses it."""
    if uncontrolled is None or abs(uncontrolled) < 1e-9:
        return None
    return round(1 - lift / uncontrolled, 3)


def _bundle(ranked: list, district_yield: float | None,
            warnings: list[str]) -> RecommendationBundle:
    """Sum the recommended lifts.

    Additive because the curves are: every lever is fitted holding the others
    fixed, so the model's own claim is that the effects add. That claim is
    weakest for changes made together at the top of two curves, which the note
    and the scale warning say out loud rather than burying.
    """
    options = [r.chosen for r in ranked]
    total = sum(o.lift for o in options)
    # Independent standard errors would understate the band; these estimates
    # share a sample, so the conservative sum of half-widths is used.
    half = Z_90 * sum(o.se for o in options)

    if not options:
        note = ("No change to this plot clears the evidence threshold. That is a "
                "result, not a gap: on the levers measured here this plot is "
                "already near the fitted optimum for its district.")
    else:
        note = (f"All {len(options)} changes that clear the evidence threshold, "
                "ranked by expected lift. Lifts are added because each curve is "
                "fitted holding the other levers fixed. Everything is in kg/ha; "
                "converting to money needs prices this survey does not carry.")

    share = total / district_yield if district_yield else None
    if share and share > 0.5:
        warnings.append(
            f"the bundle's total lift is {share:.0%} of {district_yield:,.0f} kg/ha, "
            "this district's observed mean yield. Arithmetically that is what the "
            "curves say for a plot starting near zero on several levers at once, "
            "but the additivity behind it is least reliable exactly there, and no "
            "plot in the survey was observed making all these changes together. "
            "Treat it as the size of the opportunity, not as a forecast.")

    return RecommendationBundle(
        levers=[r.name for r in ranked],
        total_expected_lift_kg_ph=round(total, 1),
        lift_low_kg_ph=round(total - half, 1),
        lift_high_kg_ph=round(total + half, 1),
        lift_share_of_district_yield=round(share, 3) if share else None,
        district_mean_yield_kg_ph=district_yield,
        note=note,
    )


def _baseline(request: RecommendationRequest, warnings: list[str]) -> float | None:
    """The yield model's estimate for the plot as described, when it is loadable.

    Never fatal. The recommendation layer is deliberately independent of the
    artefact, and a deployment without it should still be able to advise.
    """
    registry = get_registry()
    if not registry.is_loaded and not registry.status()["artefact_present"]:
        warnings.append(
            "the yield model artefact is not present, so no baseline yield is "
            "reported; the recommendations themselves do not depend on it")
        return None
    try:
        from api.routers.predict import predict as run_predict       # noqa: PLC0415
        from api.schemas import PredictionRequest                    # noqa: PLC0415

        response = run_predict(PredictionRequest(
            plots=[request.plot], year=request.year,
            include_plot_predictions=True, min_plots=1))
        return response.plots[0].predicted_yield_kg_ph if response.plots else None
    except (ModelUnavailable, HTTPException) as exc:
        warnings.append(f"no baseline yield: the model could not be scored ({exc})")
        return None
    except Exception as exc:                        # never fail the advice on this
        log.warning("baseline prediction failed: %s", exc)
        warnings.append(f"no baseline yield: {type(exc).__name__}")
        return None
