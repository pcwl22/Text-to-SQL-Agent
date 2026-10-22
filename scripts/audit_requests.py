"""Offline, read-only experiment inspection. Never resumes or contacts a provider."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.request_audit import inspect_experiment, write_new_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", help="Optional fresh report path; never overwritten")
    args = parser.parse_args(argv)
    try:
        report = inspect_experiment(args.config)
        if args.out:
            write_new_json(args.out, report)
    except (OSError, ValueError) as exc:
        parser.error("Cannot inspect experiment or exclusively create report; preserve existing files")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
