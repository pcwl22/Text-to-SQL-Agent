"""Prepare evaluator-only official-format files; does not execute official EX."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.ablation import PROJECT_ROOT
from bird_starter.official_ex_inputs import export_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--group", required=True, choices=list("ABCDE"))
    parser.add_argument("--suite-config", default=str(PROJECT_ROOT / "configs/phase3c/ablation_v1.json"))
    parser.add_argument("--cases", default=str(PROJECT_ROOT / "data/mini_dev_data/mini_dev_sqlite.json"))
    parser.add_argument("--out-dir", required=True, help="Fresh directory inside ignored project outputs/")
    args = parser.parse_args(argv)
    try:
        result = export_inputs(args.run_dir, args.group, args.suite_config, args.cases, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Export rejected: check identity, audit, dataset, SQL representation and private fresh output path")
    print(f"Aligned {result['planned_cases']} cases; official EX not executed; engine remains blocked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
