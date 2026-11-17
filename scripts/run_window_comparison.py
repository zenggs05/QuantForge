"""Run the frozen Phase 2D-A offline window comparison from a clean commit."""
from __future__ import annotations

import argparse
import json
import sys

from quantforge.window_experiment import run_window_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/rolling-windows.toml")
    parser.add_argument("--reproduction", action="store_true", help="Label the single unchanged reproduction run")
    args = parser.parse_args()
    try:
        summary = run_window_comparison(args.config, run_kind="reproduction" if args.reproduction else "formal")
    except Exception as exc:
        print(f"Window comparison FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({key: summary[key] for key in ("status", "run_id", "run_kind", "output_directory",
                     "prediction_rows", "paired_observations", "budget", "march_6m_equivalence", "limitation")},
                     indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
