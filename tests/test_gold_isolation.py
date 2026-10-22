"""Tiny fixtures + scripted model only; verify the existing generation boundary."""
from contextlib import closing
import json
import sqlite3
import sys

import pytest

from bird_starter.data import AgentInput
import run_eval


def test_agent_input_never_reads_gold_field(tmp_path):
    class EvaluationRecord(dict):
        def __getitem__(self, key):
            assert key != "SQL", "Generation must never read the gold field"
            return super().__getitem__(key)

    record = EvaluationRecord(question="fixture question", evidence="fixture evidence", SQL="fixture gold")
    state = AgentInput.from_case(record, tmp_path / "fixture.sqlite", "baseline").to_state()
    assert set(state) == {"question", "evidence", "db_path", "mode"}


@pytest.mark.parametrize("no_evidence", [False, True])
@pytest.mark.parametrize("path_source", ["cli", "environment"])
def test_runner_schema_generator_repair_and_state_exclude_gold(tmp_path, monkeypatch, capsys, no_evidence, path_source):
    sentinel = "EVALUATOR_ONLY_FIXTURE_MARKER"
    db_root = tmp_path / "databases"
    db_root.mkdir()
    db = db_root / "demo.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE fixture (id INTEGER)")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([{
        "question_id": 9, "db_id": "demo", "question": "fixture question",
        "evidence": "fixture clue", "SQL": sentinel,
    }]), encoding="utf-8")
    model_calls, states, evaluated_gold = [], [], []
    answers = iter(['{"tables":["fixture"]}', "SELECT missing FROM fixture", "SELECT id FROM fixture"])

    class ScriptedModel:
        def __init__(self, **kwargs):
            pass

        def ask(self, system, user):
            model_calls.append((system, user))
            return next(answers)

    actual_build_graph = run_eval.build_graph

    class RecordingGraph:
        def __init__(self, graph):
            self.graph = graph

        def invoke(self, initial):
            states.extend(self.graph.stream(initial, stream_mode="values"))
            return states[-1]

    def offline_evaluator(db_path, predicted_sql, gold_sql):
        evaluated_gold.append(gold_sql)
        assert predicted_sql == "SELECT id FROM fixture"
        return True

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(run_eval, "Model", ScriptedModel)
    monkeypatch.setattr(run_eval, "build_graph", lambda model, max_repairs, **kwargs: RecordingGraph(
        actual_build_graph(model, max_repairs, **kwargs)))
    monkeypatch.setattr(run_eval, "result_match_proxy", offline_evaluator)
    out = tmp_path / "predictions.jsonl"
    argv = ["run_eval.py", "--mode", "graph", "--out", str(out), "--allow-api"]
    if path_source == "cli":
        argv.extend(["--cases", str(cases), "--db-root", str(db_root)])
    else:
        monkeypatch.setenv("BIRD_CASES_PATH", str(cases))
        monkeypatch.setenv("BIRD_DB_ROOT", str(db_root))
    if no_evidence:
        argv.append("--no-evidence")
    monkeypatch.setattr(sys, "argv", argv)
    run_eval.main()
    assert len(model_calls) == 3  # existing schema selection, draft and error repair
    assert evaluated_gold == [sentinel]
    assert sentinel not in json.dumps(model_calls)
    assert sentinel not in json.dumps(states)
    assert all("SQL" not in state for state in states)
    assert ("fixture clue" in model_calls[1][1]) is not no_evidence
    assert json.loads(out.read_text(encoding="utf-8"))["attempts"] == 2
    assert sentinel not in out.read_text(encoding="utf-8") + capsys.readouterr().out
