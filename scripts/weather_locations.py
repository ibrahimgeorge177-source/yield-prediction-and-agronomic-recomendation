"""
Kenya Maize Weather Feature Pipeline -- Stage 1: Location Resolution
====================================================================

Resolves ONE (latitude, longitude, season_year) triple per survey row, then
collapses those rows into the small set of distinct raster cells that actually
need to be queried from Google Earth Engine.

This stage is deliberately separated from the Earth Engine call
(scripts/fetch_gee_weather.py) for two reasons:

  1. It is the only stage with judgement calls in it (which coordinate to
     trust, what counts as a valid fix, which calendar year a season belongs
     to). Those need to be inspectable and testable without a network round
     trip or a Google account.
  2. It is what makes the Earth Engine call cheap. The survey has 22,279
     distinct field coordinates, but CHIRPS is a 0.05 degree grid: snapped to
     that grid the same 23,674 rows collapse to ~950 distinct cells. Querying
     per row would be a ~23x waste and would hit Earth Engine's request limits;
     querying per cell finishes in minutes.

Run:
    python scripts/weather_locations.py

Reads:  data/cleaned/kenya_maize_cleaned.csv
Writes: data/weather/weather_points.csv        (the ~950 cells to query)
        data/weather/row_locations.csv         (unique_id -> cell, season year)
        data/weather/weather_location_stats.json

DESIGN PRINCIPLE
----------------
Inherited from the two pipelines upstream: never silently invent a location.
Every row carries `weather_location_source` recording which rung of the
fallback ladder produced its coordinate, so a downstream model can condition on
(or exclude) rows whose weather was read from a district centroid 20 km away
rather than from the farmer's own GPS fix.

DEPENDENCIES: numpy + pandas only, matching clean_kenya_maize.py and
build_features.py so this runs under the same interpreter as the rest of the
project. No Earth Engine import here on purpose -- this stage must stay
runnable on a machine with no Google credentials.
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
POINTS_PATH = WEATHER_DIR / "weather_points.csv"
ROW_LOCATIONS_PATH = WEATHER_DIR / "row_locations.csv"
STATS_PATH = WEATHER_DIR / "weather_location_stats.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# CHIRPS native resolution. ERA5-Land is coarser (0.1 deg), so snapping to the
# finer of the two grids and sampling both datasets at the same cell centres
# loses nothing: several CHIRPS cells simply share an ERA5-Land pixel, which is
# the physically correct outcome rather than an approximation.
CHIRPS_GRID_DEG = 0.05

# Kenya's national bounding box, generously padded. Used only to reject
# impossible fixes (0,0 nulls, transposed lat/lon, decimal-shift typos), not to
# clip real coordinates. The observed survey extent is far tighter --
# lat [-1.16, 1.23], lon [33.99, 37.83] -- so this box will only ever fire on
# genuinely broken values.
KENYA_BBOX = {"lat_min": -5.0, "lat_max": 5.5, "lon_min": 33.5, "lon_max": 42.0}

SURVEY_YEARS = (2016, 2017, 2018, 2019, 2020)

stats = {}


def log(msg):
    print(f"[weather_locations] {msg}")


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------
LOCATION_INPUT_COLUMNS = [
    "unique_id", "district", "site", "year",
    "field_latitude", "field_longitude",
    "site_latitude", "site_longitude",
    "field_gps_flagged_invalid",
    "plant_date", "harvest_date",
]


def load_cleaned():
    log(f"Loading cleaned dataset: {CLEANED_CSV_PATH.name}")
    df = pd.read_csv(CLEANED_CSV_PATH, low_memory=False, usecols=LOCATION_INPUT_COLUMNS)
    stats["input_rows"] = int(len(df))
    for col in ("plant_date", "harvest_date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    log(f"Loaded {len(df)} rows.")
    return df


# ---------------------------------------------------------------------------
# 2. Validate coordinate pairs
# ---------------------------------------------------------------------------
def _valid_coords(lat, lon):
    """A coordinate pair is usable only if BOTH halves are present and the pair
    lands inside Kenya. Checking the pair rather than each column separately
    matters: a row with a latitude and no longitude is not half-locatable, it is
    unlocatable, and pandas' column-wise notna() will not tell you that."""
    lat = lat.astype("float64")
    lon = lon.astype("float64")
    return (
        lat.notna() & lon.notna()
        & lat.between(KENYA_BBOX["lat_min"], KENYA_BBOX["lat_max"])
        & lon.between(KENYA_BBOX["lon_min"], KENYA_BBOX["lon_max"])
        # Exact zeros are the classic "GPS never acquired a fix" sentinel and
        # (0, 0) is in the Atlantic, but a real Kenyan longitude is never near
        # 0 either, so a zero in EITHER column condemns the pair.
        & (lat != 0.0) & (lon != 0.0)
    )


# ---------------------------------------------------------------------------
# 3. District centroids -- the last rung of the fallback ladder
# ---------------------------------------------------------------------------
# Built from the survey's own valid field fixes rather than from an external
# administrative boundary file. Two reasons: it needs no new dependency or
# shapefile, and it centres each district on where these farmers actually are
# rather than on the geometric centre of an administrative polygon that may be
# half national park.
#
# MEDIAN, not mean: a single surviving typo in a district with few fixes would
# drag a mean across the country, and the median is unmoved by it.
def build_district_centroids(df, field_ok):
    valid = df.loc[field_ok, ["district", "field_latitude", "field_longitude"]]
    cent = valid.groupby("district").agg(
        lat=("field_latitude", "median"),
        lon=("field_longitude", "median"),
        n_fixes=("field_latitude", "size"),
    )
    # A centroid from one or two fixes is not a centroid, it is one farm. Kept
    # anyway (it is still the best available estimate for that district) but
    # counted, so the documentation can state how many rows lean on a thin one.
    stats["district_centroids"] = {
        "n_districts": int(len(cent)),
        "districts_from_fewer_than_5_fixes": sorted(
            cent.index[cent["n_fixes"] < 5].tolist()
        ),
        "median_fixes_per_district": int(cent["n_fixes"].median()),
    }
    log(f"Built {len(cent)} district centroids from valid field GPS "
        f"(median {int(cent['n_fixes'].median())} fixes per district).")
    return cent


# ---------------------------------------------------------------------------
# 4. Resolve one coordinate per row
# ---------------------------------------------------------------------------
# The ladder, best precision first:
#   field           -- the farmer's own plot fix (97.8% of rows)
#   site            -- the enumeration site centroid, ~village scale (0.5%)
#   district_centroid -- median of that district's field fixes (1.7%)
#
# Rung 2 and 3 are both coarser than the 5.5 km CHIRPS cell we are about to
# snap to, which is the honest way to read this: for rainfall, a site fallback
# is close to free, and a district fallback is a real approximation. The
# `weather_location_source` column carries that distinction downstream instead
# of burying it.
def resolve_row_locations(df):
    field_ok = _valid_coords(df["field_latitude"], df["field_longitude"])
    # field_gps_flagged_invalid is the cleaning pipeline's own verdict on the
    # fix; honour it rather than re-deriving one.
    if "field_gps_flagged_invalid" in df.columns:
        flagged = df["field_gps_flagged_invalid"].astype("string").isin(
            ["True", "TRUE", "true", "1"]
        )
        field_ok &= ~flagged
    site_ok = _valid_coords(df["site_latitude"], df["site_longitude"])

    centroids = build_district_centroids(df, field_ok)
    dist_lat = df["district"].map(centroids["lat"])
    dist_lon = df["district"].map(centroids["lon"])
    dist_ok = dist_lat.notna() & dist_lon.notna()

    lat = pd.Series(np.nan, index=df.index, dtype="float64")
    lon = pd.Series(np.nan, index=df.index, dtype="float64")
    source = pd.Series("none", index=df.index, dtype="object")

    for mask, la, lo, name in (
        (dist_ok, dist_lat, dist_lon, "district_centroid"),
        (site_ok, df["site_latitude"], df["site_longitude"], "site"),
        (field_ok, df["field_latitude"], df["field_longitude"], "field"),
    ):
        # Applied worst-first so each better rung overwrites the one below it.
        lat[mask] = la[mask].astype("float64")
        lon[mask] = lo[mask].astype("float64")
        source[mask] = name

    df["weather_lat"] = lat
    df["weather_lon"] = lon
    df["weather_location_source"] = source

    counts = source.value_counts().to_dict()
    stats["location_source_counts"] = {k: int(v) for k, v in counts.items()}
    stats["location_source_pct"] = {
        k: round(float(v) / len(df) * 100, 2) for k, v in counts.items()
    }
    stats["rows_unlocatable"] = int((source == "none").sum())
    log("Resolved coordinates: "
        + ", ".join(f"{k}={v} ({stats['location_source_pct'][k]}%)"
                    for k, v in sorted(counts.items(), key=lambda x: -x[1]))
        + ".")
    return df


# ---------------------------------------------------------------------------
# 5. Season year
# ---------------------------------------------------------------------------
# Every row in this file is the Kenyan LONG RAINS season, which opens and
# closes inside a single calendar year (planting Feb-May, harvest Jun-Oct), so
# one calendar year fully contains one season and `year` can be used directly.
# That is an assertion about the data, not an assumption, so it is checked:
# where a plant_date exists, its calendar year must agree with `year`.
#
# It agrees on 20,878 of 20,934 dated rows (99.7%). The 56 exceptions are
# December plantings recorded against the following season, which is a real
# (if unusual) early planting rather than a data error -- they keep `year` as
# their season, since that is the season whose harvest was surveyed.
SEASON_YEAR_MISMATCH_TOLERANCE = 0.01


def assign_season_year(df):
    df["season_year"] = df["year"].astype("int64")

    dated = df["plant_date"].notna()
    mismatch = dated & (df["plant_date"].dt.year != df["season_year"])
    rate = float(mismatch.sum()) / max(int(dated.sum()), 1)

    stats["season_year"] = {
        "dated_rows": int(dated.sum()),
        "plant_year_mismatches": int(mismatch.sum()),
        "plant_year_mismatch_pct": round(rate * 100, 2),
        "mismatch_plant_months": {
            int(k): int(v) for k, v in
            df.loc[mismatch, "plant_date"].dt.month.value_counts().sort_index().items()
        },
    }
    assert rate < SEASON_YEAR_MISMATCH_TOLERANCE, (
        f"{rate*100:.1f}% of dated rows have a plant_date in a different calendar "
        f"year than `year`. The one-season-per-calendar-year assumption that lets "
        f"weather be keyed on `year` no longer holds; re-check whether a short "
        f"rains season has entered the file before trusting these features."
    )
    log(f"Season year = survey year; plant_date confirms it on "
        f"{100-stats['season_year']['plant_year_mismatch_pct']:.1f}% of "
        f"{int(dated.sum())} dated rows "
        f"({int(mismatch.sum())} December-planting exceptions retained).")
    return df


# ---------------------------------------------------------------------------
# 6. Snap to the CHIRPS grid
# ---------------------------------------------------------------------------
# Snapping to the CELL CENTRE, not to the corner. Sampling a raster at a corner
# is a coin flip between four pixels that floating-point noise decides; the
# centre is unambiguously inside the intended pixel.
#
# The key is built from the rounded centre so that it round-trips through CSV
# exactly. Reconstructing a float key from text is the sort of thing that
# silently produces a 5% join failure three scripts later.
def snap_to_grid(df, res=CHIRPS_GRID_DEG):
    locatable = df["weather_location_source"] != "none"

    cell_lat = np.floor(df["weather_lat"] / res) * res + res / 2
    cell_lon = np.floor(df["weather_lon"] / res) * res + res / 2
    df["cell_lat"] = cell_lat.round(4).where(locatable)
    df["cell_lon"] = cell_lon.round(4).where(locatable)
    df["location_key"] = (
        df["cell_lat"].map(lambda v: "" if pd.isna(v) else f"{v:+08.4f}")
        + "_"
        + df["cell_lon"].map(lambda v: "" if pd.isna(v) else f"{v:+09.4f}")
    ).where(locatable)

    # Displacement introduced by snapping. Bounded by half a cell diagonal
    # (~3.9 km at this latitude) by construction, but reported rather than
    # asserted so the documentation can quote a real number.
    dlat_km = (df["weather_lat"] - df["cell_lat"]).abs() * 110.57
    dlon_km = (df["weather_lon"] - df["cell_lon"]).abs() * 111.32 * np.cos(
        np.radians(df["weather_lat"])
    )
    disp = np.sqrt(dlat_km ** 2 + dlon_km ** 2)

    stats["grid_snapping"] = {
        "grid_deg": res,
        "grid_km_approx": round(res * 111.32, 2),
        "distinct_exact_coords": int(
            df.loc[locatable].groupby(["weather_lat", "weather_lon"]).ngroups
        ),
        "distinct_cells": int(df["location_key"].nunique()),
        "distinct_cell_years": int(
            df.loc[locatable].groupby(["location_key", "season_year"]).ngroups
        ),
        "snap_displacement_km_median": round(float(disp.median()), 2),
        "snap_displacement_km_max": round(float(disp.max()), 2),
    }
    log(f"Snapped to {res} deg grid: {stats['grid_snapping']['distinct_exact_coords']} "
        f"distinct coordinates collapse to {df['location_key'].nunique()} cells "
        f"(median displacement {stats['grid_snapping']['snap_displacement_km_median']} km). "
        f"This is the {stats['grid_snapping']['distinct_exact_coords'] // max(df['location_key'].nunique(), 1)}x "
        f"reduction that makes the Earth Engine call cheap.")
    return df


# ---------------------------------------------------------------------------
# 7. Emit
# ---------------------------------------------------------------------------
def save(df):
    WEATHER_DIR.mkdir(parents=True, exist_ok=True)

    locatable = df["location_key"].notna()

    points = (
        df.loc[locatable]
        .groupby("location_key")
        .agg(
            cell_lat=("cell_lat", "first"),
            cell_lon=("cell_lon", "first"),
            n_rows=("unique_id", "size"),
            n_districts=("district", "nunique"),
            first_year=("season_year", "min"),
            last_year=("season_year", "max"),
        )
        .reset_index()
        .sort_values("location_key")
        .reset_index(drop=True)
    )
    # Guard: one key must mean exactly one cell centre. If the string formatting
    # above ever collides two cells onto one key, every weather value joined
    # downstream would be silently wrong for one of them.
    assert points["location_key"].is_unique, "location_key collision"
    assert len(points) == df.loc[locatable].groupby(["cell_lat", "cell_lon"]).ngroups, (
        "location_key does not partition the cells one-to-one"
    )

    row_locations = df[[
        "unique_id", "district", "site", "year", "season_year",
        "weather_lat", "weather_lon", "weather_location_source",
        "cell_lat", "cell_lon", "location_key",
    ]].copy()

    points.to_csv(POINTS_PATH, index=False)
    row_locations.to_csv(ROW_LOCATIONS_PATH, index=False)

    stats["points_written"] = int(len(points))
    stats["rows_written"] = int(len(row_locations))
    stats["rows_per_cell"] = {
        "median": int(points["n_rows"].median()),
        "max": int(points["n_rows"].max()),
        "cells_with_one_row": int((points["n_rows"] == 1).sum()),
    }
    stats["years_to_fetch"] = list(SURVEY_YEARS)

    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)

    log(f"Saved query points:   {POINTS_PATH} ({len(points)} cells)")
    log(f"Saved row locations:  {ROW_LOCATIONS_PATH} ({len(row_locations)} rows)")
    log(f"Saved audit stats:    {STATS_PATH}")
    return points, row_locations


def run():
    df = load_cleaned()
    df = resolve_row_locations(df)
    df = assign_season_year(df)
    df = snap_to_grid(df)
    return save(df)


if __name__ == "__main__":
    run()
