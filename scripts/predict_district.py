"""Score a season with a trained district model.

    python scripts/predict_district.py --year 2020
    python scripts/predict_district.py --input my_season.csv --out predictions.csv
    python scripts/predict_district.py --year 2020 --min-plots 50 --top 15

`--input` takes any CSV in the schema of data/features/kenya_maize_features_full.csv
(the engineered feature columns plus `district`; `yield_kg_ph` is optional and, when
present, is used to score the predictions). Output is one row per district with the
predicted mean yield, an 80% interval and the number of plots behind it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

from district_model import (DistrictPipeline, TARGET, district_metrics, load_frame, load_schema,
                            project_root, restore_dtypes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="data/models/district_v1", help="artefact directory")
    ap.add_argument("--year", type=int, help="score this season from the project feature file")
    ap.add_argument("--input", help="score this CSV instead (same schema as the feature file)")
    ap.add_argument("--out", help="write the district table here (CSV)")
    ap.add_argument("--min-plots", type=int, help="override the reporting threshold")
    ap.add_argument("--top", type=int, default=0, help="print only the top N districts")
    args = ap.parse_args()

    root = project_root()
    model_dir = (root / args.model) if not Path(args.model).is_absolute() else Path(args.model)
    pipe = DistrictPipeline.load(model_dir)
    schema = load_schema(root)

    if args.input:
        frame = restore_dtypes(pd.read_csv(args.input, low_memory=False))
        for c in schema.cats:
            frame[c] = frame[c].astype("category")
        label = args.input
    else:
        if args.year is None:
            ap.error("pass --year or --input")
        frame = load_frame(root, schema)
        frame = frame[frame.year == args.year]
        label = f"season {args.year}"
    if frame.empty:
        print(f"no rows for {label}")
        return 1

    missing = [c for c in schema.feats if c not in frame.columns]
    if missing:
        print(f"input is missing {len(missing)} required feature columns, "
              f"first few: {missing[:5]}")
        return 2

    table = pipe.predict_districts(frame, min_plots=args.min_plots)
    trained = pipe.metadata_.get("train_years", [])
    print(f"district predictions for {label} — model trained on "
          f"{min(trained)}-{max(trained)} ({len(frame):,} plots, {len(table)} districts)\n")

    show = table.head(args.top) if args.top else table
    cols = ["district", "n_plots", "pred_mean", "pred_lo", "pred_hi"]
    if "actual_mean" in table:
        cols += ["actual_mean", "error"]
    fmt = show[cols].copy()
    for c in fmt.columns:
        if fmt[c].dtype.kind == "f":
            fmt[c] = fmt[c].round(0).astype(int)
    print(fmt.to_string(index=False))

    if "actual_mean" in table:
        m = district_metrics(table.actual_mean.to_numpy(), table.pred_mean.to_numpy(),
                             table.n_plots.to_numpy())
        scored_years = sorted(set(frame.year.dropna().astype(int))) if "year" in frame else []
        in_sample = sorted(set(scored_years) & set(trained))
        if in_sample:
            print(f"\n  ** {in_sample} is inside this model's training seasons. The scores "
                  f"below are in-sample and overstate accuracy — they are not a measure of "
                  f"how the model will do on a new season. Use data/models/district_v1 "
                  f"(trained on 2016-2019) to score 2020 honestly. **")
        print(f"\nscored against observed means: R2={m['r2']:+.3f}  MAE={m['mae']:.0f} kg/ha  "
              f"corr={m['corr']:+.3f}  bias={m['bias']:+.0f}")
        print(f"  {int(pipe.intervals_.coverage_target * 100)}% interval covers "
              f"{table.inside_band.mean():.0%} of districts")
    else:
        print(f"\nno {TARGET} column in the input, so predictions are unscored.")

    if args.out:
        out_path = Path(args.out)
        table.to_csv(out_path, index=False)
        print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
