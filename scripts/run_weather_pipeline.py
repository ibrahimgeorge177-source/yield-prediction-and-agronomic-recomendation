"""
Kenya Maize Weather Feature Pipeline -- Runner
==============================================

Runs the whole environmental-feature chain end to end, so the normal case is
one command:

    python scripts/run_weather_pipeline.py

    1. weather_locations.py       resolve each row to a CHIRPS grid cell   offline
    2. fetch_gee_weather.py       pull CHIRPS + ERA5-Land from GEE         NETWORK
    3. build_weather_features.py  build the 89 row-level features          offline
    4. build_features.py          merge into the modelling feature matrix  offline

The Earth Engine project ID comes from `.env` at the repository root
(EE_PROJECT=ontrack-356205), so no flag is needed. Override it for one run with
--project, or permanently in .env.local.

Useful flags:
    --check         verify credentials and project registration, then stop.
                    Run this FIRST -- it takes two seconds and catches the two
                    things that otherwise fail after stage 2 has already
                    started.
    --refetch       discard the Earth Engine resume cache and re-pull stage 2
    --skip-features stop after stage 3, leaving the main feature matrix alone
    --project ID    override the .env project for this run

WHY STAGE 2 IS SKIPPED WHEN ITS OUTPUT EXISTS
---------------------------------------------
It is the only stage that costs anything -- a few hundred Earth Engine requests
against a monthly compute quota -- and its output is deterministic. Re-running
this script after editing a feature definition in stage 3 should not re-pull
rainfall that has not changed since 1981. Pass --refetch when you actually want
new data.

DEPENDENCIES: standard library only. The stages it calls have their own.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
WEATHER_DIR = ROOT / "data" / "weather"
PANEL_PATH = WEATHER_DIR / "monthly_weather_panel.csv"
CLIMATOLOGY_PATH = WEATHER_DIR / "monthly_weather_climatology.csv"
CACHE_DIR = WEATHER_DIR / "_gee_cache"


def log(msg=""):
    print(f"[run_weather_pipeline] {msg}" if msg else "", flush=True)


def rule(title):
    log()
    log("=" * 68)
    log(title)
    log("=" * 68)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
# Both failures this catches happen *before* any real work, but without an
# explicit check they surface several minutes into stage 2 -- after the user has
# walked away from the terminal. Two seconds here is worth it.
def preflight(project):
    # Import the fetch module rather than re-implementing its .env handling, so
    # the project ID resolved here is provably the one stage 2 will use.
    sys.path.insert(0, str(SCRIPTS))
    try:
        import fetch_gee_weather as fetch
    except ImportError as exc:
        log(f"FAIL  cannot import fetch_gee_weather: {exc}")
        return False

    project = project or os.environ.get("EE_PROJECT")
    if not project:
        log(f"FAIL  no EE_PROJECT found in {ROOT / '.env'} and no --project given.")
        return False
    log(f"OK    project id: {project}")

    try:
        import ee
    except ImportError:
        log("FAIL  earthengine-api is not installed.  ->  pip install earthengine-api")
        return False
    log(f"OK    earthengine-api {getattr(ee, '__version__', 'unknown')}")

    # Reuse the fetch module's own credential resolution rather than a second
    # copy of it, so a preflight pass genuinely predicts a stage 2 pass.
    key_path = os.environ.get("EE_SERVICE_ACCOUNT_KEY", "").strip()
    if key_path:
        resolved = Path(os.path.expanduser(key_path))
        if not resolved.exists():
            log(f"FAIL  EE_SERVICE_ACCOUNT_KEY points at a missing file: {resolved}")
            log("      Fix the path in .env, or blank the line to use "
                "`earthengine authenticate` instead.")
            return False
        log(f"OK    service-account key: {resolved}")
        try:
            credentials = fetch._service_account_credentials(ee, key_path)
        except SystemExit as exc:
            log(f"FAIL  {exc}")
            return False
    else:
        credentials = None
        log("OK    auth mode: interactive user credentials (no service-account key set)")

    try:
        if credentials is not None:
            ee.Initialize(credentials, project=project)
        else:
            ee.Initialize(project=project)
    except Exception as exc:  # noqa: BLE001 -- show the real cause
        log(f"FAIL  Earth Engine would not initialise: {exc}")
        log()
        log("      Most likely one of:")
        if credentials is not None:
            log("        1. The service account lacks Earth Engine access")
            log(f"           ->  https://code.earthengine.google.com/register?project={project}")
            log("        2. The Earth Engine API is not enabled on the project")
            log("           ->  https://console.cloud.google.com/apis/library/"
                "earthengine.googleapis.com")
        else:
            log("        1. Not authenticated yet   ->  earthengine authenticate")
            log("        2. Project not registered for Earth Engine")
            log(f"           ->  https://code.earthengine.google.com/register?project={project}")
        log("        3. Wrong ID (use the project ID, not its display name)")
        return False
    log("OK    authenticated and initialised")

    # A live query, because initialising proves credentials exist while this
    # proves the project can actually read the catalogue -- a project that is
    # authenticated but unregistered fails only at this step.
    try:
        n = (ee.ImageCollection(fetch.CHIRPS_ID)
             .filterDate("2020-04-01", "2020-05-01").size().getInfo())
    except Exception as exc:  # noqa: BLE001
        log(f"FAIL  catalogue query rejected: {exc}")
        log(f"      Register the project: "
            f"https://code.earthengine.google.com/register?project={project}")
        return False
    log(f"OK    catalogue reachable ({n} CHIRPS images in April 2020)")
    return True


# ---------------------------------------------------------------------------
# Stage execution
# ---------------------------------------------------------------------------
def run_stage(script, args=(), label=""):
    rule(label or script)
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    log(f"$ {' '.join(cmd[1:])}")
    log()
    t0 = time.time()
    # Not capturing output: these stages log their own reasoning as they go and
    # stage 2 can run for many minutes. Swallowing that to re-print it at the
    # end would leave the user staring at a silent terminal.
    result = subprocess.run(cmd, cwd=ROOT)
    elapsed = time.time() - t0
    if result.returncode != 0:
        log()
        log(f"FAILED: {script} exited {result.returncode} after {elapsed:.0f}s.")
        sys.exit(result.returncode)
    log()
    log(f"{script} finished in {elapsed:.0f}s.")
    return elapsed


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--project", default=None,
                    help="Earth Engine project ID (default: EE_PROJECT from .env)")
    ap.add_argument("--check", action="store_true",
                    help="verify credentials and registration, then exit")
    ap.add_argument("--refetch", action="store_true",
                    help="discard the resume cache and re-pull from Earth Engine")
    ap.add_argument("--skip-features", action="store_true",
                    help="stop after stage 3; do not rebuild the feature matrix")
    ap.add_argument("--chunk-size", type=int, default=None,
                    help="points per Earth Engine request (lower it on timeouts)")
    args = ap.parse_args()

    rule("Preflight")
    if not preflight(args.project):
        log()
        log("Preflight failed -- nothing was fetched. Fix the above and re-run.")
        sys.exit(1)
    if args.check:
        log()
        log("All checks passed. Run without --check to execute the pipeline.")
        return

    started = time.time()

    run_stage("weather_locations.py",
              label="Stage 1/4  Resolve survey rows to CHIRPS grid cells")

    if args.refetch and CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        log(f"Removed resume cache {CACHE_DIR} (--refetch).")

    have_panel = PANEL_PATH.exists() and CLIMATOLOGY_PATH.exists()
    if have_panel and not args.refetch:
        rule("Stage 2/4  Earth Engine extraction -- SKIPPED")
        log(f"{PANEL_PATH.relative_to(ROOT)} and its climatology already exist.")
        log("Pass --refetch to pull them again from Earth Engine.")
    else:
        fetch_args = []
        if args.project:
            fetch_args += ["--project", args.project]
        if args.chunk_size:
            fetch_args += ["--chunk-size", str(args.chunk_size)]
        run_stage("fetch_gee_weather.py", fetch_args,
                  label="Stage 2/4  Fetch CHIRPS rainfall + ERA5-Land temperature")

    run_stage("build_weather_features.py",
              label="Stage 3/4  Build row-level weather features")

    if args.skip_features:
        rule("Stage 4/4  Feature matrix -- SKIPPED (--skip-features)")
    else:
        run_stage("build_features.py",
                  label="Stage 4/4  Merge into the modelling feature matrix")

    rule(f"Done in {time.time() - started:.0f}s")
    for path in (
        WEATHER_DIR / "kenya_maize_weather_features.csv",
        ROOT / "data" / "features" / "kenya_maize_features_full.csv",
        ROOT / "data" / "features" / "kenya_maize_features_core.csv",
        ROOT / "data" / "features" / "kenya_maize_feature_manifest.csv",
    ):
        if path.exists():
            log(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
