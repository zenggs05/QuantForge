"""The single result-blind Gate 2 real-data process."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from quantforge.phase3a_gate2 import run_real


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--contract", required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run_real("prefit", args.contract), ensure_ascii=True))


if __name__ == "__main__":
    main()
