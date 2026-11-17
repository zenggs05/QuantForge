"""Validate the authorized first-month/day comparison or full development set."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json

import pandas as pd

from quantforge.data import download_day, repository_path, sha256
from quantforge.data_monthly import verify_cached_month
from quantforge.experiment import _write_json


def cross_check_first_day() -> dict:
    """Compare the same real UTC day from two independently verified archives.

    Only September 1, 2025 is authorized here. This operation may fetch that
    single daily archive and checksum; it never expands the monthly whitelist.
    """
    month_frame, month_manifest = verify_cached_month("2025-09")
    daily_manifest = download_day(date(2025, 9, 1))
    daily = pd.read_parquet(repository_path(daily_manifest["parquet_path"]))
    boundary = pd.Timestamp("2025-09-02T00:00:00Z")
    subset = month_frame.loc[month_frame.open_time < boundary].reset_index(drop=True)
    pd.testing.assert_frame_equal(subset, daily, check_exact=True)
    result = {"status": "passed", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "month": "2025-09", "day": "2025-09-01", "rows_compared": len(daily),
              "all_columns_exactly_equal": True, "columns": list(daily.columns),
              "month_archive_sha256": month_manifest["archive_sha256"],
              "daily_manifest": daily_manifest,
              "script_sha256": sha256(repository_path("scripts/validate_monthly_data.py").read_bytes()),
              "limitation": "Agreement of these two official archives does not prove market truth or future revision stability."}
    _write_json(repository_path("results/monthly-download/daily-cross-check.json"), result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--cross-check-first-day", action="store_true")
    actions.add_argument("--build-development-manifest", action="store_true")
    args = parser.parse_args()
    try:
        if args.cross_check_first_day:
            result = cross_check_first_day()
            print(json.dumps({"status": result["status"], "rows_compared": result["rows_compared"],
                              "all_columns_exactly_equal": result["all_columns_exactly_equal"]}, indent=2))
        else:
            from quantforge.datasets.research_dataset import build_development_manifest
            result = build_development_manifest()
            print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        target = repository_path("results/monthly-download")
        target.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        _write_json(repository_path(target / f"validation-failure-{stamp}.json"), {
            "status": "failed", "action": "daily_cross_check" if args.cross_check_first_day else "development_manifest",
            "error_type": type(exc).__name__, "error": str(exc),
        })
        print(f"Validation FAILED: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
