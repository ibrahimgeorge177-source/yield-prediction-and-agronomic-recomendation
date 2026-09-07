"""
Kenya Maize Data Cleaning Pipeline
===================================

Production data-understanding-and-cleaning pipeline for the One Acre Fund
MEL Agronomic Survey (2021), scoped to Kenya maize only.

This script is the executable source of truth behind:
  - notebooks/01_kenya_maize_data_understanding_and_cleaning.ipynb
  - documentation/kenya_maize_cleaning_documentation.md

Run:
    python scripts/clean_kenya_maize.py

Reads:  data/raw/Copy of Final 2021_One_Acre_Fund_Agronomic_Survey_Data_2021.xlsx
Writes: data/cleaned/kenya_maize_cleaned.csv
        data/cleaned/kenya_maize_cleaning_stats.json  (audit numbers used in docs)

All cleaning decisions and their exact thresholds are documented inline and
mirrored in documentation/kenya_maize_cleaning_documentation.md. Every
correction is non-destructive: raw values are preserved in their original
columns, and corrections/flags are added as new columns, so nothing found
in the source workbook is silently discarded.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT / "data" / "raw" / "Copy of Final 2021_One_Acre_Fund_Agronomic_Survey_Data_2021.xlsx"
CLEANED_DIR = ROOT / "data" / "cleaned"
CLEANED_CSV_PATH = CLEANED_DIR / "kenya_maize_cleaned.csv"
STATS_PATH = CLEANED_DIR / "kenya_maize_cleaning_stats.json"

ACRE_TO_HECTARE = 0.404686  # standard conversion; empirically confirmed exact
                            # against every Kenya row that carries both
                            # plot_acres and plot_hectares (0 deviations
                            # across 17,484 rows, see documentation).

stats = {}  # collects every number used in the documentation narrative


def log(msg):
    print(f"[clean_kenya_maize] {msg}")


# ---------------------------------------------------------------------------
# 1. Load & isolate Kenya maize
# ---------------------------------------------------------------------------
def load_kenya_maize():
    log(f"Loading raw workbook: {RAW_PATH.name}")
    raw = pd.read_excel(RAW_PATH, sheet_name="Data", engine="openpyxl")
    stats["raw_total_rows"] = int(len(raw))
    stats["raw_total_cols"] = int(raw.shape[1])
    stats["raw_country_counts"] = raw["country"].value_counts(dropna=False).to_dict()
    stats["raw_crop_counts"] = raw["crop"].value_counts(dropna=False).to_dict()

    ke = raw[(raw["country"] == "Kenya") & (raw["crop"] == "maize")].copy()
    stats["kenya_maize_rows_selected"] = int(len(ke))
    stats["kenya_all_rows"] = int((raw["country"] == "Kenya").sum())
    # Confirms the structural fact used to justify a Kenya-maize scope:
    # Kenya's survey only ever recorded maize, so filtering on crop=='maize'
    # changes nothing — it's kept as an explicit, self-documenting guard
    # rather than an implicit assumption.
    assert stats["kenya_maize_rows_selected"] == stats["kenya_all_rows"], (
        "Expected country=='Kenya' to already imply crop=='maize'; "
        "this assumption changed in a newer data pull and must be re-checked."
    )
    ke = ke.reset_index(drop=True)
    log(f"Kenya maize rows: {len(ke)}")
    ke = fix_unique_id_dtype(ke)
    return ke


def fix_unique_id_dtype(ke):
    # unique_id is meant to be an opaque hex-like string (e.g. "b801f443"),
    # but Excel/openpyxl auto-typed some cells as numbers or dates wherever
    # the original string happened to look numeric (e.g. all-digit hex),
    # occasionally with visible float-precision corruption on long values.
    # This affects every survey year, not just one, so it is a source
    # workbook quirk rather than something introduced by this pipeline.
    non_str_mask = ke["unique_id"].map(lambda x: not isinstance(x, str))
    stats["unique_id_non_string_in_source_rows"] = int(non_str_mask.sum())
    ke["unique_id"] = ke["unique_id"].astype(str)
    dupes = ke["unique_id"].duplicated().sum()
    stats["unique_id_duplicates_after_cast"] = int(dupes)
    log(
        f"unique_id: {non_str_mask.sum()} rows ({non_str_mask.mean()*100:.1f}%) were stored as "
        f"int/datetime instead of string in the source; cast to string for consistency "
        f"({dupes} collisions found after casting)."
    )
    return ke


# ---------------------------------------------------------------------------
# 2. Drop columns that are 100% empty for Kenya
# ---------------------------------------------------------------------------
ALWAYS_NULL_FOR_KENYA = [
    "seed_kg", "seed_kg_pa", "seed_kg_ph",       # Kenya never reported consolidated seed_kg (relies on local/hybrid split)
    "hybridseed_kg",                              # only *_pa / *_ph variants collected
    "termites", "gls", "anthracnose", "hail",     # pest/shock columns never asked in Kenya
    "stalk_rot", "kelnel_rot",
    "distance_min",                               # Kenya uses distance_meter instead
    "dap_method", "urea_method", "npk_method",    # method-of-application only collected in Rwanda
    "planting_method",
    "heavy_rain",                                 # shock flag never asked in Kenya
    "sitename",                                   # never populated in this extract
    "comp_kg_ph",                                 # Kenya only records compost in wheelbarrows (comp_wb_pa), never kg/ha
]


def drop_empty_columns(ke):
    completeness = ke.notna().mean() * 100
    truly_empty = completeness[completeness == 0].index.tolist()
    stats["columns_100pct_null_expected"] = sorted(ALWAYS_NULL_FOR_KENYA)
    stats["columns_100pct_null_found"] = sorted(truly_empty)
    # Guard: if the source data changes and a "known-empty" column now has
    # data, we want that to be visible rather than silently dropped.
    unexpected_diff = set(truly_empty).symmetric_difference(ALWAYS_NULL_FOR_KENYA)
    if unexpected_diff:
        log(f"WARNING: empty-column set differs from expectation: {unexpected_diff}")
    ke = ke.drop(columns=truly_empty)
    stats["cols_after_empty_drop"] = int(ke.shape[1])
    log(f"Dropped {len(truly_empty)} columns that are 100% null for Kenya.")
    return ke


# ---------------------------------------------------------------------------
# 3. Harmonize categorical labels: season, district
# ---------------------------------------------------------------------------
SEASON_MAP = {"lr": "long_rains", "long rain": "long_rains"}

# district spellings observed to refer to the same place across survey years
DISTRICT_MAP = {
    "kakamega(south)": "kakamega_south",
    "kakamegasouth": "kakamega_south",
    "kakamegab(north)": "kakamega_north",
    "kakameganorth": "kakamega_north",
}


def harmonize_categoricals(ke):
    stats["season_values_before"] = ke["season"].value_counts(dropna=False).to_dict()
    ke["season"] = ke["season"].map(SEASON_MAP).fillna(ke["season"])
    stats["season_values_after"] = ke["season"].value_counts(dropna=False).to_dict()

    ke["district_raw"] = ke["district"]
    ke["district"] = (
        ke["district"].astype("string").str.strip().str.lower()
    )
    n_remapped = ke["district"].isin(DISTRICT_MAP).sum()
    ke["district"] = ke["district"].map(DISTRICT_MAP).fillna(ke["district"])
    stats["district_rows_remapped"] = int(n_remapped)
    stats["district_unique_before"] = int(ke["district_raw"].nunique())
    stats["district_unique_after"] = int(ke["district"].nunique())
    log(
        f"Harmonized district labels: {stats['district_unique_before']} -> "
        f"{stats['district_unique_after']} unique values "
        f"({n_remapped} rows remapped)."
    )
    return ke


# ---------------------------------------------------------------------------
# 4. GPS validation
# ---------------------------------------------------------------------------
# Approximate Kenya bounding box (generous, includes border margin)
KE_LAT_RANGE = (-4.9, 5.1)
KE_LON_RANGE = (33.5, 42.0)


def validate_gps(ke):
    lat, lon = ke["field_latitude"], ke["field_longitude"]
    bad = (
        lat.notna()
        & lon.notna()
        & (
            (lat < KE_LAT_RANGE[0]) | (lat > KE_LAT_RANGE[1])
            | (lon < KE_LON_RANGE[0]) | (lon > KE_LON_RANGE[1])
        )
    )
    stats["gps_invalid_rows"] = int(bad.sum())
    ke["field_gps_flagged_invalid"] = bad
    ke.loc[bad, ["field_latitude", "field_longitude"]] = np.nan
    log(f"Nulled {bad.sum()} field GPS pair(s) falling outside Kenya's bounding box.")
    return ke


# ---------------------------------------------------------------------------
# 5. Date parsing & growing-season-length flagging
# ---------------------------------------------------------------------------
GROWING_SEASON_MIN_DAYS = 60
GROWING_SEASON_MAX_DAYS = 300


def clean_dates(ke):
    ke["plant_date"] = pd.to_datetime(ke["plant_date"], errors="coerce")
    ke["harvest_date"] = pd.to_datetime(ke["harvest_date"], errors="coerce")

    plant_year = ke["plant_date"].dt.year
    implausible_plant_year = plant_year.notna() & (
        (plant_year < ke["year"] - 1) | (plant_year > ke["year"])
    )
    stats["plant_date_implausible_year_rows"] = int(implausible_plant_year.sum())
    ke["plant_date_flagged_implausible"] = implausible_plant_year
    ke.loc[implausible_plant_year, "plant_date"] = pd.NaT

    growing_season_days = (ke["harvest_date"] - ke["plant_date"]).dt.days
    out_of_range = growing_season_days.notna() & (
        (growing_season_days < GROWING_SEASON_MIN_DAYS)
        | (growing_season_days > GROWING_SEASON_MAX_DAYS)
    )
    stats["growing_season_out_of_range_rows"] = int(out_of_range.sum())
    stats["growing_season_valid_range_days"] = [GROWING_SEASON_MIN_DAYS, GROWING_SEASON_MAX_DAYS]

    ke["growing_season_days"] = growing_season_days
    ke["growing_season_days_flagged"] = out_of_range
    ke.loc[out_of_range, "growing_season_days"] = np.nan

    stats["growing_season_days_available_rows"] = int(ke["growing_season_days"].notna().sum())
    log(
        f"Flagged {implausible_plant_year.sum()} rows with an implausible plant_date year, "
        f"and {out_of_range.sum()} rows with a growing-season length outside "
        f"[{GROWING_SEASON_MIN_DAYS}, {GROWING_SEASON_MAX_DAYS}] days."
    )
    return ke


# ---------------------------------------------------------------------------
# 6. Per-hectare backfill via verified acre->hectare conversion factor
# ---------------------------------------------------------------------------
# Kenya's 2020 survey round only collected acreage (plot_hectares and every
# *_kg_ph column are structurally unrecorded that year — see Variables sheet
# comment "Only collect acreage data"). We verified that wherever both the
# _pa and _ph versions of a field exist elsewhere in the Kenya data, their
# ratio is a constant 2.471050 (== 1 / 0.404686, the standard acre-to-hectare
# conversion) to within floating point error. We use that verified constant
# to derive the missing per-hectare values from the always-present per-acre
# values, rather than leaving an entire survey year blank for these fields.
#
# CRITICAL: the acre->hectare conversion runs in OPPOSITE DIRECTIONS for the
# two kinds of column here, and they must not share a factor.
#
#   * A RATE (kg per unit area). One acre is the smaller area, so the same
#     physical application is a LARGER number per hectare:
#         dap_kg_ph = dap_kg_pa * 2.471050
#     Verified: dap_kg_ph / dap_kg_pa == 2.471050 in every row carrying both.
#
#   * An AREA. A half-acre plot is a SMALLER number in hectares:
#         plot_hectares = plot_acres * 0.404686
#     Verified: plot_hectares / plot_acres == 0.404686 in all 17,484 rows
#     carrying both -- the reciprocal of the rate factor, as the cleaning
#     documentation's own S3.3 measurement states.
#
# plot_acres/plot_hectares originally sat in the rate list and so received the
# rate factor, inflating 2020's plot sizes by 2.471050/0.404686 = 6.107x (2020
# median 1.24 ha against 0.20 ha in all four training years, max 44.5 ha).
# Because only 2020 needed backfilling, the error landed exactly on the
# out-of-time holdout boundary and turned an ordinary condition variable into a
# 6x step function that identifies the holdout year -- invisible to every gate
# in the feature stage, which screens coverage and variance but not scale.
# Found by the base-model stage; see documentation/kenya_maize_base_models_documentation.md S3.1.
PA_TO_PH_RATE_PAIRS = [
    ("dap_kg_pa", "dap_kg_ph"),
    ("urea_kg_pa", "urea_kg_ph"),
    ("npk_kg_pa", "npk_kg_ph"),
    ("can_kg_pa", "can_kg_ph"),
    ("lime_kg_pa", "lime_kg_ph"),
    ("localseed_kg_pa", "localseed_kg_ph"),
    ("hybridseed_kg_pa", "hybridseed_kg_ph"),
]
PA_TO_PH_AREA_PAIRS = [
    ("plot_acres", "plot_hectares"),
]
PA_TO_PH_FACTOR = 1 / ACRE_TO_HECTARE  # 2.47105... , for rates
ACRE_TO_HECTARE_FACTOR = ACRE_TO_HECTARE  # 0.404686, for areas

PA_TO_PH_PAIRS = PA_TO_PH_RATE_PAIRS + PA_TO_PH_AREA_PAIRS  # for reporting only


def backfill_per_hectare_fields(ke):
    backfill_counts = {}
    for pairs, factor in ((PA_TO_PH_RATE_PAIRS, PA_TO_PH_FACTOR),
                          (PA_TO_PH_AREA_PAIRS, ACRE_TO_HECTARE_FACTOR)):
      for pa_col, ph_col in pairs:
        before = int(ke[ph_col].notna().sum())
        missing_mask = ke[ph_col].isna() & ke[pa_col].notna()
        ke.loc[missing_mask, ph_col] = ke.loc[missing_mask, pa_col] * factor
        after = int(ke[ph_col].notna().sum())
        backfill_counts[ph_col] = {
            "completeness_before_pct": round(before / len(ke) * 100, 1),
            "completeness_after_pct": round(after / len(ke) * 100, 1),
            "rows_backfilled": int(missing_mask.sum()),
            "factor": round(float(factor), 6),
        }
    stats["per_hectare_backfill"] = backfill_counts
    total_backfilled = sum(v["rows_backfilled"] for v in backfill_counts.values())
    log(f"Backfilled {total_backfilled} per-hectare cells across {len(PA_TO_PH_PAIRS)} columns: "
        f"rates x{PA_TO_PH_FACTOR:.6f}, areas x{ACRE_TO_HECTARE_FACTOR:.6f}.")

    # The two directions must not be allowed to drift back together. A ratio
    # check on the finished columns fails loudly rather than silently shipping a
    # 6x year proxy again.
    both = ke.dropna(subset=["plot_acres", "plot_hectares"])
    both = both[both["plot_acres"] > 0]
    area_ratio = (both["plot_hectares"] / both["plot_acres"])
    assert abs(area_ratio.mean() - ACRE_TO_HECTARE) < 1e-4 and area_ratio.std() < 1e-4, (
        f"plot_hectares/plot_acres is {area_ratio.mean():.6f} (sd {area_ratio.std():.2e}), "
        f"expected {ACRE_TO_HECTARE} -- the area conversion has been inverted again.")

    # 2017 hybridseed is a documented exception: the source Variables sheet
    # notes 2017 hybrid-seed kg were estimated differently for 1AF vs.
    # non-1AF farmers, and empirically the _pa/_ph ratio is NOT constant
    # that year (mean 3.79, std 2.98, vs. exactly 2.471050 in every other
    # year). No 2017 rows needed backfilling (both fields were already
    # populated that year), so this doesn't affect the backfill above, but
    # existing 2017 hybridseed_kg_pa/ph values should be treated as lower
    # confidence and are flagged rather than altered.
    is_2017 = ke["year"] == 2017
    ke["hybridseed_2017_methodology_flag"] = is_2017 & ke["hybridseed_kg_pa"].notna()
    stats["hybridseed_2017_flagged_rows"] = int(ke["hybridseed_2017_methodology_flag"].sum())
    return ke


# ---------------------------------------------------------------------------
# 7. Drop now-redundant raw (_kg) and per-acre (_kg_pa) duplicate columns
# ---------------------------------------------------------------------------
# plot_hectares and every _kg_ph column above are now a fixed linear
# rescaling of their _kg_pa / plot_acres counterpart (correlation = 1.0), so
# keeping both is pure redundancy. We standardize on the per-hectare (_ph)
# unit as recommended in the CRISP-DM report (documentation/project1_crispdm_report.docx,
# Section 3.2), keep plot_acres as the original most-complete source
# measurement, and drop the raw totals and now-redundant _pa duplicates.
REDUNDANT_AFTER_BACKFILL = [
    "can_kg", "dap_kg", "urea_kg", "lime_kg", "npk_kg",              # raw totals (no denominator)
    "dap_kg_pa", "urea_kg_pa", "npk_kg_pa", "can_kg_pa", "lime_kg_pa",
    "localseed_kg_pa", "localseed_kg", "hybridseed_kg_pa",
]


def drop_redundant_columns(ke):
    present = [c for c in REDUNDANT_AFTER_BACKFILL if c in ke.columns]
    ke = ke.drop(columns=present)
    stats["redundant_columns_dropped"] = present
    log(f"Dropped {len(present)} redundant raw/_pa columns now superseded by _ph fields.")
    return ke


# ---------------------------------------------------------------------------
# 8. seed_type harmonization
# ---------------------------------------------------------------------------
SEED_TOKEN_MAP = {
    "local_maize": "local", "localmaize": "local",
    "other_hybrid": "other_hybrid", "otherhybrid": "other_hybrid",
    "punda_milia_53": "sc_punda_milia_53", "scpundamilia53": "sc_punda_milia_53",
    "sc_punda_milia_53": "sc_punda_milia_53",
    "pioneer_p3812w": "pioneer_p3812w", "p3812w": "pioneer_p3812w",
    "dk8033": "dk_8033", "dk_8033": "dk_8033",
    "we1101": "we_1101", "we_1101": "we_1101",
}

HYBRID_BRAND_PREFIXES = ("h_", "dk_", "wh_", "sc_", "pan_", "pn_", "pioneer_", "we_", "sy")


def _clean_seed_token(tok):
    tok = tok.strip().lower()
    return SEED_TOKEN_MAP.get(tok, tok)


def _seed_category(tokens):
    if not tokens:
        return np.nan
    if len(tokens) > 1:
        return "mixed"
    tok = tokens[0]
    if tok == "local":
        return "local"
    if tok == "other_hybrid":
        return "other_hybrid"
    if tok.startswith(HYBRID_BRAND_PREFIXES):
        return "hybrid_branded"
    return "other"


def clean_seed_type(ke):
    raw = ke["seed_type"].astype("string")
    stats["seed_type_raw_unique_values"] = int(raw.nunique())

    def process(val):
        if pd.isna(val):
            return []
        # collapse repeated whitespace, split on remaining whitespace
        toks = str(val).strip().split()
        return [_clean_seed_token(t) for t in toks if t]

    token_lists = raw.map(process)
    ke["seed_type_clean"] = token_lists.map(lambda toks: " ".join(toks) if toks else pd.NA)
    ke["seed_type_primary"] = token_lists.map(lambda toks: toks[0] if toks else pd.NA)
    ke["seed_type_is_mixed"] = token_lists.map(lambda toks: len(toks) > 1)
    ke["seed_category"] = token_lists.map(_seed_category)

    stats["seed_type_clean_unique_values"] = int(ke["seed_type_clean"].nunique())
    stats["seed_category_counts"] = ke["seed_category"].value_counts(dropna=False).to_dict()
    stats["seed_type_mixed_rows"] = int(ke["seed_type_is_mixed"].sum())
    log(
        f"seed_type: {stats['seed_type_raw_unique_values']} raw distinct values -> "
        f"{stats['seed_type_clean_unique_values']} after whitespace/synonym harmonization; "
        f"derived seed_category (hybrid_branded/local/other_hybrid/mixed/other)."
    )
    return ke


# ---------------------------------------------------------------------------
# 9. Outlier handling (non-destructive: flag + winsorized companion column)
# ---------------------------------------------------------------------------
# Fertilizer-rate ceilings are set from agronomic plausibility for
# single-nutrient fertilizers on a Kenyan smallholder maize plot (DAP/urea/
# NPK/CAN topdress or basal rates rarely exceed ~150 kg/ha even under
# intensive management; 500 kg/ha is already a generous multiple of that).
# Lime is a soil-amendment applied in much larger quantities (often
# 1-2+ t/ha), so it gets its own, higher ceiling.
FERTILIZER_CEILINGS = {
    "dap_kg_ph": 500,
    "urea_kg_ph": 500,
    "npk_kg_ph": 500,
    "can_kg_ph": 500,
    "lime_kg_ph": 2000,
}

WEED_VALID_RANGE = {2016: (0, 3), 2017: (0, 3), 2018: (0, 3), 2019: (0, 5), 2020: (0, 5)}

YIELD_WINSOR_PCTL = 0.995
COMP_WB_WINSOR_PCTL = 0.995
PLOT_ACRES_FLAG_PCTL = 0.999


def handle_outliers(ke):
    outlier_stats = {}

    # --- target variable: statistical winsorization (no agronomic ceiling
    # exists for yield the way it does for a fertilizer rate) ---
    cap = ke["yield_kg_ph"].quantile(YIELD_WINSOR_PCTL)
    flag = ke["yield_kg_ph"] > cap
    ke["yield_kg_ph_flagged_outlier"] = flag
    ke["yield_kg_ph_winsorized"] = ke["yield_kg_ph"].clip(upper=cap)
    ke["yield_kg_ph_crop_failure"] = ke["yield_kg_ph"] == 0
    outlier_stats["yield_kg_ph"] = {
        "method": f"winsorize at p{YIELD_WINSOR_PCTL*100:g}",
        "cap_value": round(float(cap), 1),
        "rows_flagged": int(flag.sum()),
        "zero_yield_rows": int(ke["yield_kg_ph_crop_failure"].sum()),
    }

    # --- fertilizer rate columns: agronomic ceiling ---
    for col, ceil in FERTILIZER_CEILINGS.items():
        flag = ke[col] > ceil
        ke[f"{col}_flagged_outlier"] = flag
        ke[f"{col}_winsorized"] = ke[col].clip(upper=ceil)
        outlier_stats[col] = {
            "method": f"agronomic ceiling {ceil} kg/ha",
            "cap_value": ceil,
            "rows_flagged": int(flag.sum()),
        }

    # --- comp_wb_pa: no agronomic reference range for this Kenya-specific
    # "wheelbarrows of compost per acre" unit -> statistical winsorization ---
    cap = ke["comp_wb_pa"].quantile(COMP_WB_WINSOR_PCTL)
    flag = ke["comp_wb_pa"] > cap
    ke["comp_wb_pa_flagged_outlier"] = flag
    ke["comp_wb_pa_winsorized"] = ke["comp_wb_pa"].clip(upper=cap)
    outlier_stats["comp_wb_pa"] = {
        "method": f"winsorize at p{COMP_WB_WINSOR_PCTL*100:g}",
        "cap_value": round(float(cap), 1),
        "rows_flagged": int(flag.sum()),
    }

    # --- plot_acres: denominator-first screen (flag only; this column
    # feeds every _kg_ph and plot_hectares figure, so it is never altered
    # here, only flagged for downstream review) ---
    cap = ke["plot_acres"].quantile(PLOT_ACRES_FLAG_PCTL)
    flag = ke["plot_acres"] > cap
    ke["plot_acres_flagged_outlier"] = flag
    outlier_stats["plot_acres"] = {
        "method": f"flag only (denominator field) at p{PLOT_ACRES_FLAG_PCTL*100:g}",
        "cap_value": round(float(cap), 2),
        "rows_flagged": int(flag.sum()),
    }

    # weed: values outside the documented 0-5 and 0-3 response range (depending on year) are
    # treated as entry errors and nulled (not winsorized, since e.g. "22"
    # is not a plausible weeding count, just corrupted)
    weed_bounds = ke["year"].map(WEED_VALID_RANGE)
    bad_weed = ke["weed"].notna() & ~ke["weed"].between(weed_bounds.str[0], weed_bounds.str[1])
    ke.loc[bad_weed, "weed"] = np.nan

    # flag = ke["weed"].notna() & ((ke["weed"] < lo) | (ke["weed"] > hi))
    # ke["weed_flagged_invalid"] = flag
    # ke.loc[flag, "weed"] = np.nan


    outlier_stats["weed"] = {
        "method": f"null values outside documented range [{WEED_VALID_RANGE}]",
        "rows_nulled": int(flag.sum()),
    }

    stats["outlier_handling"] = outlier_stats
    log("Applied outlier flags + non-destructive winsorized companion columns "
        "for yield, fertilizer rates, compost, plot size, and weeding counts.")
    return ke


# ---------------------------------------------------------------------------
# 10. Missingness indicator flags for structurally sparse columns
# ---------------------------------------------------------------------------
MISSINGNESS_FLAG_COLUMNS = [
    "fertility", "slope_angle_num", "slope", "distance_meter",
    "pest_disease", "comp_method", "comp_quality",
]


def add_missingness_flags(ke):
    added = []
    for col in MISSINGNESS_FLAG_COLUMNS:
        flag_col = f"{col}_is_missing"
        ke[flag_col] = ke[col].isna()
        added.append(flag_col)
    stats["missingness_flag_columns_added"] = added
    log(f"Added {len(added)} explicit missingness indicator columns for structurally sparse fields.")
    return ke


# ---------------------------------------------------------------------------
# 11. Dtype finalization
# ---------------------------------------------------------------------------
BINARY_COLUMNS = [
    "hybrid", "pest_disease", "drought", "flood", "intercrop", "compost",
    "FAW", "stemborer", "msv", "mlnd", "cutworms", "aphids", "blight", "striga",
    "pesticide", "electricity", "radio_binary", "bikes_binary", "cows_binary",
    "goats_binary", "chicken_binary", "comp_source_manure",
]


def finalize_dtypes(ke):
    for col in BINARY_COLUMNS:
        if col in ke.columns:
            ke[col] = ke[col].astype("boolean")  # nullable boolean, preserves NaN
    stats["binary_columns_cast"] = [c for c in BINARY_COLUMNS if c in ke.columns]
    return ke


# ---------------------------------------------------------------------------
# 12. Final column ordering & save
# ---------------------------------------------------------------------------
def save(ke):
    CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    ke = ke.sort_values(["year", "unique_id"]).reset_index(drop=True)
    ke.to_csv(CLEANED_CSV_PATH, index=False)
    stats["final_shape"] = list(ke.shape)
    stats["final_columns"] = ke.columns.tolist()
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)
    log(f"Saved cleaned dataset: {CLEANED_CSV_PATH} ({ke.shape[0]} rows x {ke.shape[1]} cols)")
    log(f"Saved audit stats: {STATS_PATH}")
    return ke


def run():
    ke = load_kenya_maize()
    ke = drop_empty_columns(ke)
    ke = harmonize_categoricals(ke)
    ke = validate_gps(ke)
    ke = clean_dates(ke)
    ke = backfill_per_hectare_fields(ke)
    ke = drop_redundant_columns(ke)
    ke = clean_seed_type(ke)
    ke = handle_outliers(ke)
    ke = add_missingness_flags(ke)
    ke = finalize_dtypes(ke)
    ke = save(ke)
    return ke


if __name__ == "__main__":
    run()
