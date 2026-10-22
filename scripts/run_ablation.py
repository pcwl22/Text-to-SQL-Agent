"""Four-group serial ablation. Defaults to zero-request dry-run, never resume."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.ablation import run_suite, PROJECT_ROOT
from bird_starter.data import dataset_paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "configs/phase3c/ablation_v1.json"))
    parser.add_argument("--cases", default=None)
    parser.add_argument("--db-root", default=None)
    parser.add_argument("--out-dir", required=True, help="Fresh directory only; never reuse after a crash")
    parser.add_argument("--groups", nargs="+", choices=("A", "B", "C", "D"), default=["A", "B", "C", "D"])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--mock-llm", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--allow-api", action="store_true", help="Fresh explicit authorization, only with --live")
    parser.add_argument("--mock-scenario", choices=("read_only_probe", "fault_injection_v1"), default="read_only_probe")
    parser.add_argument("--group-budget", action="append", default=[], metavar="A=33", help="Reduce a selected group's theoretical cap")
    parser.add_argument("--batch-budget", type=int, default=None)
    args = parser.parse_args(argv)
    if args.allow_api and not args.live:
        parser.error("--allow-api is only valid for an explicitly authorized --live experiment")
    if args.live and not args.allow_api:
        parser.error("Live use requires fresh --allow-api authorization")
    caps = {}
    try:
        for value in args.group_budget:
            group, number = value.split("=", 1)
            if group in caps:
                raise ValueError()
            caps[group] = int(number)
    except ValueError:
        parser.error("Each group-budget must uniquely specify GROUP=INTEGER")
    cases, root = dataset_paths(args.cases, args.db_root)
    try:
        summary = run_suite(args.config, cases, root, args.out_dir, groups=args.groups,
                            execution="live" if args.live else "mock" if args.mock_llm else "dry-run",
                            allow_api=args.allow_api, group_budgets=caps, batch_budget=args.batch_budget,
                            mock_scenario=args.mock_scenario)
    except (OSError, ValueError, KeyError, TypeError):
        # Fixed diagnostic: configuration/parser exceptions can contain arbitrary data.
        parser.error("Experiment rejected: check pinned configuration/data, budgets and fresh output path; files are never overwritten")
    print(f"Saved {summary['execution']} ablation: {summary['run_id']}; model attempts={summary['model_calls']}; "
          "Mock is not model accuracy; proxy is not official EX.")
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
