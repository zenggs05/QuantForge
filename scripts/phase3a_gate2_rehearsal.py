"""The one full-calendar engineering rehearsal; no real market-row access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from quantforge.phase3a_gate2_rehearsal import run_rehearsal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.parse_args(argv)
    print(json.dumps(run_rehearsal(), ensure_ascii=True))


if __name__ == "__main__":
    main()
