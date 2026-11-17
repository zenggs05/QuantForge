"""Run the offline one-day causal prediction smoke test (no orders or PnL)."""

from __future__ import annotations

import argparse
import json
import sys

from quantforge.experiment import load_config, run_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/baseline.toml", help="Repository-local TOML configuration")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.error(f"Invalid baseline configuration: {exc}")
    try:
        summary = run_baseline(config, config_path=args.config)
    except Exception as exc:
        print(f"Baseline smoke test FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": summary["status"], "output_directory": summary["output_directory"],
                      "limitation": summary["limitation"], "metrics": summary["metrics"]}, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
