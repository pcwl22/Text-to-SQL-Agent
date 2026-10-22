"""Real dev33 SQLite + scripted SDK; mechanical recovery, never BIRD answer accuracy."""
from collections import deque
from contextlib import closing
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace as NS

import pytest

from bird_starter.baseline import run_baseline
from bird_starter.data import AgentInput, dataset_paths, find_db, load_cases
from bird_starter.experiment_sets import file_sha256, select_manifest_cases
from bird_starter.model import Model, TrackedModel
from bird_starter.repair_reporting import repair_summary
from bird_starter.request_audit import RequestLog, inspect_request_log, write_new_json
from bird_starter.schema_linking import read_schema
from bird_starter.schema_workflow import build_schema_graph, schema_graph_record
from bird_starter.tools import ro_connect


@pytest.mark.bird_integration
def test_real_dev33_four_modes_mock_repair(tmp_path):
    if os.environ.get("BIRD_RUN_INTEGRATION") != "1":
        pytest.skip("Real repair integration requires explicit opt-in downloaded BIRD data")
    project = Path(__file__).resolve().parents[1]
    manifest = project / "configs/phase3a/bird_dev33_v1.json"
    manifest_before = file_sha256(manifest)
    cases_path, db_root = dataset_paths()
    cases = select_manifest_cases(load_cases(cases_path), manifest, file_sha256(cases_path))
    assert len(cases) == 33 and len({c["db_id"] for c in cases}) == 11
    paths = {c["db_id"]: find_db(db_root, c["db_id"]) for c in cases}
    before = {db: file_sha256(path) for db, path in paths.items()}
    snapshots = {db: read_schema(path) for db, path in paths.items()}
    folder = Path(os.environ["BIRD_REPAIR_REPORT_DIR"]) if os.environ.get("BIRD_REPAIR_REPORT_DIR") else tmp_path / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    results, groups = [], {}
    for mode, linking, repairing in (("baseline", False, False), ("schema_only", True, False),
                                    ("repair_only", False, True), ("full", True, True)):
        queue, calls = deque(), []
        log_path = folder / f"{mode}.attempts.jsonl"
        log = RequestLog(log_path, f"offline-{mode}", "offline-mock-not-provider", 99)
        def create(**kw):
            assert len(calls) < 99
            calls.append(kw)
            return NS(status_code=200, _request_id=f"mock-{len(calls)}",
                      choices=[NS(message=NS(content=queue.popleft()), finish_reason="stop")])
        model = Model(model="offline-mock-not-provider", max_retries=0, max_calls=99,
                      client=NS(chat=NS(completions=NS(create=create))), attempt_log=log)
        tracked = TrackedModel(model)
        graph = None if mode == "baseline" else build_schema_graph(tracked, schema_linking=linking, sql_repair=repairing)
        rows = []
        try:
            for index, case in enumerate(cases):
                snapshot = snapshots[case["db_id"]]
                table = next(iter(snapshot["tables"]))
                column = snapshot["tables"][table]["columns"][0]["name"]
                quoted = '"' + table.replace('"', '""') + '"'
                assert "__phase3b_absent__" not in snapshot["tables"]
                candidate = ("SELECT 1" if index % 3 == 0 else 'SELECT * FROM "__phase3b_absent__"'
                             if index % 3 == 1 else f"SELECT FROM {quoted}")
                if linking:
                    queue.append(json.dumps({"tables": [{"name": table, "columns": [column]}]}))
                queue.append(candidate)
                if repairing and index % 3:
                    queue.append(f"SELECT 1 FROM {quoted} LIMIT 1")
                tracked.reset()
                model.set_case_id(case["case_id"])
                agent = AgentInput.from_case(case, paths[case["db_id"]], mode)
                assert set(agent.to_state()) == {"question", "evidence", "db_path", "mode"}
                started = time.monotonic()
                if graph:
                    output = graph.invoke(dict(agent.to_state(), SQL="GOLD_REAL_REPAIR_SENTINEL"))
                    assert "SQL" not in output and output["full_schema"] == snapshot["full_schema"]
                    row = schema_graph_record(output)
                else:
                    row = run_baseline(agent, tracked)
                    row.update(initial_execution_ok=row["execution_ok"], repair_attempts=0, repair_trigger=False,
                               repair_success=None, repair_execution_recovered=False)
                row.update(case_id=case["case_id"], db_id=case["db_id"], mode=mode, **tracked.metrics,
                           latency_ms=(time.monotonic() - started) * 1000, offline_mock=True,
                           measurement_kind="SCRIPTED_RECOVERY_NOT_TEXT_TO_SQL_ACCURACY")
                assert not queue and row["model_calls"] <= 3
                assert row["repair_attempts"] == int(repairing and index % 3 != 0)
                assert row["execution_ok"] == (repairing or index % 3 == 0)
                rows.append(row)
            report = inspect_request_log(log_path, f"offline-{mode}", 99)
            assert report["prepared_attempts"] == len(calls) == sum(r["model_calls"] for r in rows)
            assert report["indeterminate_attempts"] == 0
            assert "GOLD_REAL_REPAIR_SENTINEL" not in json.dumps(calls) + json.dumps(rows)
            assert len(calls) == {"baseline": 33, "schema_only": 66, "repair_only": 55, "full": 88}[mode]
            summary = repair_summary(rows)
            groups[mode] = {"summary": summary, "mock_sdk_calls": len(calls), "audit": report}
            results.extend(rows)
        finally:
            log.close()
    for db, path in paths.items():
        with closing(ro_connect(path)) as conn:
            assert conn.execute("PRAGMA query_only").fetchone() == (1,)
        assert file_sha256(path) == before[db]
    assert file_sha256(manifest) == manifest_before
    with (folder / "four_groups_mock33.jsonl").open("x", encoding="utf-8") as stream:
        for row in results:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_new_json(folder / "four_groups_summary.json", {
        "remote_api_requests": 0, "cases_per_group": 33, "databases": 11,
        "measurement_kind": "OFFLINE_SCRIPTED_RECOVERY_NOT_MODEL_QUALITY", "manifest_sha256": manifest_before,
        "sqlite_sha256_unchanged": True, "databases_before_and_after": before, "groups": groups})
