"""Offline evaluator preflight, using tiny SQLite fixtures only."""
from contextlib import closing
import json
import sqlite3

from bird_starter import evaluation


def test_compatibility_reports_without_executing_gold_or_logging_it(tmp_path, monkeypatch):
    path = tmp_path / "fixture.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE fixture (id INTEGER)")
        conn.execute("INSERT INTO fixture VALUES (1)")
        conn.commit()
    before = path.read_bytes()
    calls = []
    original = evaluation.ro_connect

    def connect(db_path):
        conn = original(db_path)
        conn.create_function("fixture_boom", 0, lambda: calls.append("executed"))
        return conn

    monkeypatch.setattr(evaluation, "ro_connect", connect)
    cases = [
        {"case_id": 0, "db_id": "demo", "SQL": "SELECT fixture_boom() FROM fixture"},
        {"case_id": 1, "db_id": "demo", "SQL": "SELECT secret_missing_column FROM fixture"},
        {"case_id": 2, "db_id": "demo", "SQL": "DROP TABLE fixture"},
        {"case_id": 3, "db_id": "absent", "SQL": "SELECT 1"},
    ]
    result = evaluation.schema_compatibility_report(cases, {"demo": path})
    assert (result["checked"], result["passed"], result["failed"]) == (4, 1, 3)
    assert [item["case_id"] for item in result["errors"]] == [1, 2, 3]
    assert calls == []
    assert "secret_missing_column" not in json.dumps(result)
    assert "DROP TABLE" not in json.dumps(result)
    assert path.read_bytes() == before
