"""Request and response models.

The prediction request is deliberately *lean*: it asks only for what a farmer
or extension officer knows. Every field except `district` is optional, and an
omitted field is filled from the district's own history rather than rejected
(see api/features.py). This keeps the contract usable by a frontend while the
model still receives the 143 columns it was trained on.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SeedCategory = Literal["hybrid_branded", "other_hybrid", "local", "mixed"]


# ---------------------------------------------------------------------------
# prediction request
# ---------------------------------------------------------------------------
class PlotInput(BaseModel):
    """One maize plot for a single season."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plot_id: str | None = Field(
        None, max_length=64,
        description="Caller's own identifier, echoed back on the matching result.")
    district: str = Field(
        ..., min_length=1, max_length=64,
        description="Kenyan district. Case-insensitive; "
                    "GET /api/v1/reference/districts lists the known values.")
    year: int | None = Field(
        None, ge=2000, le=2100,
        description="Season. Defaults to the most recent season in the training data. "
                    "A season with no observed weather falls back to district climatology.")

    # --- plot ---------------------------------------------------------------
    plot_acres: float | None = Field(None, ge=0, le=1000, description="Plot size in acres.")

    # --- seed ---------------------------------------------------------------
    seed_category: SeedCategory | None = Field(
        None, description="Coarse seed choice. Use this when the exact variety is unknown.")
    seed_type: str | None = Field(
        None, max_length=120,
        description="Exact variety, e.g. 'h_614d'. "
                    "GET /api/v1/reference/seed-types lists the vocabulary.")
    hybridseed_kg_ph: float | None = Field(None, ge=0, le=20000,
                                           description="Hybrid seed, kg per hectare.")
    localseed_kg_ph: float | None = Field(None, ge=0, le=20000,
                                          description="Local seed, kg per hectare.")

    # --- fertiliser, kg per hectare -----------------------------------------
    # Bounds are set above the largest value in the survey rather than at an
    # agronomic ceiling: the raw columns legitimately carry entry errors (DAP
    # reaches 45,922 kg/ha), and the pipeline winsorizes them at 500 kg/ha the
    # way training did. Anything over a cap is clipped and warned about, not
    # rejected -- a real survey row must never 422.
    dap_kg_ph: float | None = Field(None, ge=0, le=50000, description="DAP (18-46-0).")
    urea_kg_ph: float | None = Field(None, ge=0, le=50000, description="Urea (46-0-0).")
    can_kg_ph: float | None = Field(None, ge=0, le=50000,
                                    description="CAN (26-0-0), the usual topdress.")
    npk_kg_ph: float | None = Field(None, ge=0, le=50000,
                                    description="NPK blend, assumed 17-17-17.")
    lime_kg_ph: float | None = Field(None, ge=0, le=50000, description="Agricultural lime.")
    compost_wheelbarrows_per_acre: float | None = Field(
        None, ge=0, le=5000, description="Compost in wheelbarrows per acre, as surveyed.")

    # --- timing -------------------------------------------------------------
    plant_date: date | None = Field(None, description="Planting date; only the day of year is used.")
    plant_date_doy: float | None = Field(
        None, ge=1, le=366, description="Day of year, if the full date is unknown.")

    # --- intercropping ------------------------------------------------------
    intercrop: bool | None = Field(None, description="Whether the plot is intercropped.")
    intercrop_type: str | None = Field(
        None, max_length=80,
        description="Intercrop species, e.g. 'beans'. Legumes are treated distinctly.")

    # --- household ----------------------------------------------------------
    hh_num: float | None = Field(None, ge=0, le=100, description="Household size.")
    hh_num_under18: float | None = Field(None, ge=0, le=100,
                                         description="Household members under 18.")
    cows: float | None = Field(None, ge=0, le=10000, description="Cows owned.")
    owns_oxen: bool | None = None
    owns_electricity: bool | None = None

    @field_validator("district", "seed_type", "intercrop_type")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plots: list[PlotInput] = Field(
        ..., min_length=1,
        description="Plots to score. Plots sharing a district are aggregated together.")
    year: int | None = Field(
        None, ge=2000, le=2100,
        description="Default season for plots that do not carry their own.")
    include_plot_predictions: bool = Field(
        True, description="Return the per-plot estimates as well as the district means.")
    min_plots: int | None = Field(
        None, ge=1, le=10000,
        description="Override the district reporting threshold (default 20, from the "
                    "model's metadata). Districts below it are still returned, flagged.")


# ---------------------------------------------------------------------------
# prediction response
# ---------------------------------------------------------------------------
class PlotPrediction(BaseModel):
    plot_id: str | None = None
    index: int = Field(..., description="Position of this plot in the request.")
    district: str
    district_known: bool = Field(
        ..., description="False when the model never saw this district in training; "
                         "its level error is roughly twice as large.")
    year: int | None = None
    predicted_yield_kg_ph: float
    inputs_supplied: list[str] = Field(
        default_factory=list,
        description="Model columns derived from this request. Everything else was "
                    "filled from the district's history.")
    inputs_supplied_count: int = 0
    used_season_weather: bool = Field(
        ..., description="True when observed weather for that district-season was "
                         "available; False means climatological averages were used.")
    warnings: list[str] = Field(default_factory=list)


class DistrictPrediction(BaseModel):
    district: str
    year: int | None = None
    n_plots: int
    predicted_mean_yield_kg_ph: float
    interval_low_kg_ph: float
    interval_high_kg_ph: float
    interval_sd_kg_ph: float
    interval_coverage: float = Field(
        ..., description="Nominal coverage of the interval, e.g. 0.8.")
    below_reporting_threshold: bool = Field(
        ..., description="True when fewer plots than the model's threshold back this "
                         "mean, which makes it largely sampling noise.")
    district_known: bool


class PredictionResponse(BaseModel):
    model_version: str
    unit: str = "kg/ha"
    n_plots_scored: int
    districts: list[DistrictPrediction]
    plots: list[PlotPrediction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    accuracy_note: str


# ---------------------------------------------------------------------------
# model summary
# ---------------------------------------------------------------------------
class SeasonMetrics(BaseModel):
    season: int
    n_districts: int | None = None
    r2: float | None = None
    r2_weighted: float | None = None
    mae_kg_ph: float | None = None
    rmse_kg_ph: float | None = None
    corr: float | None = None
    bias_kg_ph: float | None = None


class IntervalSummary(BaseModel):
    coverage_target: float
    a: float
    b: float
    description: str


class ModelSummaryResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_version: str
    status: Literal["ready", "not_loaded", "unavailable"]
    prediction_unit: str
    target: str
    train_seasons: list[int] = Field(default_factory=list)
    validation_seasons: list[int] = Field(default_factory=list)
    n_training_plots: int | None = None
    ensemble_members: list[str] = Field(default_factory=list)
    min_plots_for_reporting: int | None = None
    validation: list[SeasonMetrics] = Field(default_factory=list)
    headline: dict[str, Any] = Field(default_factory=dict)
    interval: IntervalSummary | None = None
    expected_performance: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    artefact: dict[str, Any] = Field(default_factory=dict)
    feature_defaults: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# reference
# ---------------------------------------------------------------------------
class DistrictsResponse(BaseModel):
    count: int
    districts: list[str]
    seasons: list[int]


class SeedTypesResponse(BaseModel):
    seed_categories: list[str]
    seed_types: list[str]
    seed_types_primary: list[str]
    intercrop_types: list[str]


class FieldSpec(BaseModel):
    name: str
    type: str
    required: bool
    description: str | None = None
    unit: str | None = None
    allowed_values: list[str] | None = None
    observed_range: dict[str, float] | None = None


class InputSchemaResponse(BaseModel):
    fields: list[FieldSpec]
    note: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    model_loaded: bool
    defaults_loaded: bool
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# recommendation request
# ---------------------------------------------------------------------------
# Everything here is in kilograms of maize per hectare. There is deliberately no
# budget and no prices: the CRISP-DM report (§1.4) records that this workbook
# carries no fertiliser or farm-gate price data, and that Project 1's
# recommendation layer must stay in yield units rather than depending on price
# assumptions nobody supplied.
class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plot: PlotInput = Field(..., description="The plot to advise on. Same shape as "
                                             "a prediction request's plot.")
    year: int | None = Field(None, ge=2000, le=2100,
                             description="Season, if the plot does not carry one.")
    levers: list[str] | None = Field(
        None, description="Restrict to these levers. "
                          "GET /api/v1/reference/levers lists the names.")
    min_lift_kg_ph: float = Field(
        25, ge=0, le=2000,
        description="Ignore changes worth less than this. Raise it to see only "
                    "the changes that matter.")
    min_support: int = Field(
        200, ge=20, le=10000,
        description="Refuse any target with fewer comparable plots behind it than "
                    "this. Lowering it lets the curve speak where the data is thin.")
    include_curve: bool = Field(
        False, description="Return each lever's whole fitted response curve, for "
                           "plotting rather than for advice.")


# ---------------------------------------------------------------------------
# recommendation response
# ---------------------------------------------------------------------------
class LeverEvidence(BaseModel):
    """Why this number should or should not be believed."""

    n_plots: int = Field(..., description="Plots the curve was fitted on.")
    n_districts: int
    support_at_target: int = Field(
        ..., description="Plots observed near the recommended value. Thin support "
                         "is the main way a fitted optimum misleads.")
    confidence: Literal["high", "medium", "low"]
    t_statistic: float = Field(..., description="Lift over its district-clustered "
                                                "standard error.")
    uncontrolled_lift_kg_ph: float | None = Field(
        None, description="The same contrast with no covariates. The gap to the "
                          "headline is how much of the raw association is who "
                          "chooses the input rather than the input.")
    control_absorbed_share: float | None = None
    trained_to_2019_lift_kg_ph: float | None = Field(
        None, description="The same contrast refit on 2016-2019 only.")
    curve_shape: str
    agronomically_plausible: bool = Field(
        ..., description="Whether the fitted shape matches the agronomic prior "
                         "(CRISP-DM §1.3). Fitted, not imposed.")


class Recommendation(BaseModel):
    rank: int
    lever: str
    label: str
    action: str = Field(..., description="What to do, in plain words.")
    current_value: Any = Field(..., description="What the plot does now.")
    recommended_value: Any
    unit: str | None = None
    current_is_assumed: bool = Field(
        ..., description="True when the request did not say what the plot does and "
                         "the district's median practice was used instead.")
    expected_lift_kg_ph: float
    lift_low_kg_ph: float = Field(..., description="90% interval, district-clustered.")
    lift_high_kg_ph: float
    lift_share_of_district_yield: float | None = Field(
        None, description="The lift as a fraction of the district's observed mean "
                          "yield. The unit that says whether a number is large.")
    why: str
    evidence: LeverEvidence
    curve: list[dict[str, Any]] | None = None


class SkippedLever(BaseModel):
    lever: str
    label: str
    current_value: Any = None
    reason: str


class RecommendationBundle(BaseModel):
    """Every recommended change, taken together."""

    levers: list[str]
    total_expected_lift_kg_ph: float
    lift_low_kg_ph: float
    lift_high_kg_ph: float
    lift_share_of_district_yield: float | None = None
    district_mean_yield_kg_ph: float | None = None
    note: str


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    curves_version: str = Field(..., description="When the curves were fitted.")
    district: str
    district_known: bool
    year: int | None = None
    unit: str = "kg/ha"
    plot_inputs_supplied: list[str] = Field(default_factory=list)
    baseline_predicted_yield_kg_ph: float | None = Field(
        None, description="The yield model's estimate for the plot as described, "
                          "when the artefact is loaded. Recommendations do not "
                          "depend on it.")
    projected_yield_kg_ph: float | None = Field(
        None, description="Baseline plus the bundle's lift. Inherits the baseline's "
                          "per-plot uncertainty, which is large.")
    recommendations: list[Recommendation] = Field(default_factory=list)
    bundle: RecommendationBundle
    skipped: list[SkippedLever] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    method_note: str
    causal_note: str


# ---------------------------------------------------------------------------
# lever reference
# ---------------------------------------------------------------------------
class LeverSpec(BaseModel):
    name: str
    label: str
    column: str
    kind: Literal["continuous", "binary", "categorical"]
    unit: str | None = None
    n_plots: int
    typical_value: Any = None
    full_range_lift_kg_ph: float | None = None
    curve_shape: str
    agronomically_plausible: bool
    why: str


class LeversResponse(BaseModel):
    curves_version: str
    target: str
    unit: str
    method: str
    fitted_on: dict[str, Any] = Field(default_factory=dict)
    levers: list[LeverSpec]
