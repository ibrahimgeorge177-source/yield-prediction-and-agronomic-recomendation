"""Reference endpoints.

A lean prediction request only works if the caller can discover the valid
district names and seed vocabulary, so these ship alongside /predict rather
than as an extra.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from api.curves import CurvesUnavailable, get_curves
from api.defaults import DefaultsUnavailable, get_defaults
from api.schemas import (DistrictsResponse, ErrorResponse, FieldSpec, InputSchemaResponse,
                         LeverSpec, LeversResponse, SeedTypesResponse)

router = APIRouter(prefix="/api/v1/reference", tags=["reference"])


def _defaults():
    try:
        return get_defaults()
    except DefaultsUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/districts", response_model=DistrictsResponse,
            responses={503: {"model": ErrorResponse}},
            summary="Districts the model was trained on")
def districts() -> DistrictsResponse:
    defaults = _defaults()
    return DistrictsResponse(count=len(defaults.districts),
                             districts=defaults.districts,
                             seasons=defaults.years)


@router.get("/seed-types", response_model=SeedTypesResponse,
            responses={503: {"model": ErrorResponse}},
            summary="Seed and intercrop vocabularies")
def seed_types() -> SeedTypesResponse:
    defaults = _defaults()
    return SeedTypesResponse(
        seed_categories=defaults.category_values("seed_category"),
        seed_types=defaults.category_values("seed_type"),
        seed_types_primary=defaults.category_values("seed_type_primary"),
        intercrop_types=defaults.category_values("intercrop_type"),
    )


# Field name -> (unit, the column whose observed range describes it).
_UNITS = {
    "plot_acres": ("acres", "plot_acres"),
    "hybridseed_kg_ph": ("kg/ha", "hybridseed_kg_ph"),
    "localseed_kg_ph": ("kg/ha", "localseed_kg_ph"),
    "dap_kg_ph": ("kg/ha", "dap_kg_ph"),
    "urea_kg_ph": ("kg/ha", "urea_kg_ph"),
    "can_kg_ph": ("kg/ha", "can_kg_ph"),
    "npk_kg_ph": ("kg/ha", "npk_kg_ph"),
    "lime_kg_ph": ("kg/ha", "lime_kg_ph"),
    "compost_wheelbarrows_per_acre": ("wheelbarrows/acre", "comp_wb_pa"),
    "plant_date_doy": ("day of year", "plant_date_doy"),
    "hh_num": ("people", "hh_num"),
    "hh_num_under18": ("people", "hh_num_under18"),
    "cows": ("head", "cows"),
}

_ALLOWED_FROM = {
    "district": "district",
    "seed_type": "seed_type",
    "seed_category": "seed_category",
    "intercrop_type": "intercrop_type",
}


@router.get("/input-schema", response_model=InputSchemaResponse,
            responses={503: {"model": ErrorResponse}},
            summary="Accepted prediction inputs, with allowed values and observed ranges")
def input_schema() -> InputSchemaResponse:
    """Describe every field of PlotInput, annotated with the values the training
    data actually contains. Intended for building and validating a form."""
    from api.schemas import PlotInput          # noqa: PLC0415  (avoids a cycle at import)

    defaults = _defaults()
    schema = PlotInput.model_json_schema()
    required = set(schema.get("required", []))

    fields = []
    for name, field in PlotInput.model_fields.items():
        unit, range_column = _UNITS.get(name, (None, None))
        allowed = None
        if name in _ALLOWED_FROM:
            values = defaults.category_values(_ALLOWED_FROM[name])
            # seed_type has 295 levels; the full list has its own endpoint.
            allowed = values if len(values) <= 60 else None
        observed = defaults.range_of(range_column) if range_column else None
        fields.append(FieldSpec(
            name=name,
            type=_type_name(field.annotation),
            required=name in required,
            description=field.description,
            unit=unit,
            allowed_values=allowed,
            observed_range={k: round(v, 3) for k, v in observed.items()} if observed else None,
        ))

    return InputSchemaResponse(
        fields=fields,
        note="Only `district` is required. Every omitted field is filled from that "
             "district's own history rather than rejected, so a sparse request still "
             "scores -- but a prediction is only as informative as the inputs behind "
             "it. `inputs_supplied_count` on each plot result reports how many model "
             "columns your request actually determined.",
    )


def _type_name(annotation) -> str:
    text = str(annotation)
    for needle, name in (("date", "date"), ("bool", "boolean"),
                         ("float", "number"), ("int", "integer"), ("str", "string")):
        if needle in text:
            return name
    return "string"


@router.get("/levers", response_model=LeversResponse,
            responses={503: {"model": ErrorResponse}},
            summary="Controllable levers the recommendation layer can advise on")
def levers() -> LeversResponse:
    """The decisions POST /api/v1/recommend is able to rank, with the shape of
    each fitted curve and whether it matches its agronomic prior.

    The set is chosen to span the farmer's decision space without overlapping
    it. The feature file carries 45 ex-ante lever columns, but most restate one
    another -- `n_kg_ph` and `total_nutrient_kg_ph` are arithmetic on the
    fertiliser columns, `uses_hybrid_seed` is `hybrid_seed_share` thresholded --
    and advising on all of them would credit one bag of DAP several times over.
    """
    try:
        curves = get_curves()
    except CurvesUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return LeversResponse(
        curves_version=curves.generated_at,
        target=curves.target,
        unit="kg/ha",
        method=curves.method,
        fitted_on=curves.source,
        levers=[
            LeverSpec(
                name=lever["name"],
                label=lever["label"],
                column=lever["column"],
                kind=lever["kind"],
                unit=lever["unit"],
                n_plots=lever["n"],
                typical_value=lever["population"]["median"],
                full_range_lift_kg_ph=lever["diagnostics"].get("full_range_lift_kg_ph"),
                curve_shape=lever["diagnostics"]["shape"],
                agronomically_plausible=lever["diagnostics"]["agronomic_check"]["passed"],
                why=lever["why"],
            )
            for lever in curves.levers
        ],
    )
