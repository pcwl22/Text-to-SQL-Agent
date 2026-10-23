"""P1A offline schema-cost comparison. There is no live/allow-api option."""
import argparse
import os
from pathlib import Path
import socket
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.schema_cost_analysis import analyze_costs
from bird_starter.experiment_review import PROJECT_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite-config", default=str(PROJECT_ROOT / "configs/phase3c/ablation_v1.json"))
    parser.add_argument("--gate-config", default=str(PROJECT_ROOT / "configs/p1a/adaptive_schema_gate_v1.json"))
    parser.add_argument("--cases", default=str(PROJECT_ROOT / "data/mini_dev_data/mini_dev_sqlite.json"))
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--p0-run", default=str(PROJECT_ROOT / "outputs/observability_v1_delivery/real_mock_suite"))
    parser.add_argument("--a-run", default=str(PROJECT_ROOT / "outputs/phase3c_live/A_v1"))
    parser.add_argument("--b-run", default=str(PROJECT_ROOT / "outputs/phase3c_live/B_v1"))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    def deny(*_, **__):
        raise AssertionError("Offline-only analysis cannot connect to the network")
    previous = socket.socket.connect, socket.socket.connect_ex
    socket.socket.connect = socket.socket.connect_ex = deny
    try:
        report = analyze_costs(args.suite_config, args.cases, args.db_root, args.p0_run,
                               args.a_run, args.b_run, args.gate_config, args.out_dir)
    except (OSError, ValueError, KeyError, TypeError):
        parser.error("Offline analysis rejected: check pinned inputs, P0 evidence, gate configuration and fresh private output")
    finally:
        socket.socket.connect, socket.socket.connect_ex = previous
    print(f"Offline {report['planned_cases']} cases: {report['routing_counts']}; real API=0; Actual EX=NOT EVALUATED.")
    return 0


if __name__ == "__main__":
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    raise SystemExit(main())
