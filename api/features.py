"""Expand a lean API request into the row the model was trained on.

A caller supplies what a farmer or extension officer knows: district, season,
plot size, seed choice, fertiliser rates, planting date, a few household
assets. The pipeline consumes 143 columns. This module bridges the two.

Two rules govern the bridge:

1. **Anything derivable from the caller's input is derived, not defaulted.**
   The formulas below mirror `scripts/build_features.py` exactly -- the same
   nutrient fractions, the same winsorization ceilings, the same seed and
   compost derivations -- so a supplied fertiliser rate moves every downstream
   column it moved during training.

2. **Everything else is filled from the district's own history**, falling back
   to national medians (see api/defaults.py). Filling with an in-distribution
   value rather than a null matters: the pipeline's median imputer would
   otherwise replace a null with the *national* median regardless of district.

Every row records which columns came from the caller, so a response can be
honest about how much of the prediction rests on the caller's information.
"""
from __future__ import annotations

from datetime import date
from typing import Any

# --- constants mirrored from scripts/build_features.py ----------------------
ACRE_TO_HECTARE = 0.404686

# (N, P2O5, K2O) mass fractions. NPK is assumed 17-17-17; build_features.py
# §nutrients records the sensitivity of that assumption.
FERTILIZER_NUTRIENT_FRACTIONS = {
    "dap_kg_ph": (0.18, 0.46, 0.00),
    "urea_kg_ph": (0.46, 0.00, 0.00),
    "can_kg_ph": (0.26, 0.00, 0.00),
    "npk_kg_ph": (0.17, 0.17, 0.17),
}

LEGUME_TOKENS = ("bean", "groundnut", "peanut", "cowpea", "soy", "green_gram", "pea")

# Wheelbarrows per acre -> per hectare.
WHEELBARROW_PER_ACRE_TO_HECTARE = 2.471050

HYBRID_CATEGORIES = {"hybrid_branded", "other_hybrid"}


def _clip(value: float | None, cap: float | None) -> float | None:
    if value is None:
        return None
    if cap is not None and value > cap:
        return float(cap)
    return float(value)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


class PlotExpansion:
    """One expanded model row plus a record of where its values came from."""

    def __init__(self, row: dict, supplied: list[str], warnings: list[str],
                 district: str, district_known: bool, year: int | None,
                 season_weather: bool) -> None:
        self.row = row
        self.supplied = supplied
        self.warnings = warnings
        self.district = district
        self.district_known = district_known
        self.year = year
        self.season_weather = season_weather


def expand_plot(plot: dict, defaults, default_year: int | None = None) -> PlotExpansion:
    """Turn one lean plot payload into a full model row.

    `plot` is the validated PlotInput as a dict (None for anything omitted).
    """
    warnings: list[str] = []
    supplied: list[str] = []

    raw_district = plot.get("district")
    canonical = defaults.resolve_district(raw_district)
    district_known = canonical is not None
    if not district_known:
        warnings.append(
            f"district '{raw_district}' is not one the model was trained on; "
            "national medians are used for its conditions and the prediction is "
            "materially less reliable")

    year = plot.get("year") or default_year or defaults.latest_year()
    season_weather = defaults.has_season_weather(raw_district, year)
    if district_known and not season_weather:
        warnings.append(
            f"no observed weather for {canonical} in {year}; the district's "
            "climatological averages are used instead")

    row = defaults.base_row(raw_district, year)
    row["district"] = canonical or (str(raw_district) if raw_district else row.get("district"))
    if year is not None:
        row["year"] = int(year)

    def put(column: str, value: Any) -> None:
        """Write a caller-derived value and mark it as supplied."""
        if value is None:
            return
        row[column] = value
        if column not in supplied:
            supplied.append(column)

    def note_cap(column: str, value: float, cap: float | None) -> None:
        """Winsorization must be visible, not silent."""
        if cap is not None and value > cap:
            warnings.append(
                f"{column} of {value:g} exceeds the winsorization ceiling of {cap:g} "
                "applied during training; it was clipped to that ceiling and the "
                "outlier flag was set, exactly as the training data was treated")

    # --- plot size ----------------------------------------------------------
    acres = _as_float(plot.get("plot_acres"))
    if acres is not None:
        cap = defaults.cap("plot_acres")
        note_cap("plot_acres", acres, cap)
        put("plot_acres", _clip(acres, cap))
        put("plot_hectares", _clip(acres, cap) * ACRE_TO_HECTARE)
        put("plot_acres_flagged_outlier", float(cap is not None and acres > cap))

    # --- fertiliser ---------------------------------------------------------
    # Winsorized companions drive every nutrient total, exactly as in training.
    fert_supplied = False
    winsorized: dict[str, float] = {}
    for col in ("dap_kg_ph", "urea_kg_ph", "can_kg_ph", "npk_kg_ph"):
        value = _as_float(plot.get(col))
        if value is None:
            continue
        fert_supplied = True
        cap = defaults.cap(col)
        note_cap(col, value, cap)
        capped = _clip(value, cap)
        winsorized[col] = capped
        put(col, float(value))
        put(f"{col}_winsorized", capped)
        flag = f"{col}_flagged_outlier"
        if flag in row:
            put(flag, float(cap is not None and value > cap))

    if fert_supplied:
        # A column the caller omitted contributes nothing, matching the
        # fillna(0.0) in build_nutrient_features.
        n = p = k = 0.0
        for col, (fn, fp, fk) in FERTILIZER_NUTRIENT_FRACTIONS.items():
            amount = winsorized.get(col, 0.0)
            n += amount * fn
            p += amount * fp
            k += amount * fk
        put("n_kg_ph", n)
        put("p2o5_kg_ph", p)
        put("k2o_kg_ph", k)
        put("total_nutrient_kg_ph", n + p + k)
        put("n_kg_ph_is_missing", 0.0)
        if "can_kg_ph" in winsorized:
            put("topdress_applied", float(winsorized["can_kg_ph"] > 0))

    lime = _as_float(plot.get("lime_kg_ph"))
    if lime is not None:
        note_cap("lime_kg_ph", lime, defaults.cap("lime_kg_ph"))
        put("lime_kg_ph", lime)
        put("lime_kg_ph_winsorized", _clip(lime, defaults.cap("lime_kg_ph")))

    # --- compost ------------------------------------------------------------
    compost_wb = _as_float(plot.get("compost_wheelbarrows_per_acre"))
    if compost_wb is not None:
        cap = defaults.cap("comp_wb_pa")
        note_cap("compost_wheelbarrows_per_acre", compost_wb, cap)
        capped = _clip(compost_wb, cap)
        put("comp_wb_pa", compost_wb)
        put("comp_wb_pa_winsorized", capped)
        put("compost_wb_ph", capped * WHEELBARROW_PER_ACRE_TO_HECTARE)
        put("compost_applied", float(capped > 0))
        put("compost", float(capped > 0))
        put("comp_wb_pa_flagged_outlier", float(cap is not None and compost_wb > cap))

    # --- seed ---------------------------------------------------------------
    hybrid_kg = _as_float(plot.get("hybridseed_kg_ph"))
    local_kg = _as_float(plot.get("localseed_kg_ph"))
    if hybrid_kg is not None or local_kg is not None:
        h, l = hybrid_kg or 0.0, local_kg or 0.0
        put("hybridseed_kg_ph", h)
        put("localseed_kg_ph", l)
        put("uses_hybrid_seed", float(h > 0))
        put("uses_hybrid_seed_is_missing", 0.0)
        if h + l > 0:
            put("hybrid_seed_share", h / (h + l))

    seed_category = plot.get("seed_category")
    if seed_category:
        seed_category = str(seed_category).strip().lower()
        if seed_category in defaults.category_values("seed_category"):
            put("seed_category", seed_category)
            put("seed_type_is_mixed", float(seed_category == "mixed"))
            if hybrid_kg is None and local_kg is None:
                # Seed weights unknown, but the category still fixes the share.
                if seed_category in HYBRID_CATEGORIES:
                    put("hybrid_seed_share", 1.0)
                    put("uses_hybrid_seed", 1.0)
                elif seed_category == "local":
                    put("hybrid_seed_share", 0.0)
                    put("uses_hybrid_seed", 0.0)
        else:
            warnings.append(f"unknown seed_category '{seed_category}'; ignored")

    seed_type = plot.get("seed_type")
    if seed_type:
        seed_type = str(seed_type).strip().lower()
        if seed_type in defaults.category_values("seed_type"):
            put("seed_type", seed_type)
            if seed_type in defaults.category_values("seed_type_clean"):
                put("seed_type_clean", seed_type)
            primary = seed_type.split()[0]
            if primary in defaults.category_values("seed_type_primary"):
                put("seed_type_primary", primary)
            put("seed_type_is_mixed", float(len(seed_type.split()) > 1))
        else:
            warnings.append(
                f"seed_type '{seed_type}' is not in the model's vocabulary; "
                "the district's most common seed is used instead "
                "(GET /api/v1/reference/seed-types lists valid values)")

    # --- timing -------------------------------------------------------------
    plant_date = plot.get("plant_date")
    doy = _as_float(plot.get("plant_date_doy"))
    if isinstance(plant_date, date):
        put("plant_date_doy", float(plant_date.timetuple().tm_yday))
    elif doy is not None:
        put("plant_date_doy", doy)

    # --- intercropping ------------------------------------------------------
    intercrop = plot.get("intercrop")
    if intercrop is not None:
        put("intercrop", float(bool(intercrop)))
    intercrop_type = plot.get("intercrop_type")
    if intercrop_type:
        intercrop_type = str(intercrop_type).strip().lower()
        if intercrop_type in defaults.category_values("intercrop_type"):
            put("intercrop_type", intercrop_type)
            put("intercrop", 1.0)
        else:
            warnings.append(f"unknown intercrop_type '{intercrop_type}'; ignored")
        put("intercrop_is_legume",
            float(any(t in intercrop_type for t in LEGUME_TOKENS)))

    # --- household ----------------------------------------------------------
    for field, column in (("hh_num", "hh_num"),
                          ("hh_num_under18", "hh_num_under18"),
                          ("cows", "cows")):
        value = _as_float(plot.get(field))
        if value is not None:
            put(column, value)
    if _as_float(plot.get("cows")) is not None:
        put("owns_cows", float(_as_float(plot.get("cows")) > 0))

    for field, columns in (("owns_oxen", ("owns_oxen",)),
                           ("owns_electricity", ("owns_electricity", "electricity"))):
        value = plot.get(field)
        if value is not None:
            for column in columns:
                put(column, float(bool(value)))

    _apply_wealth_index(row, plot, defaults, put)

    return PlotExpansion(row=row, supplied=supplied, warnings=warnings,
                         district=row["district"], district_known=district_known,
                         year=int(year) if year is not None else None,
                         season_weather=season_weather)


def _apply_wealth_index(row: dict, plot: dict, defaults, put) -> None:
    """Recompute wealth_index when the caller supplied any asset.

    Training standardised the index within each season across the whole cohort,
    which a single request cannot reproduce. The stored asset means and standard
    deviations put one row on the same scale: z-score each asset, average, then
    standardise by the cohort's spread of that average.
    """
    spec = defaults.wealth
    if not spec or not spec.get("assets"):
        return
    if not any(plot.get(f) is not None for f in ("cows", "owns_oxen", "owns_electricity")):
        return

    zs = []
    for column in spec["assets"]:
        sd = spec["sds"].get(column)
        mean = spec["means"].get(column)
        value = row.get(column)
        if value is None or sd in (None, 0) or mean is None:
            continue
        zs.append((float(value) - mean) / sd)
    if not zs:
        return
    raw = sum(zs) / len(zs)
    raw_sd = spec.get("raw_sd") or 0.0
    if raw_sd > 0:
        put("wealth_index", (raw - spec.get("raw_mean", 0.0)) / raw_sd)
