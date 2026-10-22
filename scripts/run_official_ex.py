"""Offline only; no Model construction, credentials, API switch or experiment startup."""
import argparse
import multiprocessing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bird_starter.official_ex_windows import DEFAULT_SOURCE, DEFAULT_MANIFEST, run_isolated, score_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--db-root', required=True)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--source', default=str(DEFAULT_SOURCE))
    parser.add_argument('--manifest', default=str(DEFAULT_MANIFEST))
    parser.add_argument('--sql-timeout', type=float, default=30)
    parser.add_argument('--worker-timeout', type=float, default=40)
    parser.add_argument('--outer-timeout', type=float, default=1500)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.child:
            score_bundle(args.bundle, args.db_root, args.out_dir, source=args.source,
                         sql_timeout=args.sql_timeout, worker_timeout=args.worker_timeout, manifest=args.manifest)
            return 0
        result = run_isolated(args.bundle, args.db_root, args.out_dir, source=args.source,
                              sql_timeout=args.sql_timeout, worker_timeout=args.worker_timeout, outer_timeout=args.outer_timeout, manifest=args.manifest)
        print('Official evaluator process:', result['exit_code'], 'hard_timeout:', result['timed_out'])
        return 1 if result['timed_out'] or result['exit_code'] != 0 else 0
    except Exception as exc:
        # Category only: exceptions must not reveal input SQL or inherited keys.
        print('Offline evaluator failed:', type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
