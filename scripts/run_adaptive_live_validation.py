"""Dedicated P1B preparation/B-E runner; defaults to offline dry-run."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.adaptive_validation import isolated_runtime, run_validation
from bird_starter.adaptive_validation_config import ROOT, prepare


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ROOT / "configs/p1b/adaptive_live_v1.json"))
    parser.add_argument("--cases", default=str(ROOT / "data/mini_dev_data/mini_dev_sqlite.json"))
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--frozen-dir")
    parser.add_argument("--authorization", help="Separate single-use local authorization file; never a key")
    parser.add_argument("--allow-api", action="store_true")
    parser.add_argument("--project-env", action="store_true", help="Explicit Live/catalog use of the existing secure project credential loader; never print values")
    parser.add_argument("--max-attempts", type=int)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--mock-llm", action="store_true")
    modes.add_argument("--live", action="store_true")
    modes.add_argument("--check-connection", action="store_true", help="One authorized, audited model-catalog GET; no generation probe")
    args = parser.parse_args(argv)
    connected_mode = args.live or args.check_connection
    if ((args.allow_api or args.authorization or args.project_env) and not connected_mode) or (connected_mode and not (args.allow_api and args.authorization)):
        parser.error("Live requires independent --live --allow-api --authorization")
    if not args.prepare and not args.frozen_dir:
        parser.error("Prepare a fresh frozen cohort first; --frozen-dir is required")
    try:
        if args.prepare:
            with isolated_runtime(True):
                result = prepare(args.config, args.cases, args.db_root, args.out_dir)
            print(f"Frozen {len(result['cases'])} new cases; no model requests; separate Live authorization required.")
        elif args.check_connection:
            from bird_starter.adaptive_validation_connection import check_connection
            result = check_connection(args.frozen_dir, args.cases, args.db_root, args.out_dir, args.authorization,
                                      project_env=args.project_env)
            print(f"Connection {result['status']}; frozen model available; catalog reservations=1; model generation requests=0.")
        else:
            result = run_validation(args.frozen_dir, args.cases, args.db_root, args.out_dir,
                execution="live" if args.live else "mock" if args.mock_llm else "dry-run", allow_api=args.allow_api,
                authorization=args.authorization, max_attempts=args.max_attempts, project_env=args.project_env)
            print(f"{result['execution']}: 10 paired cases/group; reservations={result['reserved_attempts']}; "
                  f"real model reservations={result['real_model_request_reservations']}; official EX NOT EVALUATED.")
            return 1 if result["stopped_reason"] else 0
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("P1B rejected: check frozen inputs, unique outputs, authorization, usage and verified billing; never retry Live")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
