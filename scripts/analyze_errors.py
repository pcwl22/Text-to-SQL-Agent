"""Analyze saved experiment errors offline; no model, SQL execution or Gold input."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.error_analysis import build_error_report
from bird_starter.experiment_review import PROJECT_ROOT


def mappings(values):
    result = {}
    for value in values:
        group, path = value.split("=", 1)
        if group not in {"A", "B", "C", "D"} or group in result or not path:
            raise ValueError("Invalid or duplicate group")
        result[group] = path
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, metavar="A=RUN_DIRECTORY")
    parser.add_argument("--official", action="append", default=[], metavar="A=SUMMARY_JSON")
    parser.add_argument("--suite-config", default=str(PROJECT_ROOT / "configs/phase3c/ablation_v1.json"))
    parser.add_argument("--annotations", help="Optional source-hash-bound manual semantic attributions")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        report = build_error_report(mappings(args.run), args.suite_config, args.out_dir,
            official_summaries=mappings(args.official), annotations=args.annotations)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Analysis rejected: incompatible input, score provenance, schema evidence, annotation or output directory")
    print(f"Analyzed {len(report['cases'])} saved records; model requests 0; SQL executions 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
