"""Fetch a fitted district-model artefact into a directory.

Used by the Dockerfile to bake the artefact into an image at build time, and
available on the command line to prime a volume or a local checkout:

    python scripts/fetch_model_artifact.py --url https://.../district_v2.tar.gz \
        --dest data/models/district_v2

Accepts a bare `pipeline.joblib`, or a `.zip` / `.tar.gz` containing one — the
same shapes the service accepts at run time through MODEL_URL, because both go
through the one implementation in api/registry.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.registry import _download_artefact  # noqa: E402  (path set above)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="artefact URL (.joblib, .zip or .tar.gz)")
    ap.add_argument("--dest", required=True, help="directory to write pipeline.joblib into")
    args = ap.parse_args()

    dest = Path(args.dest)
    _download_artefact(args.url, dest)

    artefact = dest / "pipeline.joblib"
    if not artefact.exists():
        print(f"no pipeline.joblib landed in {dest}", file=sys.stderr)
        return 1
    print(f"{artefact} ({artefact.stat().st_size / 1e6:.1f} MB)")
    meta = dest / "metadata.json"
    print(f"{meta} ({meta.stat().st_size} bytes)" if meta.exists()
          else "no metadata.json in the archive; /api/v1/model/summary will report less "
               "until the model is loaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
