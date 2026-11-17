"""Execute only the frozen Phase 2E post-hoc reanalysis and training audit."""
import argparse
import json
import sys
from quantforge.power_reanalysis import run_power_reanalysis


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",default="configs/power-reanalysis.toml")
    parser.add_argument("--reproduction",action="store_true")
    args=parser.parse_args()
    try:
        result=run_power_reanalysis(args.config,run_kind="reproduction" if args.reproduction else "formal")
    except Exception as exc:
        print(f"Power reanalysis FAILED: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 1
    print(json.dumps({k:result[k] for k in ["status","run_id","run_kind","output_directory","placebo_pipeline_warning","elapsed_seconds"]},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
