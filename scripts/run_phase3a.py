"""Command-line entry point for the frozen synthetic-only Phase 3A pipeline."""

from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))

from quantforge.phase3a_runner import run_phase3a


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only",action="store_true",required=True)
    parser.add_argument("--config",default="configs/history-extension.toml")
    parser.add_argument("--output-directory")
    args=parser.parse_args()
    result=run_phase3a(synthetic_only=args.synthetic_only,config_path=args.config,output_directory=args.output_directory)
    print(json.dumps({k:result[k] for k in ("status","output_directory","ridge_fits","folds","evidence_level")},indent=2))


if __name__=="__main__":
    main()
