"""GET /api/v1/model/summary -- what the model is and how well it does.

The figures are not hard-coded: training seasons, ensemble members, per-season
validation metrics and the interval parameters all come from the artefact's
metadata.json, so a retrained model reports its own numbers. Only the
qualitative limits are prose, quoted from
Documentation/kenya_maize_district_model_deployment.md.
"""
from __future__ import annotations

from fastapi import APIRouter

from api.config import get_settings
from api.defaults import DefaultsUnavailable, get_defaults
from api.registry import get_registry
from api.schemas import IntervalSummary, ModelSummaryResponse, SeasonMetrics

router = APIRouter(prefix="/api/v1/model", tags=["model"])

# Documentation/kenya_maize_district_model_deployment.md §5.
LIMITATIONS = [
    "Districts the model never saw in training carry roughly twice the level error "
    "(MAE 543 vs 271 kg/ha on the 2020 test season). Their ranking is still useful.",
    "Per-plot predictions are weak (R2 ~0.16) and deliberately shrunk toward the mean. "
    "Most plot-to-plot variance comes from soil and management detail the survey does "
    "not measure.",
    "A district mean backed by fewer than the reporting threshold of plots is mostly "
    "sampling noise.",
    "The model converts a season's plot-level inputs into a yield estimate. It cannot "
    "forecast a district from its history alone -- that route scored R2 = -1.32.",
    "Season effects dominate model choice. Accuracy in any single season can land "
    "anywhere in the measured 0.22-0.59 band.",
    "Prediction intervals are conservative: the nominal 80% band covered 92% of "
    "districts in 2020. Read them as a rarely-wrong bound, not a sharp 80% interval.",
]

EXPECTED_PERFORMANCE = {
    "unit": "district-season mean yield, kg/ha",
    "r2_range": [0.22, 0.59],
    "mae_range_kg_ph": [300, 700],
    "evaluation": "out-of-time: fitted on earlier seasons only, scored on a season "
                  "never seen during training",
    "accuracy_by_district_size_2020": [
        {"min_plots": 30, "n_districts": 50, "r2": 0.586, "mae_kg_ph": 329},
        {"min_plots": 50, "n_districts": 47, "r2": 0.607, "mae_kg_ph": 301},
        {"min_plots": 100, "n_districts": 29, "r2": 0.661, "mae_kg_ph": 281},
    ],
    "note": "The R2 >= 0.70 target is not reachable with the present survey data. "
            "District R2 is bounded by corr^2, and the feature-to-yield correlation "
            "runs 0.56-0.77 across seasons. Closing the gap needs better inputs -- "
            "soil tests, GPS-verified plot area, crop-cut yields -- not a better model.",
}


@router.get("/summary", response_model=ModelSummaryResponse,
            summary="Model performance, training seasons and stated limits")
def model_summary() -> ModelSummaryResponse:
    """Summarise the deployed model.

    Works whether or not the weights are loaded: the metrics live in the
    metadata.json committed beside the artefact, so a deployment still waiting
    on its model file can report what it is meant to be serving.
    """
    settings = get_settings()
    registry = get_registry()
    metadata = registry.metadata()
    artefact_status = registry.status()

    if registry.is_loaded:
        state = "ready"
    elif metadata:
        state = "not_loaded"
    else:
        state = "unavailable"

    validation = [
        SeasonMetrics(
            season=int(season),
            n_districts=metrics.get("n_districts"),
            r2=_round(metrics.get("r2")),
            r2_weighted=_round(metrics.get("r2_weighted")),
            mae_kg_ph=_round(metrics.get("mae"), 1),
            rmse_kg_ph=_round(metrics.get("rmse"), 1),
            corr=_round(metrics.get("corr")),
            bias_kg_ph=_round(metrics.get("bias"), 1),
        )
        for season, metrics in sorted((metadata.get("validation") or {}).items())
    ]

    interval = None
    spec = metadata.get("interval") or {}
    if spec:
        interval = IntervalSummary(
            coverage_target=float(spec.get("coverage_target", 0.8)),
            a=float(spec.get("a", 0.0)),
            b=float(spec.get("b", 0.0)),
            description="District-mean error is modelled as sd(n) = sqrt(a + b/n), so "
                        "districts backed by fewer plots get wider bands.",
        )

    headline = {}
    if validation:
        best = max(validation, key=lambda m: m.r2 if m.r2 is not None else -99)
        headline = {"most_recent_validation_season": validation[-1].season,
                    "most_recent_r2": validation[-1].r2,
                    "most_recent_mae_kg_ph": validation[-1].mae_kg_ph,
                    "best_validation_season": best.season,
                    "best_r2": best.r2}

    try:
        defaults = get_defaults()
        defaults_info = {"available": True,
                         "generated_at": defaults.generated_at,
                         "districts": len(defaults.districts),
                         "seasons": defaults.years,
                         "model_columns": len(defaults.model_columns),
                         "source_rows": defaults.source.get("rows")}
    except DefaultsUnavailable as exc:
        defaults_info = {"available": False, "detail": str(exc)}

    return ModelSummaryResponse(
        model_version=settings.model_version,
        status=state,
        prediction_unit="kg/ha",
        target="district-season mean maize yield",
        train_seasons=metadata.get("train_years", []),
        validation_seasons=metadata.get("val_years", []),
        n_training_plots=metadata.get("n_train_plots"),
        ensemble_members=metadata.get("members", []),
        min_plots_for_reporting=metadata.get("min_plots"),
        validation=validation,
        headline=headline,
        interval=interval,
        expected_performance=EXPECTED_PERFORMANCE,
        limitations=LIMITATIONS,
        artefact=artefact_status,
        feature_defaults=defaults_info,
    )


def _round(value, digits: int = 3):
    return None if value is None else round(float(value), digits)
