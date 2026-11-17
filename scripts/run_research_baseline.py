"""Run the fixed, offline Phase 2B monthly prediction baseline; never trade."""

from __future__ import annotations

import argparse
import json
import sys

from quantforge.research_experiment import load_research_config, run_research_baseline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/research-baseline.toml",
                        help="Repository-local fixed research configuration")
    args = parser.parse_args()
    try:
        config = load_research_config(args.config)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        parser.error(f"Invalid research configuration: {exc}")
    try:
        summary = run_research_baseline(config, config_path=args.config)
    except Exception as exc:
        print(f"Research baseline FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": summary["status"], "output_directory": summary["output_directory"],
                      "limitation": summary["limitation"], "metrics": summary["metrics"]},
                     indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
