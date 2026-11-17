"""Download only the frozen BTCUSDT development months, with persistent quotas."""

import argparse
import json
import sys

from quantforge.data_monthly import ALLOWED_MONTHS, download_development, download_month


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--month", choices=ALLOWED_MONTHS, help="One month; defaults to the mandatory 2025-09 gate")
    group.add_argument("--development", action="store_true", help="Exact 10-month list; requires verified 2025-09 cache")
    args = parser.parse_args()
    try:
        manifests = download_development() if args.development else [download_month(args.month or "2025-09")]
    except Exception as exc:
        print(f"Monthly download FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps([{"month": item["month"], "status": item["status"],
                       "rows": item["validation"]["rows"], "archive_sha256": item["archive_sha256"],
                       "network_phase_totals": item["network_phase_totals"]} for item in manifests], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
