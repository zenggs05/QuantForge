"""Run only the frozen March 1 delayed-feedback engineering pilot."""
import argparse
import json
import sys
from quantforge.online.foundation import run_online_foundation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",default="configs/online-foundation.toml")
    parser.add_argument("--reproduction",action="store_true")
    args=parser.parse_args()
    try:
        summary=run_online_foundation(args.config,run_kind="reproduction" if args.reproduction else "formal")
    except Exception as exc:
        print(f"Online foundation FAILED: {type(exc).__name__}: {exc}",file=sys.stderr)
        return 1
    print(json.dumps({k:summary[k] for k in ("status","run_id","run_kind","output_directory","prediction_count",
        "update_count","batch_checkpoint_count","restored_continuation_exact","elapsed_seconds")},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
