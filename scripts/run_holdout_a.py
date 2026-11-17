"""Run exactly one D-059 stage with the child-process authorization gate."""
import argparse
import json

from quantforge.holdout_a import run


def main(argv=None):
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("mode", choices=("download", "audit", "formal", "reproduction"))
    args = parser.parse_args(argv)
    print(json.dumps(run(args.mode), ensure_ascii=True, allow_nan=False))


if __name__ == "__main__":
    main()
