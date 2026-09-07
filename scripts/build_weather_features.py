"""
Kenya Maize Weather Feature Pipeline -- Stage 3: Row-Level Feature Construction
==============================================================================

Turns the cell x year x month panel fetched from Earth Engine into one row per
survey record, matching data/cleaned/kenya_maize_cleaned.csv exactly.

Run:
    python scripts/build_weather_features.py

Reads:  data/weather/row_locations.csv              (stage 1)
        data/weather/monthly_weather_panel.csv      (stage 2)
        data/weather/monthly_weather_climatology.csv(stage 2, optional)
        data/cleaned/kenya_maize_cleaned.csv        (for plant/harvest dates)
Writes: data/weather/kenya_maize_weather_features.csv
        data/weather/weather_feature_stats.json

WHAT GETS BUILT, AND WHY EACH BLOCK EARNS ITS PLACE
---------------------------------------------------
1. CALENDAR MONTHS (January-October). The requested feature: rainfall total,
   rain days, mean/max/min temperature and growing degree days for each month
   of the season year. This is the raw material; blocks 2-4 are what a model
   can actually use.

2. PRE-SEASON. The previous November-December rainfall that fills the soil
   profile before anyone plants. The only rainfall block that is genuinely
   known in advance, which is what makes it the one usable by the loan-sizing
   surface.

3. SEASON WINDOWS, on a fixed March-August calendar window AND on a
   plant-date-relative window. Both, because they answer different questions:
   the fixed window is defined for all 23,674 rows and is comparable across
   them; the relative window is defined only for the 88% with a plant date but
   is the one that actually tracks the crop, since April rain means
   establishment to a March planter and flowering to a February one.

4. ANOMALIES against a 1991-2020 normal. This block is the reason the whole
   exercise is worth doing, and the reasoning is in ANOMALIES vs LEVELS below.

ANOMALIES vs LEVELS -- the honest problem with weather features
---------------------------------------------------------------
A raw rainfall level is close to a year label. This survey covers five seasons,
and the cells are packed into a few hundred kilometres of western Kenya, so
regional rainfall moves largely in unison: within-year spread across cells is
small next to between-year spread. A gradient-boosted tree handed `rain_mm_apr`
can read the season off it and then reproduce that season's mean yield, which
scores beautifully in cross-validation and predicts nothing out of time. This
is the same failure build_features.py built three gates to catch, arriving
through a new door -- and the coverage gates will NOT catch it, because these
columns are 100% present in every year by construction.

So this stage does two things about it rather than one:

  - It emits anomalies (`*_anom_*`) alongside levels. An anomaly asks "wetter
    or drier than this specific place normally is", which is comparable across
    cells and strips out the fixed geography that levels smuggle in.
  - It MEASURES the problem instead of asserting it, reporting for every
    weather feature the share of its variance that lies between years rather
    than within them (`year_variance_share`). Anything above ~0.5 is more year
    label than weather and is flagged in the manifest for the modelling package
    to weigh deliberately.

Neither of those makes a level feature safe on its own; what makes it safe is
the out-of-time holdout the project already uses. The diagnostic exists so the
choice is made with a number in hand.

DEPENDENCIES: numpy + pandas only. Runs offline once stage 2 has landed.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CLEANED_CSV_PATH = ROOT / "data" / "cleaned" / "kenya_maize_cleaned.csv"
WEATHER_DIR = ROOT / "data" / "weather"
ROW_LOCATIONS_PATH = WEATHER_DIR / "row_locations.csv"
PANEL_PATH = WEATHER_DIR / "monthly_weather_panel.csv"
CLIMATOLOGY_PATH = WEATHER_DIR / "monthly_weather_climatology.csv"
OUTPUT_PATH = WEATHER_DIR / "kenya_maize_weather_features.csv"
STATS_PATH = WEATHER_DIR / "weather_feature_stats.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MONTH_ABBR = {
    1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "may", 6: "jun",
    7: "jul", 8: "aug", 9: "sep", 10: "oct", 11: "nov", 12: "dec",
}

# Emitted as row columns. Stops at October because the long rains harvest is
# over by then (97% of dated harvests fall in June-October) and November
# onward belongs to the NEXT season's pre-season block, not this one's.
FEATURE_MONTHS = tuple(range(1, 11))

# The fixed season window. Chosen from the data, not from a calendar: 92% of
# dated plantings fall in February-April and 93% of dated harvests in
# June-October, so March-August is the window that contains the crop for the
# clear majority of rows.
SEASON_MONTHS = (3, 4, 5, 6, 7, 8)

# Pre-season soil moisture recharge: the short rains of the PREVIOUS calendar
# year. Read from season_year - 1, which is why stage 2 fetches 2015.
PRESEASON_MONTHS = (11, 12)

# Plant-date-relative window. Four months from the month of planting covers
# establishment (p0), vegetative growth (p1), flowering and silking (p2, the
# yield-critical one for maize) and grain fill (p3).
PLANT_RELATIVE_OFFSETS = (0, 1, 2, 3)

MONTHLY_BANDS = ["rain_mm", "rain_days", "tmean_c", "tmax_c", "tmin_c", "gdd10"]

# Above this, a feature's variance is mostly between-season rather than
# between-place: it is closer to a year label than to a description of a farm.
# Not a gate -- nothing is dropped on it -- but it is reported per feature and
# summarised in the documentation.
YEAR_VARIANCE_WARN = 0.5

stats = {}


def log(msg):
    print(f"[build_weather_features] {msg}")


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------
def load_inputs():
    for path, stage in (
        (ROW_LOCATIONS_PATH, "python scripts/weather_locations.py"),
        (PANEL_PATH, "python scripts/fetch_gee_weather.py --project YOUR_PROJECT_ID"),
    ):
        if not path.exists():
            raise SystemExit(f"{path} not found. Run first:\n    {stage}")

    rows = pd.read_csv(ROW_LOCATIONS_PATH)
    panel = pd.read_csv(PANEL_PATH)
    clim = pd.read_csv(CLIMATOLOGY_PATH) if CLIMATOLOGY_PATH.exists() else None

    dates = pd.read_csv(
        CLEANED_CSV_PATH, low_memory=False,
        usecols=["unique_id", "plant_date", "harvest_date"],
    )
    for c in ("plant_date", "harvest_date"):
        dates[c] = pd.to_datetime(dates[c], errors="coerce")
    rows = rows.merge(dates, on="unique_id", how="left", validate="one_to_one")

    stats["input_rows"] = int(len(rows))
    stats["panel_rows"] = int(len(panel))
    stats["panel_cells"] = int(panel["location_key"].nunique())
    stats["climatology_available"] = clim is not None

    # Guard: the panel must cover every cell-year a row asks for, or the merge
    # below silently produces NaN weather for real farms.
    need = set(map(tuple, rows.loc[
        rows["location_key"].notna(), ["location_key", "season_year"]
    ].drop_duplicates().itertuples(index=False)))
    have = set(map(tuple, panel[["location_key", "year"]].drop_duplicates()
                   .itertuples(index=False)))
    missing = need - have
    if missing:
        raise SystemExit(
            f"The panel is missing {len(missing)} cell-year combination(s) that "
            f"survey rows need, e.g. {sorted(missing)[:5]}. Re-run stage 2 "
            "(delete data/weather/_gee_cache to force a clean refetch)."
        )

    log(f"Loaded {len(rows)} rows, {len(panel)} cell-months over "
        f"{panel['location_key'].nunique()} cells"
        + (f", climatology for {clim['location_key'].nunique()} cells."
           if clim is not None else ", NO climatology (anomalies skipped)."))
    return rows, panel, clim


# ---------------------------------------------------------------------------
# 2. Calendar-month columns
# ---------------------------------------------------------------------------
def _pivot_months(panel, months, bands, suffix_from_month):
    block = panel[panel["month"].isin(months)]
    wide = block.pivot_table(
        index=["location_key", "year"], columns="month", values=bands, aggfunc="first"
    )
    wide.columns = [f"{band}_{suffix_from_month(month)}" for band, month in wide.columns]
    return wide.reset_index()


def add_calendar_months(rows, panel):
    wide = _pivot_months(panel, FEATURE_MONTHS, MONTHLY_BANDS, MONTH_ABBR.get)
    # Renamed before the merge rather than de-suffixed after it. Both frames
    # carry a `year`, and letting pandas resolve that into year_x/year_y leaves
    # a stray column behind that looks exactly like a real one.
    wide = wide.rename(columns={"year": "season_year"})
    out = rows.merge(wide, on=["location_key", "season_year"], how="left")

    made = [f"{b}_{MONTH_ABBR[m]}" for b in MONTHLY_BANDS for m in FEATURE_MONTHS]
    made = [c for c in made if c in out.columns]
    stats["calendar_month_columns"] = len(made)
    log(f"Built {len(made)} calendar-month columns "
        f"({len(MONTHLY_BANDS)} variables x {len(FEATURE_MONTHS)} months, "
        f"{MONTH_ABBR[FEATURE_MONTHS[0]]}-{MONTH_ABBR[FEATURE_MONTHS[-1]]}).")
    return out, made


# ---------------------------------------------------------------------------
# 3. Pre-season block
# ---------------------------------------------------------------------------
# Joined against season_year - 1, so the 2016 season reads November and
# December 2015. This is the only rainfall in the file that a lender could
# actually have seen before disbursing, which is why it is tiered ex_ante while
# every in-season month is not.
def add_preseason(rows, panel):
    block = panel[panel["month"].isin(PRESEASON_MONTHS)]
    agg = block.groupby(["location_key", "year"]).agg(
        rain_mm_preseason=("rain_mm", "sum"),
        rain_days_preseason=("rain_days", "sum"),
    ).reset_index()
    agg["season_year"] = agg["year"] + 1
    agg = agg.drop(columns="year")

    out = rows.merge(agg, on=["location_key", "season_year"], how="left")
    cov = float(out["rain_mm_preseason"].notna().mean() * 100)
    stats["preseason"] = {
        "months": [MONTH_ABBR[m] for m in PRESEASON_MONTHS],
        "read_from": "season_year - 1",
        "coverage_pct": round(cov, 1),
        "mean_mm": round(float(out["rain_mm_preseason"].mean()), 1),
    }
    log(f"Built pre-season block from the previous "
        f"{'/'.join(MONTH_ABBR[m] for m in PRESEASON_MONTHS)} "
        f"({cov:.1f}% coverage, mean {out['rain_mm_preseason'].mean():.0f} mm).")
    return out, ["rain_mm_preseason", "rain_days_preseason"]


# ---------------------------------------------------------------------------
# 4. Fixed-window season aggregates
# ---------------------------------------------------------------------------
# Sums for the things that accumulate (rainfall, rain days, degree days) and
# means for the things that do not (temperatures). Summing a temperature would
# produce a number that rises with the length of the window and means nothing.
def add_season_aggregates(rows):
    def cols(band):
        return [f"{band}_{MONTH_ABBR[m]}" for m in SEASON_MONTHS
                if f"{band}_{MONTH_ABBR[m]}" in rows.columns]

    rows["rain_mm_season"] = rows[cols("rain_mm")].sum(axis=1, min_count=1)
    rows["rain_days_season"] = rows[cols("rain_days")].sum(axis=1, min_count=1)
    rows["gdd10_season"] = rows[cols("gdd10")].sum(axis=1, min_count=1)
    rows["tmean_c_season"] = rows[cols("tmean_c")].mean(axis=1)
    rows["tmax_c_season"] = rows[cols("tmax_c")].mean(axis=1)

    # The driest single month inside the window. A season can hit a perfectly
    # normal total and still fail if one month of it was empty, and for maize
    # that month landing on flowering is the difference between a crop and no
    # crop. The total cannot express that; this can.
    rows["rain_mm_driest_season_month"] = rows[cols("rain_mm")].min(axis=1)

    made = ["rain_mm_season", "rain_days_season", "gdd10_season",
            "tmean_c_season", "tmax_c_season", "rain_mm_driest_season_month"]
    stats["season_window"] = {
        "months": [MONTH_ABBR[m] for m in SEASON_MONTHS],
        "rain_mm_season_mean": round(float(rows["rain_mm_season"].mean()), 1),
        "rain_mm_season_by_year": {
            int(y): round(float(v), 1) for y, v in
            rows.groupby("season_year")["rain_mm_season"].mean().items()
        },
        "tmean_c_season_mean": round(float(rows["tmean_c_season"].mean()), 2),
    }
    log(f"Built {len(made)} fixed-window aggregates over "
        f"{MONTH_ABBR[SEASON_MONTHS[0]]}-{MONTH_ABBR[SEASON_MONTHS[-1]]} "
        f"(mean season rainfall {rows['rain_mm_season'].mean():.0f} mm).")
    return rows, made


# ---------------------------------------------------------------------------
# 5. Plant-date-relative windows
# ---------------------------------------------------------------------------
# Indexed on an ABSOLUTE month counter (year*12 + month), not on the calendar
# month, so a December planting reads forward into the following January
# without special-casing. Rows whose window runs past the fetched panel simply
# come back NaN rather than silently wrapping to the wrong year -- which is
# what a naive modulo-12 implementation does, and it is undetectable downstream.
def add_plant_relative(rows, panel):
    lookup = panel.copy()
    lookup["abs_month"] = lookup["year"] * 12 + (lookup["month"] - 1)
    lookup = lookup.set_index(["location_key", "abs_month"])
    assert lookup.index.is_unique, "panel is not unique on (location_key, year, month)"

    plant_abs = (
        rows["plant_date"].dt.year * 12 + (rows["plant_date"].dt.month - 1)
    ).astype("Float64")

    made = []
    for band in ("rain_mm", "gdd10"):
        series = lookup[band]
        for k in PLANT_RELATIVE_OFFSETS:
            name = f"{band}_p{k}"
            target = plant_abs + k
            idx = pd.MultiIndex.from_arrays([
                rows["location_key"].astype("object"),
                target.astype("float64"),
            ])
            rows[name] = series.reindex(idx).to_numpy()
            made.append(name)

    rows["rain_mm_p0_p3"] = rows[
        [f"rain_mm_p{k}" for k in PLANT_RELATIVE_OFFSETS]
    ].sum(axis=1, min_count=len(PLANT_RELATIVE_OFFSETS))
    rows["gdd10_p0_p3"] = rows[
        [f"gdd10_p{k}" for k in PLANT_RELATIVE_OFFSETS]
    ].sum(axis=1, min_count=len(PLANT_RELATIVE_OFFSETS))
    made += ["rain_mm_p0_p3", "gdd10_p0_p3"]

    # Rain over the actual observed growing season. Month-resolution: it sums
    # whole calendar months from the planting month through the harvest month
    # inclusive, so it over-counts by up to two part-months. Kept because the
    # error is small against a 700 mm season and the alternative -- daily
    # extraction per row -- is the 23x-larger Earth Engine job stage 1 exists
    # to avoid.
    harvest_abs = (
        rows["harvest_date"].dt.year * 12 + (rows["harvest_date"].dt.month - 1)
    ).astype("Float64")
    span = (harvest_abs - plant_abs)
    both = plant_abs.notna() & harvest_abs.notna() & (span >= 0) & (span <= 11)

    totals = pd.Series(np.nan, index=rows.index, dtype="float64")
    rain = lookup["rain_mm"]
    for k in range(12):
        active = both & (span >= k)
        if not active.any():
            continue
        idx = pd.MultiIndex.from_arrays([
            rows.loc[active, "location_key"].astype("object"),
            (plant_abs[active] + k).astype("float64"),
        ])
        vals = pd.Series(rain.reindex(idx).to_numpy(), index=rows.index[active])
        totals.loc[active] = totals.loc[active].fillna(0.0).add(vals, fill_value=0.0)
    rows["rain_mm_plant_to_harvest"] = totals
    made.append("rain_mm_plant_to_harvest")

    stats["plant_relative"] = {
        "offsets": list(PLANT_RELATIVE_OFFSETS),
        "coverage_pct": {c: round(float(rows[c].notna().mean() * 100), 1)
                         for c in made},
        "rows_with_plant_date": int(rows["plant_date"].notna().sum()),
        "rows_with_both_dates_in_range": int(both.sum()),
        "month_resolution_caveat": (
            "rain_mm_plant_to_harvest sums whole calendar months and so "
            "over-counts by up to two part-months"
        ),
    }
    log(f"Built {len(made)} plant-date-relative columns "
        f"(p0-p3 = planting, vegetative, flowering, grain fill; "
        f"{rows['rain_mm_p0'].notna().mean()*100:.1f}% coverage).")
    return rows, made


# ---------------------------------------------------------------------------
# 6. Climatological normals and anomalies
# ---------------------------------------------------------------------------
# The normal itself is a feature, and a good one: it is pure geography ("this
# cell averages 780 mm a season"), constant across years by construction, and
# therefore the one rainfall variable that CANNOT be a year label. It is also
# the only one of these that is legitimately ex_ante.
def add_anomalies(rows, clim):
    if clim is None:
        stats["anomalies"] = "skipped -- no climatology file"
        log("No climatology file; anomaly features skipped. Re-run stage 2 "
            "without --skip-climatology to enable them.")
        return rows, []

    season = clim[clim["month"].isin(SEASON_MONTHS)].groupby("location_key").agg(
        rain_mm_season_normal=("clim_rain_mm", "sum"),
        rain_days_season_normal=("clim_rain_days", "sum"),
        tmean_c_season_normal=("clim_tmean_c", "mean"),
        gdd10_season_normal=("clim_gdd10", "sum"),
    ).reset_index()

    pre = clim[clim["month"].isin(PRESEASON_MONTHS)].groupby("location_key").agg(
        rain_mm_preseason_normal=("clim_rain_mm", "sum"),
    ).reset_index()

    rows = rows.merge(season, on="location_key", how="left")
    rows = rows.merge(pre, on="location_key", how="left")

    rows["rain_anom_mm_season"] = rows["rain_mm_season"] - rows["rain_mm_season_normal"]
    # Percent anomaly guards against a zero normal. No cell in western Kenya has
    # a zero March-August normal, but a divide-by-zero here would produce inf
    # values that silently poison every downstream mean and split.
    rows["rain_anom_pct_season"] = np.where(
        rows["rain_mm_season_normal"] > 0,
        rows["rain_anom_mm_season"] / rows["rain_mm_season_normal"] * 100,
        np.nan,
    )
    rows["rain_anom_mm_preseason"] = (
        rows["rain_mm_preseason"] - rows["rain_mm_preseason_normal"]
    )
    rows["tmean_anom_c_season"] = (
        rows["tmean_c_season"] - rows["tmean_c_season_normal"]
    )
    rows["gdd10_anom_season"] = rows["gdd10_season"] - rows["gdd10_season_normal"]

    made = [
        "rain_mm_season_normal", "rain_days_season_normal",
        "tmean_c_season_normal", "gdd10_season_normal",
        "rain_mm_preseason_normal",
        "rain_anom_mm_season", "rain_anom_pct_season",
        "rain_anom_mm_preseason", "tmean_anom_c_season", "gdd10_anom_season",
    ]
    stats["anomalies"] = {
        "baseline": "1991-2020",
        "rain_anom_pct_season_by_year": {
            int(y): round(float(v), 1) for y, v in
            rows.groupby("season_year")["rain_anom_pct_season"].mean().items()
        },
        "tmean_anom_c_season_by_year": {
            int(y): round(float(v), 2) for y, v in
            rows.groupby("season_year")["tmean_anom_c_season"].mean().items()
        },
    }
    log(f"Built {len(made)} normal/anomaly columns against a 1991-2020 baseline.")
    return rows, made


# ---------------------------------------------------------------------------
# 7. The year-confounding diagnostic
# ---------------------------------------------------------------------------
# For each feature, the share of total variance that lies BETWEEN season years
# rather than within them, i.e. a one-way ANOVA eta-squared with year as the
# factor. 0.0 means the feature says nothing about which season a row came
# from; 1.0 means it says nothing else.
#
# This is the number that separates the two kinds of weather feature. A cell's
# long-term normal scores ~0.00 (it is fixed geography). A raw season rainfall
# total scores high, because every cell moves together from season to season.
# An anomaly sits in between: it removes the fixed geography but keeps the
# genuine common shock, which is real signal AND a year label at the same time.
#
# Nothing is dropped on this. It is reported so the modelling package chooses
# with a measurement rather than an intuition.
def year_variance_shares(rows, feature_cols):
    shares = {}
    year = rows["season_year"]
    for col in feature_cols:
        s = pd.to_numeric(rows[col], errors="coerce")
        valid = s.notna()
        if valid.sum() < 2:
            continue
        s = s[valid]
        y = year[valid]
        total = float(((s - s.mean()) ** 2).sum())
        if total <= 0:
            shares[col] = 1.0  # constant overall: degenerate, flag it loudly
            continue
        grand = s.mean()
        between = float(
            y.to_frame("y").assign(v=s).groupby("y")["v"]
            .agg(["count", "mean"])
            .eval("count * (mean - @grand) ** 2").sum()
        )
        shares[col] = round(between / total, 4)
    return shares


def report_year_confounding(rows, feature_cols):
    shares = year_variance_shares(rows, feature_cols)
    flagged = sorted(
        [c for c, v in shares.items() if v >= YEAR_VARIANCE_WARN],
        key=lambda c: -shares[c],
    )
    stats["year_variance_share"] = shares
    stats["year_variance_warn_threshold"] = YEAR_VARIANCE_WARN
    stats["features_dominated_by_year"] = flagged
    stats["year_variance_share_summary"] = {
        "n_features": len(shares),
        "n_above_threshold": len(flagged),
        "median": round(float(np.median(list(shares.values()))), 4) if shares else None,
        "lowest_5": sorted(shares.items(), key=lambda kv: kv[1])[:5],
        "highest_5": sorted(shares.items(), key=lambda kv: -kv[1])[:5],
    }
    log(f"Year-confounding diagnostic: {len(flagged)} of {len(shares)} weather "
        f"features have >={YEAR_VARIANCE_WARN:.0%} of their variance between "
        f"seasons rather than between places.")
    if flagged:
        log(f"  worst: " + ", ".join(
            f"{c} ({shares[c]:.2f})" for c in flagged[:5]
        ))
    return shares


# ---------------------------------------------------------------------------
# 8. Save
# ---------------------------------------------------------------------------
METADATA_COLUMNS = [
    "unique_id", "season_year", "location_key",
    "weather_lat", "weather_lon", "weather_location_source",
]


def save(rows, feature_cols):
    out = rows[METADATA_COLUMNS + feature_cols].copy()

    assert out["unique_id"].is_unique, "weather features are not one row per survey row"
    assert len(out) == stats["input_rows"], (
        f"row count changed during feature construction: "
        f"{stats['input_rows']} -> {len(out)}"
    )
    # Every locatable row must have at least the season rainfall. A NaN here
    # means the panel/rows join failed for that cell-year, which would otherwise
    # only surface as a mysteriously weak model.
    locatable = out["location_key"].notna()
    unresolved = int(out.loc[locatable, "rain_mm_season"].isna().sum())
    assert unresolved == 0, (
        f"{unresolved} locatable rows have no season rainfall; the panel join "
        "is incomplete."
    )

    coverage = {c: round(float(out[c].notna().mean() * 100), 1) for c in feature_cols}
    stats["output_shape"] = list(out.shape)
    stats["feature_columns"] = feature_cols
    stats["coverage_pct"] = coverage
    stats["features_below_full_coverage"] = {
        c: v for c, v in coverage.items() if v < 99.9
    }

    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)

    log(f"Saved weather features: {OUTPUT_PATH} ({out.shape[0]} x {out.shape[1]})")
    log(f"Saved audit stats:      {STATS_PATH}")
    log(f"{len(feature_cols)} weather features built. "
        "Next: python scripts/build_features.py")
    return out


def run():
    rows, panel, clim = load_inputs()

    features = []
    rows, made = add_calendar_months(rows, panel); features += made
    rows, made = add_preseason(rows, panel); features += made
    rows, made = add_season_aggregates(rows); features += made
    rows, made = add_plant_relative(rows, panel); features += made
    rows, made = add_anomalies(rows, clim); features += made

    report_year_confounding(rows, features)
    return save(rows, features)


if __name__ == "__main__":
    run()
