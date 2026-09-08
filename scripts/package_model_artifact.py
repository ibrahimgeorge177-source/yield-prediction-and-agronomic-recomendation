"""Package a fitted artefact into a single .tar.gz for release or object storage.

The artefact cannot live in git — it is 90-125 MB, above GitHub's per-file limit
for a repository, though well under the 2 GB limit for a *release asset*. So the
deployment path is: train, package, attach to a release, point MODEL_URL at it.

    python scripts/train_district_model.py --train 2016-2020 --val 2019,2020 \
        --test none --out data/models/district_v2
    python scripts/package_model_artifact.py --model-dir data/models/district_v2
    gh release create model-v2 dist/district_v2.tar.gz --title "District model v2"

Then set MODEL_URL to the asset's browser_download_url — as a build argument for
a self-contained image, or as a service variable to fetch it at run time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", default="data/models/district_v2",
                    help="artefact directory holding pipeline.joblib")
    ap.add_argument("--out-dir", default="dist", help="where to write the archive")
    ap.add_argument("--name", help="archive name (defaults to the artefact directory name)")
    args = ap.parse_args()

    model_dir = Path(args.model_dir)
    artefact = model_dir / "pipeline.joblib"
    if not artefact.exists():
        print(f"no pipeline.joblib in {model_dir} — train one first with "
              f"scripts/train_district_model.py", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = args.name or model_dir.name
    archive = out_dir / f"{name}.tar.gz"

    members = [artefact] + [p for p in (model_dir / "metadata.json",) if p.exists()]
    with tarfile.open(archive, "w:gz") as tar:
        for member in members:
            tar.add(member, arcname=f"{name}/{member.name}")

    digest = sha256(archive)
    manifest = dict(name=name,
                    archive=archive.name,
                    bytes=archive.stat().st_size,
                    sha256=digest,
                    contents=[m.name for m in members])
    meta_path = model_dir / "metadata.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        manifest["train_years"] = meta.get("train_years")
        manifest["members"] = meta.get("members")
    (out_dir / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2),
                                                   encoding="utf-8")

    print(f"{archive}  ({archive.stat().st_size / 1e6:.1f} MB)")
    print(f"  sha256 {digest}")
    print(f"  contains {', '.join(m.name for m in members)}")
    print(f"\nattach it to a release, then set MODEL_URL to the download URL:\n"
          f"  gh release create model-{name} {archive} --title 'District model {name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
