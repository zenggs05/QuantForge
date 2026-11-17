"""Entry point for the D-063 Holdout-B confirmatory run: download | audit | formal | reproduction."""
import json
import sys

from quantforge.holdout_b import run


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        raise SystemExit("usage: run_holdout_b.py download|audit|formal|reproduction")
    print(json.dumps(run(argv[0]), ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
