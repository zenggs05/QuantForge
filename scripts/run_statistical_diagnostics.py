"""Run frozen Phase 2C diagnostics offline; no market download or model fitting."""
import argparse
import json
import sys
from quantforge.diagnostics.experiment import run_diagnostics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='configs/statistical-diagnostics.toml')
    args=parser.parse_args()
    try:
        summary=run_diagnostics(args.config)
    except Exception as exc:
        print(f'Diagnostics FAILED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 1
    print(json.dumps({k:summary[k] for k in ('status','run_id','output_directory','counts','timings_seconds','elapsed_seconds')},indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
