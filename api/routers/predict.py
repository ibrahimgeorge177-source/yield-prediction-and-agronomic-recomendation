"""POST /api/v1/predict -- score plots and their district means."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, status

from api.config import get_settings
from api.defaults import DefaultsUnavailable, get_defaults
from api.features import expand_plot
from api.registry import ModelUnavailable, _ensure_scripts_importable, get_registry
from api.schemas import (DistrictPrediction, ErrorResponse, PlotPrediction, PredictionRequest,
                         PredictionResponse)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["prediction"])

ACCURACY_NOTE = (
    "District means are the unit this model was validated on: out-of-time R2 0.22-0.59 "
    "and MAE 300-700 kg/ha across seasons. Per-plot estimates are far weaker "
    "(R2 ~0.16) because most plot-to-plot variation comes from soil and management "
    "detail the survey does not capture; treat them as indicative, not as a farm "
    "forecast. See GET /api/v1/model/summary."
)


@router.post("/predict", response_model=PredictionResponse,
             responses={503: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
             summary="Predict maize yield for plots and their districts")
def predict(request: PredictionRequest) -> PredictionResponse:
    """Score one or more maize plots.

    Each plot is expanded into the 143 columns the model consumes: values the
    caller supplied are used directly, and the rest are filled from that
    district's own history (see api/features.py). Plots sharing a district are
    then averaged into the district mean the model was validated on, with the
    fitted prediction interval.
    """
    settings = get_settings()

    if len(request.plots) > settings.max_plots_per_request:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{len(request.plots)} plots exceeds the per-request limit of "
            f"{settings.max_plots_per_request}. Split the request.")

    try:
        defaults = get_defaults()
    except DefaultsUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    try:
        pipeline = get_registry().require()
    except ModelUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"the prediction model is not available: {exc}") from exc

    # --- expand every plot into a model row ---------------------------------
    expansions = [expand_plot(plot.model_dump(), defaults, request.year)
                  for plot in request.plots]

    frame = _build_frame([e.row for e in expansions], pipeline)

    try:
        plot_predictions = np.asarray(pipeline.predict_plots(frame), dtype=float)
    except Exception as exc:                       # a malformed row must not 500 silently
        log.exception("prediction failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"prediction failed: {type(exc).__name__}: {exc}") from exc

    lo, hi = pipeline.recipe.clip
    plot_predictions = np.clip(plot_predictions, lo, hi)

    districts = _aggregate(frame, plot_predictions, expansions, pipeline, request.min_plots)

    plots: list[PlotPrediction] = []
    if request.include_plot_predictions:
        plots = [
            PlotPrediction(
                plot_id=source.plot_id,
                index=i,
                district=exp.district,
                district_known=exp.district_known,
                year=exp.year,
                predicted_yield_kg_ph=round(float(pred), 1),
                inputs_supplied=exp.supplied,
                inputs_supplied_count=len(exp.supplied),
                used_season_weather=exp.season_weather,
                warnings=exp.warnings,
            )
            for i, (source, exp, pred)
            in enumerate(zip(request.plots, expansions, plot_predictions))
        ]

    warnings = _request_warnings(expansions, districts)

    return PredictionResponse(
        model_version=settings.model_version,
        n_plots_scored=len(request.plots),
        districts=districts,
        plots=plots,
        warnings=warnings,
        accuracy_note=ACCURACY_NOTE,
    )


def _build_frame(rows: list[dict], pipeline) -> pd.DataFrame:
    """Assemble the DataFrame the pipeline expects.

    The pipeline carries the schema it was fitted with, so the column list comes
    from the artefact rather than from the defaults file -- they agree today,
    and if a retrained model changes the feature set the artefact wins.
    """
    schema = pipeline.schema
    columns = list(dict.fromkeys([*schema.feats, *schema.weather, "district", "year"]))
    frame = pd.DataFrame(rows).reindex(columns=columns)

    categorical = set(schema.cats)
    for column in frame.columns:
        if column in categorical:
            frame[column] = frame[column].astype(object).astype("string").astype("category")
        else:
            # Anything left as object (an all-null column, say) must be numeric
            # before the imputer sees it.
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _aggregate(frame: pd.DataFrame, predictions: np.ndarray, expansions: list,
               pipeline, min_plots_override: int | None) -> list[DistrictPrediction]:
    """District means with the pipeline's fitted intervals.

    `aggregate_districts` is reused from the training code so the API averages
    exactly the way the model was validated. It is called with min_plots=1: a
    small request still gets its aggregate, flagged rather than dropped.
    """
    _ensure_scripts_importable()
    from district_model import aggregate_districts        # noqa: PLC0415

    threshold = min_plots_override or getattr(pipeline, "min_plots", 20) or 20

    table = aggregate_districts(frame, predictions, min_plots=1, with_actual=False)
    lo, hi = pipeline.intervals_.band(table.pred_mean.to_numpy(), table.n_plots.to_numpy())
    clip_lo, clip_hi = pipeline.recipe.clip
    sd = pipeline.intervals_.sd(table.n_plots.to_numpy())

    known = {e.district: e.district_known for e in expansions}
    out = []
    for i, record in enumerate(table.to_dict("records")):
        district = str(record["district"])
        year = record.get("year")
        out.append(DistrictPrediction(
            district=district,
            year=int(year) if year is not None and not pd.isna(year) else None,
            n_plots=int(record["n_plots"]),
            predicted_mean_yield_kg_ph=round(float(record["pred_mean"]), 1),
            interval_low_kg_ph=round(float(max(lo[i], clip_lo)), 1),
            interval_high_kg_ph=round(float(min(hi[i], clip_hi)), 1),
            interval_sd_kg_ph=round(float(sd[i]), 1),
            interval_coverage=float(pipeline.intervals_.coverage_target),
            below_reporting_threshold=int(record["n_plots"]) < threshold,
            district_known=known.get(district, False),
        ))
    return sorted(out, key=lambda d: d.predicted_mean_yield_kg_ph, reverse=True)


def _request_warnings(expansions: list, districts: list[DistrictPrediction]) -> list[str]:
    warnings: list[str] = []

    unknown = sorted({e.district for e in expansions if not e.district_known})
    if unknown:
        warnings.append(
            f"{len(unknown)} district(s) were not in the model's training data "
            f"({', '.join(unknown[:5])}{'...' if len(unknown) > 5 else ''}). Measured "
            "level error for unseen districts is about twice that of known ones.")

    thin = [d.district for d in districts if d.below_reporting_threshold]
    if thin:
        warnings.append(
            f"{len(thin)} district mean(s) rest on fewer plots than the model's "
            "reporting threshold and are largely sampling noise: "
            f"{', '.join(thin[:5])}{'...' if len(thin) > 5 else ''}.")

    climatology = sum(1 for e in expansions if e.district_known and not e.season_weather)
    if climatology:
        warnings.append(
            f"{climatology} plot(s) had no observed weather for their season; "
            "district climatological averages were used instead.")
    return warnings
