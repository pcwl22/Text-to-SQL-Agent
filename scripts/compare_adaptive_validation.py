"""Read-only, offline comparison of two independently scored P1B groups."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.adaptive_validation import isolated_runtime
from bird_starter.adaptive_validation_evaluation import compare_official


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--b-summary", required=True)
    parser.add_argument("--e-summary", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        with isolated_runtime(True):
            result = compare_official(args.run_dir, args.config, args.b_summary, args.e_summary, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Comparison rejected: check real-run identity, audit, fixed cohort and official provenance")
    print(f"Independent official EX: B={result['B']}/10; E={result['E']}/10; no model requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
