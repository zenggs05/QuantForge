"""Download and validate exactly one public BTCUSDT 1m UTC day."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import date, datetime, timezone

from quantforge.data import BASE_URL, archive_name, download_day, ensure_daily_allowed, repository_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, help="One UTC day, e.g. 2026-09-01")
    parser.add_argument("--config", default="configs/data-sample.toml", help="Repository-local TOML config")
    args = parser.parse_args()
    try:
        if args.date is not None:
            ensure_daily_allowed(args.date)
        config_path = repository_path(args.config)
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))["sample"]
        if config["symbol"] != "BTCUSDT" or config["interval"] != "1m" or config["market"] != "spot":
            parser.error("Phase one supports only a single BTCUSDT Spot 1m UTC day")
        day = args.date or date.fromisoformat(config["date"])
        ensure_daily_allowed(day)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(f"Invalid repository-local sample configuration: {exc}")
    try:
        manifest = download_day(day)
    except Exception as exc:
        failure = {
            "status": "failed", "date_utc": day.isoformat(),
            "attempted_at_utc": datetime.now(timezone.utc).isoformat(),
            "archive_url": f"{BASE_URL}/{archive_name(day)}",
            "error_type": type(exc).__name__, "error": str(exc),
            "end_to_end_validation_completed": False,
            "download_status": "Not inferred from this failure; inspect preserved source/checksum metadata.",
        }
        target = repository_path(f"results/data-validation/BTCUSDT-1m-{day.isoformat()}-failure.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
