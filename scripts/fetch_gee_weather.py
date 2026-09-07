"""
Kenya Maize Weather Feature Pipeline -- Stage 2: Google Earth Engine Extraction
==============================================================================

Pulls a monthly rainfall and temperature panel for the ~950 grid cells emitted
by scripts/weather_locations.py, straight out of Google Earth Engine's public
catalogue. No manual downloads, no station files, no shapefiles.

    Rainfall     UCSB-CHG/CHIRPS/DAILY          0.05 deg (5,566 m), 1981-present
                 band `precipitation`, mm/day -> summed to a monthly total
    Temperature  ECMWF/ERA5_LAND/DAILY_AGGR     0.1 deg (11,132 m), 1950-present
                 bands `temperature_2m`, `_min`, `_max`, Kelvin -> monthly
                 means in Celsius, plus growing degree days

Run:
    python scripts/fetch_gee_weather.py --project YOUR_GCP_PROJECT_ID

    # or set it once and drop the flag
    setx EE_PROJECT YOUR_GCP_PROJECT_ID     (Windows, new shell after)
    export EE_PROJECT=YOUR_GCP_PROJECT_ID   (bash)

First run only, to mint credentials in a browser:
    earthengine authenticate

Reads:  data/weather/weather_points.csv
Writes: data/weather/monthly_weather_panel.csv        (cell x year x month)
        data/weather/monthly_weather_climatology.csv  (cell x month, 1991-2020)
        data/weather/gee_fetch_stats.json
        data/weather/_gee_cache/*.csv                 (resume cache, disposable)

WHY THE AGGREGATION HAPPENS SERVER-SIDE
---------------------------------------
The naive version of this script downloads daily values and sums them in
pandas: 951 cells x 2,192 days x 2 datasets is ~4.2 million numbers to move
over the wire, and Earth Engine will refuse the request long before that
finishes. Instead every monthly sum, mean and degree-day accumulation below is
expressed as an Earth Engine computation, and only the 951-row monthly result
crosses the network. That is the difference between a script that runs in
minutes and one that times out.

WHY IT IS RESUMABLE
-------------------
Earth Engine is a shared cluster: individual requests fail with transient
timeouts and rate limits, and a 72-request job that loses everything on
request 71 is unusable. Each (year, month) result is cached to its own CSV
under _gee_cache/ the moment it lands, so a re-run picks up exactly where it
stopped. Delete that directory to force a clean refetch.

DEPENDENCIES: earthengine-api, plus the numpy/pandas the rest of the project
already uses. This is the ONLY script in the project that needs credentials or
a network connection; stages 1 and 3 stay runnable offline on purpose.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
WEATHER_DIR = ROOT / "data" / "weather"
POINTS_PATH = WEATHER_DIR / "weather_points.csv"
PANEL_PATH = WEATHER_DIR / "monthly_weather_panel.csv"
CLIMATOLOGY_PATH = WEATHER_DIR / "monthly_weather_climatology.csv"
STATS_PATH = WEATHER_DIR / "gee_fetch_stats.json"
CACHE_DIR = WEATHER_DIR / "_gee_cache"


# ---------------------------------------------------------------------------
# Environment defaults
# ---------------------------------------------------------------------------
# A ten-line .env reader rather than a python-dotenv dependency. Every other
# script in this project runs on numpy + pandas alone, and adding a package to
# the install instructions so that one variable can be read from a file is a
# bad trade.
#
# Precedence, highest first: a real environment variable, then .env.local
# (gitignored, per-developer), then .env (committed, the project default).
# os.environ.setdefault is what implements that -- whoever sets a key first
# keeps it, so nothing here can clobber a variable the shell already exported.
def _load_dotenv():
    for name in (".env.local", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            # Strip one layer of matching quotes, so EE_PROJECT="foo" and
            # EE_PROJECT=foo both work.
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key.strip(), value)


# Called at import time, not inside main(), because the argparse default for
# --project reads os.environ when the parser is BUILT. Loading any later means
# the flag silently defaults to None however good the .env file is.
_load_dotenv()

# ---------------------------------------------------------------------------
# Earth Engine assets
# ---------------------------------------------------------------------------
CHIRPS_ID = "UCSB-CHG/CHIRPS/DAILY"
CHIRPS_SCALE_M = 5566
ERA5_ID = "ECMWF/ERA5_LAND/DAILY_AGGR"
ERA5_SCALE_M = 11132

# ---------------------------------------------------------------------------
# What to fetch
# ---------------------------------------------------------------------------
# 2015 is included for one reason only: the 2016 season's pre-planting soil
# moisture comes from November and December 2015. Every other year is a survey
# season in its own right.
FETCH_YEARS = (2015, 2016, 2017, 2018, 2019, 2020)

# All twelve months, even though only January-October ever become row features.
# The marginal cost is two extra requests per year and it means a later change
# of mind about the season window (or anyone wanting the short rains) needs no
# second trip to Earth Engine.
FETCH_MONTHS = tuple(range(1, 13))

# The WMO standard 30-year climate normal period, and the one CHIRPS drought
# products use. Ends in 2020 so the normals are not partly defined by the very
# seasons we are computing anomalies for -- 2016-2020 contribute 5 of 30 years
# rather than dominating the baseline.
CLIMATOLOGY_YEARS = (1991, 2020)

# Maize cardinal temperatures. Growth is taken to stop below 10 C and to stop
# accelerating above 30 C, so both ends of the daily range are clamped into
# [10, 30] before the degree-day average -- the standard capped GDD form. The
# uncapped version credits a 38 C day as better for the crop than a 29 C one,
# which is exactly backwards during flowering.
GDD_BASE_C = 10.0
GDD_CAP_C = 30.0

# A "rain day" threshold of 1 mm, the WMO convention. Below that the water
# evaporates before it reaches the root zone, so counting 0.2 mm traces as rain
# days would make a dry month look well distributed.
RAIN_DAY_THRESHOLD_MM = 1.0

# Request shaping. 951 points sits under Earth Engine's 5,000-element getInfo
# ceiling and could go in one request, but a failure then costs the whole
# month; 300 keeps each request small enough to retry cheaply.
DEFAULT_CHUNK_SIZE = 300
MAX_RETRIES = 5
RETRY_BASE_SLEEP_S = 4.0

# ERA5-Land is masked over open water. A handful of these cells sit on the
# Lake Victoria shoreline and return nothing at the point, so any cell that
# comes back null is re-sampled as the mean over a buffer wide enough to reach
# the nearest land pixel. Applied ONLY to nulls -- buffering everything would
# smooth away the real spatial variation we are here to capture.
NULL_FILL_BUFFER_M = 20000

stats = {"requests": 0, "retries": 0, "null_filled_cells": {}}


def log(msg):
    print(f"[fetch_gee_weather] {msg}", flush=True)


# ---------------------------------------------------------------------------
# 1. Authentication
# ---------------------------------------------------------------------------
# Service-account authentication, when EE_SERVICE_ACCOUNT_KEY points at a key
# file. Preferred over interactive credentials for this pipeline because the
# fetch is long, unattended and re-runnable: an OAuth token minted by
# `earthengine authenticate` expires and takes a browser to renew, while a
# service account just works.
#
# The env var holds a PATH, never the key body. A private key pasted into .env
# would be committed by the next `git add .` -- that is not a hypothetical, it
# is the single most common way keys reach public repositories.
def _service_account_credentials(ee, key_path):
    path = Path(os.path.expanduser(key_path)).resolve()
    if not path.exists():
        sys.exit(
            f"EE_SERVICE_ACCOUNT_KEY points at a file that does not exist:\n"
            f"    {path}\n\n"
            "Either fix the path in .env, or blank that line out to fall back\n"
            "to interactive credentials from `earthengine authenticate`."
        )

    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"Could not read the service-account key at {path}: {exc}")

    email = info.get("client_email")
    if not email:
        sys.exit(
            f"{path} has no `client_email`, so it is not a service-account key.\n"
            "Download the JSON key from the Cloud console under\n"
            "IAM & Admin > Service Accounts > (account) > Keys."
        )

    # Guard: a key inside the working tree is one `git add .` from being
    # published. Refuse rather than warn -- a warning scrolls past.
    try:
        path.relative_to(ROOT)
    except ValueError:
        pass  # outside the repo, which is where it belongs
    else:
        sys.exit(
            f"The service-account key is inside the repository:\n"
            f"    {path}\n\n"
            "That is one `git add .` away from publishing a private key. Move\n"
            "it outside the repo and update EE_SERVICE_ACCOUNT_KEY in .env:\n"
            "    ~/.config/earthengine/<project>-service-account.json"
        )

    log(f"Authenticating as service account {email}")
    return ee.ServiceAccountCredentials(email, str(path))


def init_earth_engine(project):
    try:
        import ee
    except ImportError:
        sys.exit(
            "earthengine-api is not installed.\n"
            "    pip install earthengine-api\n"
        )

    if not project:
        sys.exit(
            "An Earth Engine / Google Cloud project ID is required.\n"
            f"    Expected EE_PROJECT in {ROOT / '.env'}, which appears to be\n"
            "    missing or empty. Either restore that line:\n"
            "        EE_PROJECT=ontrack-356205\n"
            "    or pass it explicitly:\n"
            "        python scripts/fetch_gee_weather.py --project YOUR_PROJECT_ID\n\n"
            "If you have never used Earth Engine before: sign up at\n"
            "https://code.earthengine.google.com/register, which creates or\n"
            "attaches a Cloud project; the ID is shown in the Earth Engine Code\n"
            "Editor's top-right project picker."
        )

    key_path = os.environ.get("EE_SERVICE_ACCOUNT_KEY", "").strip()
    credentials = _service_account_credentials(ee, key_path) if key_path else None

    try:
        if credentials is not None:
            ee.Initialize(credentials, project=project)
        else:
            ee.Initialize(project=project)
    except Exception as exc:  # noqa: BLE001 -- surface the real cause verbatim
        hint = (
            "  1. The service account has not been granted Earth Engine access\n"
            f"     -> https://code.earthengine.google.com/register?project={project}\n"
            "        then add the service account's email as a project member\n"
            "  2. The Earth Engine API is not enabled on the project\n"
            "     -> https://console.cloud.google.com/apis/library/"
            "earthengine.googleapis.com\n"
            "  3. Wrong project ID (use the ID, not the display name)\n"
        ) if credentials is not None else (
            "  1. No credentials yet   -> run: earthengine authenticate\n"
            "  2. Project not registered for Earth Engine\n"
            "     -> https://code.earthengine.google.com/register\n"
            "  3. Wrong project ID (use the ID, not the display name)\n"
        )
        sys.exit(
            f"Could not initialise Earth Engine for project '{project}':\n"
            f"    {exc}\n\nMost likely one of:\n{hint}"
        )

    log(f"Earth Engine initialised on project '{project}' "
        f"({'service account' if credentials is not None else 'user credentials'}).")
    return ee


# ---------------------------------------------------------------------------
# 2. Monthly composites, built server-side
# ---------------------------------------------------------------------------
def monthly_rain_image(ee, year, month):
    """One image whose bands are that month's rainfall total and rain-day count."""
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    daily = ee.ImageCollection(CHIRPS_ID).filterDate(start, end).select("precipitation")

    total = daily.sum().rename("rain_mm")
    rain_days = daily.map(
        lambda img: img.gte(RAIN_DAY_THRESHOLD_MM)
    ).sum().rename("rain_days")
    return total.addBands(rain_days)


def monthly_temp_image(ee, year, month):
    """One image whose bands are that month's mean/max/min air temperature in
    Celsius and its accumulated capped growing degree days."""
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    daily = ee.ImageCollection(ERA5_ID).filterDate(start, end)

    tmean = daily.select("temperature_2m").mean().subtract(273.15).rename("tmean_c")
    # The monthly mean of the DAILY maxima, not the single hottest day of the
    # month. The daily-maximum mean is the quantity heat-stress work is built
    # on; a monthly extremum is one observation and mostly noise.
    tmax = daily.select("temperature_2m_max").mean().subtract(273.15).rename("tmax_c")
    tmin = daily.select("temperature_2m_min").mean().subtract(273.15).rename("tmin_c")

    def degree_days(img):
        # .min(CAP) is an element-wise minimum, so it CAPS at 30; .max(BASE)
        # floors at 10. Clamping before averaging (rather than clamping the
        # average) is what makes this the capped form.
        hi = img.select("temperature_2m_max").subtract(273.15).min(GDD_CAP_C).max(GDD_BASE_C)
        lo = img.select("temperature_2m_min").subtract(273.15).min(GDD_CAP_C).max(GDD_BASE_C)
        return hi.add(lo).divide(2).subtract(GDD_BASE_C).rename("gdd10")

    gdd = daily.map(degree_days).sum()
    return tmean.addBands(tmax).addBands(tmin).addBands(gdd)


# ---------------------------------------------------------------------------
# 3. Climatological normals, built server-side
# ---------------------------------------------------------------------------
# For month m: sum each of the 30 years' daily rainfall within month m, then
# average those 30 annual totals. Doing it in that order matters -- averaging
# DAILY rainfall across 30 years and multiplying by 30 days gives a different
# (and wrong) answer whenever month lengths or missing days differ.
def climatology_rain_image(ee, month):
    y0, y1 = CLIMATOLOGY_YEARS
    years = ee.List.sequence(y0, y1)
    chirps = ee.ImageCollection(CHIRPS_ID).select("precipitation")

    def one_year(y):
        start = ee.Date.fromYMD(ee.Number(y).int(), month, 1)
        block = chirps.filterDate(start, start.advance(1, "month"))
        return block.sum().rename("rain_mm").addBands(
            block.map(lambda i: i.gte(RAIN_DAY_THRESHOLD_MM)).sum().rename("rain_days")
        )

    return ee.ImageCollection(years.map(one_year)).mean().rename(
        ["clim_rain_mm", "clim_rain_days"]
    )


def climatology_temp_image(ee, month):
    y0, y1 = CLIMATOLOGY_YEARS
    years = ee.List.sequence(y0, y1)
    era5 = ee.ImageCollection(ERA5_ID)

    def one_year(y):
        start = ee.Date.fromYMD(ee.Number(y).int(), month, 1)
        block = era5.filterDate(start, start.advance(1, "month"))
        tmean = block.select("temperature_2m").mean().subtract(273.15)

        def degree_days(img):
            hi = img.select("temperature_2m_max").subtract(273.15).min(GDD_CAP_C).max(GDD_BASE_C)
            lo = img.select("temperature_2m_min").subtract(273.15).min(GDD_CAP_C).max(GDD_BASE_C)
            return hi.add(lo).divide(2).subtract(GDD_BASE_C)

        return tmean.rename("tmean_c").addBands(
            block.map(degree_days).sum().rename("gdd10")
        )

    return ee.ImageCollection(years.map(one_year)).mean().rename(
        ["clim_tmean_c", "clim_gdd10"]
    )


# ---------------------------------------------------------------------------
# 4. Sampling with retries
# ---------------------------------------------------------------------------
def _feature_collection(ee, points, buffer_m=0):
    feats = []
    for key, lat, lon in points:
        geom = ee.Geometry.Point([float(lon), float(lat)])
        if buffer_m:
            geom = geom.buffer(buffer_m)
        feats.append(ee.Feature(geom, {"location_key": key}))
    return ee.FeatureCollection(feats)


def _sample_once(ee, image, points, scale, buffer_m=0):
    fc = _feature_collection(ee, points, buffer_m)
    sampled = image.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        scale=scale,
        # tileScale trades speed for memory headroom. Points scattered over a
        # 400 km span otherwise push reduceRegions into out-of-memory errors
        # that no amount of retrying will fix.
        tileScale=4,
    )
    stats["requests"] += 1
    return sampled.getInfo()["features"]


def sample_image(ee, image, points, scale, bands, label, chunk_size):
    """Sample `image` at every point, chunked and retried, then buffer-fill any
    cell the raster masked out."""
    out = []
    for i in range(0, len(points), chunk_size):
        chunk = points[i:i + chunk_size]
        for attempt in range(MAX_RETRIES):
            try:
                feats = _sample_once(ee, image, chunk, scale)
                break
            except Exception as exc:  # noqa: BLE001 -- any EE error is retryable
                stats["retries"] += 1
                if attempt == MAX_RETRIES - 1:
                    raise RuntimeError(
                        f"{label}: chunk {i}-{i+len(chunk)} failed after "
                        f"{MAX_RETRIES} attempts: {exc}"
                    ) from exc
                sleep = RETRY_BASE_SLEEP_S * (2 ** attempt)
                log(f"  {label}: chunk {i} attempt {attempt+1} failed "
                    f"({type(exc).__name__}); retrying in {sleep:.0f}s")
                time.sleep(sleep)

        for f in feats:
            props = f["properties"]
            row = {"location_key": props["location_key"]}
            for b in bands:
                row[b] = props.get(b)
            out.append(row)

    frame = pd.DataFrame(out)

    # Buffer-fill masked cells (see NULL_FILL_BUFFER_M).
    missing = frame[bands].isna().any(axis=1)
    if missing.any():
        keys = set(frame.loc[missing, "location_key"])
        refill = [p for p in points if p[0] in keys]
        log(f"  {label}: {len(refill)} cell(s) masked at the point; re-sampling "
            f"over a {NULL_FILL_BUFFER_M//1000} km buffer.")
        try:
            feats = _sample_once(ee, image, refill, scale, buffer_m=NULL_FILL_BUFFER_M)
            filled = {f["properties"]["location_key"]: f["properties"] for f in feats}
            for idx in frame.index[missing]:
                props = filled.get(frame.at[idx, "location_key"], {})
                for b in bands:
                    if pd.isna(frame.at[idx, b]) and props.get(b) is not None:
                        frame.at[idx, b] = props[b]
            stats["null_filled_cells"][label] = sorted(keys)
        except Exception as exc:  # noqa: BLE001
            # A failed fill is not a failed month. Leave the nulls in place --
            # stage 3 reports them rather than inventing a value.
            log(f"  {label}: buffer fill failed ({exc}); leaving nulls.")

    return frame


# ---------------------------------------------------------------------------
# 5. Orchestration, with an on-disk resume cache
# ---------------------------------------------------------------------------
RAIN_BANDS = ["rain_mm", "rain_days"]
TEMP_BANDS = ["tmean_c", "tmax_c", "tmin_c", "gdd10"]


def _cached(name, build):
    """Return the cached frame for `name`, or build, cache and return it."""
    path = CACHE_DIR / f"{name}.csv"
    if path.exists():
        return pd.read_csv(path), True
    frame = build()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame, False


def fetch_panel(ee, points, years, months, chunk_size):
    blocks = []
    total = len(years) * len(months)
    done = 0
    for year in years:
        for month in months:
            done += 1
            tag = f"{year}-{month:02d}"

            rain, rain_hit = _cached(
                f"rain_{tag}",
                lambda y=year, m=month: sample_image(
                    ee, monthly_rain_image(ee, y, m), points, CHIRPS_SCALE_M,
                    RAIN_BANDS, f"rain {y}-{m:02d}", chunk_size,
                ),
            )
            temp, temp_hit = _cached(
                f"temp_{tag}",
                lambda y=year, m=month: sample_image(
                    ee, monthly_temp_image(ee, y, m), points, ERA5_SCALE_M,
                    TEMP_BANDS, f"temp {y}-{m:02d}", chunk_size,
                ),
            )

            block = rain.merge(temp, on="location_key", how="outer")
            block.insert(1, "year", year)
            block.insert(2, "month", month)
            blocks.append(block)

            mark = "cached" if (rain_hit and temp_hit) else "fetched"
            log(f"  [{done:>2}/{total}] {tag} {mark} "
                f"({len(block)} cells, mean rain {block['rain_mm'].mean():.0f} mm, "
                f"mean temp {block['tmean_c'].mean():.1f} C)")

    return pd.concat(blocks, ignore_index=True)


def fetch_climatology(ee, points, months, chunk_size):
    blocks = []
    for month in months:
        rain, _ = _cached(
            f"clim_rain_{month:02d}",
            lambda m=month: sample_image(
                ee, climatology_rain_image(ee, m), points, CHIRPS_SCALE_M,
                ["clim_rain_mm", "clim_rain_days"], f"clim rain m{m:02d}", chunk_size,
            ),
        )
        temp, _ = _cached(
            f"clim_temp_{month:02d}",
            lambda m=month: sample_image(
                ee, climatology_temp_image(ee, m), points, ERA5_SCALE_M,
                ["clim_tmean_c", "clim_gdd10"], f"clim temp m{m:02d}", chunk_size,
            ),
        )
        block = rain.merge(temp, on="location_key", how="outer")
        block.insert(1, "month", month)
        blocks.append(block)
        log(f"  climatology month {month:02d}: mean normal rainfall "
            f"{block['clim_rain_mm'].mean():.0f} mm")
    return pd.concat(blocks, ignore_index=True)


# ---------------------------------------------------------------------------
# 6. Validation
# ---------------------------------------------------------------------------
# Ranges chosen to catch the failure modes that actually happen with this kind
# of extraction -- a unit slip (metres of rain instead of millimetres), a
# forgotten Kelvin conversion, an empty date filter silently summing to zero --
# not to police the climate. Western Kenya at 1,500 m does not see 55 C or
# 2 m of rain in a month, and a Kelvin value would land at ~295 not ~22.
#
# Every entry here is a (low, high) pair, and gdd10 is one of them rather than a
# separate scalar special case. Mixing a bare float into this dict is what broke
# the first version: the unpack happens in the `for` statement, so a guard in
# the loop body never gets the chance to skip it.
VALIDATION_RANGES = {
    "rain_mm": (0.0, 2000.0),
    "rain_days": (0.0, 31.0),
    "tmean_c": (0.0, 45.0),
    "tmax_c": (0.0, 55.0),
    "tmin_c": (-10.0, 40.0),
    # Upper bound is structural, not climatic: 31 days x the 20 C/day ceiling
    # the [10, 30] clamp allows. Exceeding it means the cap is not being applied.
    "gdd10": (0.0, 620.0),
}


def validate_panel(panel, points):
    problems = []

    expected = len(points) * len(FETCH_YEARS) * len(FETCH_MONTHS)
    if len(panel) != expected:
        problems.append(f"expected {expected} cell-months, got {len(panel)}")

    for band, (lo, hi) in VALIDATION_RANGES.items():
        if band not in panel.columns:
            problems.append(f"{band} is missing from the panel entirely")
            continue
        vals = panel[band].dropna()
        if len(vals) and (vals.min() < lo or vals.max() > hi):
            problems.append(
                f"{band} outside [{lo}, {hi}]: observed "
                f"[{vals.min():.2f}, {vals.max():.2f}]"
            )

    # An all-zero rainfall month across every cell means the date filter matched
    # nothing, which sums to zero rather than erroring. Western Kenya has no
    # month that is dry everywhere, so this is unambiguous.
    dead = panel.groupby(["year", "month"])["rain_mm"].max()
    if (dead == 0).any():
        problems.append(
            "rainfall is identically zero for "
            f"{[f'{y}-{m:02d}' for (y, m) in dead.index[dead == 0]]} -- "
            "the date filter probably matched no images"
        )

    nulls = {c: int(panel[c].isna().sum()) for c in RAIN_BANDS + TEMP_BANDS}
    stats["null_counts"] = nulls
    stats["validation_problems"] = problems

    if problems:
        log("VALIDATION PROBLEMS:")
        for p in problems:
            log(f"  - {p}")
    else:
        log("Validation passed: cell-month count, physical ranges, and "
            "non-degenerate rainfall all as expected.")
    return problems


# ---------------------------------------------------------------------------
# 7. Entry point
# ---------------------------------------------------------------------------
def load_points():
    if not POINTS_PATH.exists():
        sys.exit(
            f"{POINTS_PATH} not found. Run stage 1 first:\n"
            "    python scripts/weather_locations.py"
        )
    pts = pd.read_csv(POINTS_PATH)
    return list(zip(pts["location_key"], pts["cell_lat"], pts["cell_lon"]))


def describe_plan(points, years, months, chunk_size):
    chunks = -(-len(points) // chunk_size)
    panel_reqs = len(years) * len(months) * 2 * chunks
    clim_reqs = len(months) * 2 * chunks
    log(f"Plan: {len(points)} cells x {len(years)} years x {len(months)} months.")
    log(f"      rainfall  {CHIRPS_ID} @ {CHIRPS_SCALE_M} m")
    log(f"      temperature {ERA5_ID} @ {ERA5_SCALE_M} m")
    log(f"      climatology {CLIMATOLOGY_YEARS[0]}-{CLIMATOLOGY_YEARS[1]}")
    log(f"      ~{panel_reqs + clim_reqs} Earth Engine requests "
        f"({chunks} chunk(s) of <= {chunk_size} points each)")
    log(f"      panel rows: {len(points) * len(years) * len(months)}")


def run(project=None, chunk_size=DEFAULT_CHUNK_SIZE, dry_run=False,
        skip_climatology=False):
    points = load_points()
    describe_plan(points, FETCH_YEARS, FETCH_MONTHS, chunk_size)

    if dry_run:
        log("Dry run: plan validated, no Earth Engine call made.")
        return None, None

    ee = init_earth_engine(project or os.environ.get("EE_PROJECT"))
    t0 = time.time()

    log("Fetching monthly panel...")
    panel = fetch_panel(ee, points, FETCH_YEARS, FETCH_MONTHS, chunk_size)
    panel = panel.sort_values(["location_key", "year", "month"]).reset_index(drop=True)

    clim = None
    if not skip_climatology:
        log(f"Fetching {CLIMATOLOGY_YEARS[0]}-{CLIMATOLOGY_YEARS[1]} climatology...")
        clim = fetch_climatology(ee, points, FETCH_MONTHS, chunk_size)
        clim = clim.sort_values(["location_key", "month"]).reset_index(drop=True)

    validate_panel(panel, points)

    WEATHER_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL_PATH, index=False)
    if clim is not None:
        clim.to_csv(CLIMATOLOGY_PATH, index=False)

    stats.update({
        "chirps_collection": CHIRPS_ID,
        "era5_collection": ERA5_ID,
        "years": list(FETCH_YEARS),
        "months": list(FETCH_MONTHS),
        "climatology_years": list(CLIMATOLOGY_YEARS),
        "gdd_base_c": GDD_BASE_C,
        "gdd_cap_c": GDD_CAP_C,
        "rain_day_threshold_mm": RAIN_DAY_THRESHOLD_MM,
        "cells": len(points),
        "panel_rows": int(len(panel)),
        "climatology_rows": int(len(clim)) if clim is not None else 0,
        "elapsed_seconds": round(time.time() - t0, 1),
    })
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, default=str)

    log(f"Saved monthly panel: {PANEL_PATH} ({len(panel)} rows)")
    if clim is not None:
        log(f"Saved climatology:   {CLIMATOLOGY_PATH} ({len(clim)} rows)")
    log(f"Saved fetch stats:   {STATS_PATH}")
    log(f"Done in {stats['elapsed_seconds']:.0f}s "
        f"({stats['requests']} requests, {stats['retries']} retries). "
        f"Next: python scripts/build_weather_features.py")
    return panel, clim


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--project", default=os.environ.get("EE_PROJECT"),
                    help="Earth Engine / Google Cloud project ID "
                         "(or set EE_PROJECT)")
    ap.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE,
                    help=f"points per request (default {DEFAULT_CHUNK_SIZE}; "
                         "lower it if requests time out)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the request plan and exit without calling GEE")
    ap.add_argument("--skip-climatology", action="store_true",
                    help="fetch the panel only; anomaly features are skipped")
    args = ap.parse_args()
    run(args.project, args.chunk_size, args.dry_run, args.skip_climatology)


if __name__ == "__main__":
    main()
