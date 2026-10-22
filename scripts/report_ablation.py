"""Offline paired report across independent A/B/C/D runs; official EX stays pending."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.ablation import PROJECT_ROOT
from bird_starter.ablation_review_report import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, metavar="A=RUN_DIRECTORY")
    parser.add_argument("--suite-config", default=str(PROJECT_ROOT / "configs/phase3c/ablation_v1.json"))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        runs = {}
        for value in args.run:
            group, path = value.split("=", 1)
            if group in runs:
                raise ValueError("Duplicate group")
            runs[group] = path
        report = build_report(runs, args.suite_config, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Report rejected: incompatible cohort, run identity, audit, measurement kind or output directory")
    print(f"Reported {len(report['groups'])} groups; official EX remains pending, not replaced by Proxy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
