"""Execute only the D-043 retry, offline finalization, or offline replay."""
from __future__ import annotations

import argparse
import sys

from quantforge import archive_verification_retry as retry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("retry", "finalize", "replay-check"))
    args = parser.parse_args(argv)
    try:
        if args.mode == "retry":
            result = retry.run_retry()
            retry.emit({"status": "retry_complete", **result["counters"]})
        elif args.mode == "finalize":
            retry.finalize()
        else:
            retry.replay_check()
    except retry.StopTask as exc:
        try:
            name = retry.write_stop(exc, args.mode)
        except BaseException as write_error:
            retry.emit({"status": "stopped_without_fallback", "error_type": type(write_error).__name__,
                        "error": str(write_error)})
            return 1
        retry.emit({"status": "stopped", "error": str(exc), "stop_file": name})
        return 1
    except BaseException as exc:
        # No evidence write after a failed write, permission refusal or unknown failure.
        retry.emit({"status": "stopped_without_fallback", "error_type": type(exc).__name__, "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
