import json
from pathlib import Path
import subprocess
import os
import sys

import pytest

from bird_starter.baseline import run_baseline
from bird_starter.data import AgentInput
from bird_starter.model import ModelFailure, TrackedModel, RESPONSE_PARSER_VERSION
import run_eval


class ScriptedModel:
    model = "offline-fixture-model"

    def __init__(self, answers):
        self.answers = iter(answers)
        self.prompts = []

    def ask(self, system, user):
        self.prompts.append((system, user))
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


def input_for(db):
    return AgentInput("fixture total", "fixture clue", str(db), "baseline")


def test_full_schema_one_candidate_no_semantic_retry(tiny_db):
    model = ScriptedModel(["SELECT SUM(amount) FROM orders"])
    result = run_baseline(input_for(tiny_db[1]), TrackedModel(model))
    assert result["execution_ok"] is True and result["attempts"] == 1
    assert set(result["schema_tables"]) == {"orders", "unrelated"}
    assert len(model.prompts) == 1
    assert "CREATE TABLE unrelated" in model.prompts[0][1]
    assert "fixture clue" in model.prompts[0][1]


@pytest.mark.parametrize("answer,category", [
    ("not a SQL query", "sql_syntax_error"),
    ("SELECT ( FROM", "sql_syntax_error"),
    ("SELECT missing FROM orders", "sql_schema_error"),
    ("SELECT * FROM missing", "sql_schema_error"),
    ("DROP TABLE orders", "sql_safety_error"),
    ("SELECT load_extension('evil')", "sql_safety_error"),
    ("", "empty_response"),
    (ModelFailure("model_network_error"), "model_network_error"),
])
def test_failure_is_structured_and_never_repaired(tiny_db, answer, category):
    model = ScriptedModel([answer])
    record = run_baseline(input_for(tiny_db[1]), TrackedModel(model))
    assert record["execution_ok"] is False
    assert record["error_type"] == category
    assert len(model.prompts) == 1
    if category.startswith("sql_"):
        assert record["execution_time_ms"] is not None


def test_timeout_and_truncation(tiny_db):
    query = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x"
    record = run_baseline(input_for(tiny_db[1]), TrackedModel(ScriptedModel([query])), sql_timeout=.001)
    assert record["error_type"] == "sql_timeout"
    record = run_baseline(input_for(tiny_db[1]), TrackedModel(ScriptedModel(["SELECT * FROM orders"])), max_rows=1)
    assert record["output_truncated"] is True


def setup_cli(tmp_path, tiny_db, monkeypatch, answers, *, db_ids=("demo", "demo")):
    path = tmp_path / "fixture_cases.json"
    rows = [{"question_id": index, "db_id": db_id, "question": "fixture total", "evidence": "clue",
             "SQL": "GOLD_ONLY_FIXTURE_MARKER"} for index, db_id in enumerate(db_ids)]
    path.write_text(json.dumps(rows), encoding="utf-8")
    fake = ScriptedModel(answers)
    monkeypatch.setattr(run_eval, "Model", lambda **kwargs: fake)
    def unexpected_graph(*args, **kwargs):
        raise AssertionError("Baseline must not construct or invoke a Graph")
    monkeypatch.setattr(run_eval, "build_graph", unexpected_graph)
    monkeypatch.setenv("OPENAI_MODEL", "offline-fixture-model")
    out = tmp_path / "predictions.jsonl"
    argv = ["--mode", "baseline", "--cases", str(path), "--db-root", str(tiny_db[0]),
            "--out", str(out), "--allow-api"]
    return argv, out, fake


def test_batch_failure_continues_and_outputs_are_complete(tmp_path, tiny_db, monkeypatch):
    argv, out, fake = setup_cli(tmp_path, tiny_db, monkeypatch,
                                ["SELECT missing FROM orders", "SELECT SUM(amount) FROM orders"])
    evaluated = []
    def evaluator(db, predicted, gold):
        evaluated.append(gold)
        return True
    monkeypatch.setattr(run_eval, "result_match_proxy", evaluator)
    summary = run_eval.main(argv)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [r["case_id"] for r in rows] == [0, 1]
    assert [r["execution_ok"] for r in rows] == [False, True]
    assert all(r["model_calls"] == 1 for r in rows)
    assert summary["sql_generation_success_rate"] == 1
    assert summary["sql_execution_success_rate"] == .5
    assert summary["model_calls"] == 2
    assert summary["estimated_cost"] is None
    assert json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))["records"] == 2
    config = json.loads(out.with_suffix(".config.json").read_text(encoding="utf-8"))
    assert config["case_ids"] == [0, 1] and config["max_repairs"] == 0
    assert config["model"] == "offline-fixture-model"
    assert config["response_parser_version"] == RESPONSE_PARSER_VERSION
    assert evaluated == ["GOLD_ONLY_FIXTURE_MARKER"]
    assert "GOLD_ONLY_FIXTURE_MARKER" not in json.dumps(fake.prompts) + out.read_text(encoding="utf-8")


def test_missing_db_does_not_abort_batch(tmp_path, tiny_db, monkeypatch):
    argv, out, fake = setup_cli(tmp_path, tiny_db, monkeypatch, ["SELECT 1"], db_ids=("absent", "demo"))
    summary = run_eval.main(argv + ["--no-proxy"])
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["error_type"] == "data_error" and rows[0]["model_calls"] == 0
    assert rows[1]["execution_ok"] is True and len(fake.prompts) == 1
    assert summary["records"] == 2


def test_truncated_results_are_never_compared(tmp_path, tiny_db, monkeypatch):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, ["SELECT * FROM orders"])
    def forbidden(*args):
        raise AssertionError("Do not score truncated predictions")
    monkeypatch.setattr(run_eval, "result_match_proxy", forbidden)
    run_eval.main(argv + ["--limit", "1", "--max-rows", "1"])
    row = json.loads(out.read_text(encoding="utf-8"))
    assert row["output_truncated"] is True and row["local_result_match_proxy"] is None


@pytest.mark.parametrize("option", ["--dry-run", "zero"])
def test_dry_run_and_zero_limit_never_create_model(tmp_path, tiny_db, monkeypatch, option):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, [])
    def forbidden(**kwargs):
        raise AssertionError("No model constructor in a no-API run")
    monkeypatch.setattr(run_eval, "Model", forbidden)
    argv.remove("--allow-api")
    summary = run_eval.main(argv + (["--limit", "0"] if option == "zero" else ["--dry-run"]))
    assert summary["model_calls"] == 0 and summary["estimated_cost"] == 0
    assert summary["sql_execution_success_rate"] is None
    if option == "zero":
        assert out.read_text(encoding="utf-8") == ""
    else:
        assert summary["preflight_successes"] == 2


def test_api_permission_and_output_overwrite_guards(tmp_path, tiny_db, monkeypatch):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, ["SELECT 1"])
    without_permission = [arg for arg in argv if arg != "--allow-api"]
    with pytest.raises(SystemExit) as error:
        run_eval.main(without_permission)
    assert error.value.code == 2 and not out.exists()
    run_eval.main(argv + ["--limit", "1", "--no-proxy"])
    original = out.read_bytes()
    with pytest.raises(SystemExit):
        run_eval.main(argv)
    assert out.read_bytes() == original


def test_cannot_overwrite_dataset(tmp_path, tiny_db, monkeypatch):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, [])
    position = argv.index("--out") + 1
    argv[position] = str(tiny_db[1])
    before = tiny_db[1].read_bytes()
    with pytest.raises(SystemExit):
        run_eval.main(argv + ["--overwrite"])
    assert tiny_db[1].read_bytes() == before


def test_model_failure_and_secret_redaction(tmp_path, tiny_db, monkeypatch):
    secret = "TEST_ONLY_INVALID_CREDENTIAL"  # Synthetic Mock redaction fixture, never a provider key.
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    argv, out, fake = setup_cli(tmp_path, tiny_db, monkeypatch,
                             [RuntimeError(secret), f"SELECT '{secret}'"])
    run_eval.main(argv + ["--no-proxy"])
    for path in (out, out.with_suffix(".config.json"), out.with_suffix(".summary.json")):
        assert secret not in path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["error_type"] == "model_error"
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["stop_reason"] == "model_error" and summary["unprocessed_case_ids"] == [1]
    assert len(fake.prompts) == 1


def test_model_initialization_failure_stops_without_requests(tmp_path, tiny_db, monkeypatch):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, [])
    def failed(**kwargs):
        raise ModelFailure("model_configuration_error")
    monkeypatch.setattr(run_eval, "Model", failed)
    summary = run_eval.main(argv)
    assert summary["records"] == 1 and summary["model_calls"] == 0
    assert summary["error_counts"] == {"model_configuration_error": 1}
    assert summary["planned_cases"] == 2 and summary["unprocessed_case_ids"] == [1]
    assert summary["stopped_early"] is True


@pytest.mark.parametrize("category", ["model_authentication_error", "model_request_error",
                                    "model_network_error", "model_timeout", "model_rate_limit",
                                    "model_server_error", "empty_response", "model_budget_exceeded",
                                    "model_invalid_response", "model_empty_choices", "model_output_truncated",
                                    "model_unsupported_content", "model_reasoning_only"])
def test_provider_failure_stops_batch_before_next_request(tmp_path, tiny_db, monkeypatch, category):
    argv, out, fake = setup_cli(tmp_path, tiny_db, monkeypatch,
                               [ModelFailure(category), "SELECT 1"])
    summary = run_eval.main(argv + ["--no-proxy", "--request-retries", "0"])
    assert len(fake.prompts) == 1
    assert summary["records"] == summary["model_calls"] == 1
    assert summary["stop_reason"] == category and summary["unprocessed_case_ids"] == [1]
    assert summary["planned_cases"] == 2
    assert len(out.read_text(encoding="utf-8").splitlines()) == 1


def test_prompt_limit_keeps_schema_complete_or_fails(tmp_path, tiny_db, monkeypatch):
    argv, out, fake = setup_cli(tmp_path, tiny_db, monkeypatch, [])
    summary = run_eval.main(argv + ["--max-prompt-chars", "1"])
    assert summary["model_calls"] == 0 and not fake.prompts
    assert summary["error_counts"] == {"data_error": 2}


def test_failed_dry_run_cli_returns_nonzero_and_keeps_records(tmp_path, tiny_db, monkeypatch):
    argv, out, _ = setup_cli(tmp_path, tiny_db, monkeypatch, [])
    argv.remove("--allow-api")
    argv[argv.index("--db-root") + 1] = str(tmp_path / "missing")
    result = subprocess.run([sys.executable, str(Path(run_eval.__file__)), *argv, "--dry-run"],
                            capture_output=True, text=True, timeout=30,
                            env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"})
    assert result.returncode == 1
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["model_calls"] == 0 and summary["preflight_successes"] == 0
