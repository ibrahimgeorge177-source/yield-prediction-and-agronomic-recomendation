"""Train and persist the district-level yield model.

    python scripts/train_district_model.py                       # 2016-2019 -> data/models/district_v1
    python scripts/train_district_model.py --train 2016-2020 --out data/models/district_v2
    python scripts/train_district_model.py --no-nn --top-configs 3   # smaller, faster artefact

Selection protocol: the model is fitted on `--train`; interval widths and the reported
validation metrics come from rolling-origin passes over `--val` (each fitted on the
seasons before it). A season passed to `--test` is scored once at the end and never
influences anything.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from district_model import (DEFAULT_RECIPE, DistrictPipeline, PlotRecipe, aggregate_districts,
                            district_metrics, load_frame, load_schema, project_root)


def year_range(text: str) -> list[int]:
    if "-" in text:
        lo, hi = text.split("-")
        return list(range(int(lo), int(hi) + 1))
    return [int(t) for t in text.split(",")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", default="2016-2019", help="training seasons (default 2016-2019)")
    ap.add_argument("--val", default="2018,2019", help="validation seasons for interval fitting")
    ap.add_argument("--test", default="2020", help="season scored once at the end ('none' to skip)")
    ap.add_argument("--out", default="data/models/district_v1", help="artefact directory")
    ap.add_argument("--recipe", default=DEFAULT_RECIPE, help="notebook 06 configuration JSON")
    ap.add_argument("--top-configs", type=int, default=5,
                    help="configurations averaged per family (fewer = smaller artefact)")
    ap.add_argument("--nn-seeds", type=int, default=3, help="networks in the neural bag")
    ap.add_argument("--no-nn", action="store_true", help="tree families only (no torch needed)")
    ap.add_argument("--min-plots", type=int, default=20,
                    help="districts with fewer plots in a season are not reported")
    args = ap.parse_args()

    root = project_root()
    out_dir = (root / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    train_years, val_years = year_range(args.train), year_range(args.val)
    test_years = [] if args.test.lower() == "none" else year_range(args.test)

    t0 = time.time()
    schema = load_schema(root)
    frame = load_frame(root, schema)
    recipe = PlotRecipe.from_json(root / args.recipe, top_configs=args.top_configs)
    recipe.nn_seeds = args.nn_seeds

    print(f"district yield model — training on {train_years[0]}-{train_years[-1]}")
    print(f"  {len(schema.feats)} features, {len(schema.cats)} categorical, "
          f"{len(schema.weather)} weather columns")
    print(f"  families: {', '.join(recipe.families)}"
          f"{'' if args.no_nn else ' + NeuralNet'} | {args.top_configs} configs each")

    pipe = DistrictPipeline(schema, recipe, use_nn=not args.no_nn, min_plots=args.min_plots)
    pipe.fit(frame, train_years, val_years)

    if pipe.metadata_.get("validation"):
        print("\nvalidation seasons (fitted on prior seasons only)")
        for y, m in sorted(pipe.metadata_["validation"].items()):
            print(f"  {y}: districts={m['n_districts']:2d} R2={m['r2']:+.3f} "
                  f"MAE={m['mae']:4.0f} corr={m['corr']:+.3f}")
    print(f"\ninterval model: sd(n) = sqrt({pipe.intervals_.a:,.0f} + {pipe.intervals_.b:,.0f}/n)"
          f"  → sd at n=50 is {pipe.intervals_.sd(np.array([50]))[0]:,.0f} kg/ha")

    if test_years:
        test = frame[frame.year.isin(test_years)]
        table = pipe.predict_districts(test)
        m = district_metrics(table.actual_mean.to_numpy(), table.pred_mean.to_numpy(),
                             table.n_plots.to_numpy())
        cover = float(table.inside_band.mean())
        print(f"\ntest season {test_years} — scored once")
        print(f"  districts={m['n_districts']}  R2={m['r2']:+.3f}  MAE={m['mae']:.0f} kg/ha  "
              f"corr={m['corr']:+.3f}  bias={m['bias']:+.0f}")
        print(f"  {int(pipe.intervals_.coverage_target * 100)}% interval covers "
              f"{cover:.0%} of districts")
        pipe.metadata_["test"] = {str(test_years): dict(m, coverage=cover)}
        for cut in (30, 50, 100):
            g = table[table.n_plots >= cut]
            if len(g) >= 5:
                mm = district_metrics(g.actual_mean.to_numpy(), g.pred_mean.to_numpy())
                print(f"    districts with >={cut:3d} plots (n={mm['n_districts']:2d}): "
                      f"R2={mm['r2']:+.3f}  MAE={mm['mae']:.0f}")
                pipe.metadata_.setdefault("test_by_size", {})[f">={cut}"] = mm

    path = pipe.save(out_dir)
    size = sum(f.stat().st_size for f in Path(out_dir).glob("*")) / 1e6
    print(f"\nsaved {path.relative_to(root)}  ({size:.1f} MB)  [{time.time() - t0:.0f}s]")
    print(f"score a new season with:\n"
          f"  python scripts/predict_district.py --model {args.out} --year <YEAR>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
