"""Static SQLite fixtures only: official input contract, not official EX scores."""
import json
from pathlib import Path

import pytest

import bird_starter.experiment_review as review
from bird_starter.ablation import run_suite
from bird_starter.ablation_review_report import build_report
from bird_starter.experiment_review import read_group
from bird_starter.experiment_sets import file_sha256
from bird_starter.official_ex_inputs import (DELIMITER, FAILURE_SQL, engine_readiness, export_inputs,
                                            single_line_sql, validate_bundle)
from bird_starter.tools import execute_select, ro_connect
from contextlib import closing
from scripts.export_official_ex_inputs import main as export_main
from scripts.report_ablation import main as report_main
from test_ablation import suite_files, ScriptedSDK


@pytest.fixture(autouse=True)
def private_exports(monkeypatch, tmp_path):
    monkeypatch.setattr(review, "PRIVATE_OUTPUT_ROOT", tmp_path)


def fixture_run(tmp_path, suite_files, groups=("A",), execution="mock"):
    folder = tmp_path / "run"
    run_suite(*suite_files, folder, groups=groups, execution=execution, client_factory=(lambda g: ScriptedSDK()) if execution == "mock" else None)
    return folder


@pytest.mark.parametrize("sql", ["SELECT SUM(amount)\nFROM orders", "SELECT amount FROM orders GROUP\nBY amount",
    "SELECT 'a  b' AS \"odd name\"", "SELECT -- line comment\n 1", "SELECT 1 /* block\ncomment */ + 1",
    "SELECT - -1", "SELECT 'x''y'", "SELECT amount FROM orders WHERE amount != 3;"])
def test_static_sql_normalization_preserves_query_results(tiny_db, sql):
    before = file_sha256(tiny_db[1])
    converted = single_line_sql(sql)
    assert not any(c in converted for c in "\r\n\t")
    assert execute_select(tiny_db[1], sql)["rows"] == execute_select(tiny_db[1], converted)["rows"]
    with closing(ro_connect(tiny_db[1])) as conn:
        assert conn.execute("PRAGMA query_only").fetchone() == (1,)
    assert file_sha256(tiny_db[1]) == before


@pytest.mark.parametrize("sql", ["SELECT 'a\nb'", 'SELECT "a\nb"', "DROP TABLE orders", "SELECT 1; DELETE FROM orders"])
def test_unrepresentable_or_unsafe_sql_rejected(sql):
    with pytest.raises(Exception):
        single_line_sql(sql)


def test_static_export_correct_order_db_and_safe_sql(tmp_path, suite_files):
    folder = fixture_run(tmp_path, suite_files)
    path = folder / "A.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("".join(json.dumps(r) + "\n" for r in reversed(rows)))
    out = tmp_path / "bundle"
    bundle = export_inputs(folder, "A", suite_files[0], suite_files[1], out)
    assert [r["case_id"] for r in bundle["alignment"]] == [1, 2, 3]
    assert not any(r["forced_failure"] for r in bundle["alignment"])
    assert validate_bundle(out) == {"aligned_cases": 3, "official_ex": None, "format_compatible": True}
    pred = json.loads((out / "predictions.json").read_text())
    gold = (out / "gold.sql").read_text().splitlines()
    db = suite_files[2] / "demo/demo.sqlite"
    before = file_sha256(db)
    for key, line in zip(pred, gold):
        candidate, db_id = pred[key].split(DELIMITER)
        actual_gold, gold_db_id = line.split("\t")
        assert db_id == gold_db_id == "demo"
        assert execute_select(db, candidate)["rows"] == execute_select(db, actual_gold)["rows"] == [(8,)]
    assert file_sha256(db) == before and bundle["official_ex"] is None
    assert "BLOCKED" in bundle["official_engine"]["status"]


@pytest.mark.parametrize("damage", ["missing", "timeout", "truncated", "unsafe", "empty_file"])
def test_failures_never_disappear_from_official_input_denominator(tmp_path, suite_files, damage):
    folder = fixture_run(tmp_path, suite_files)
    path = folder / "A.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "missing": rows = rows[1:]
    elif damage == "empty_file": rows = []
    elif damage == "timeout": rows[0].update(error_category="sql_timeout", final_execution_ok=False)
    elif damage == "truncated": rows[0]["output_truncated"] = True
    else: rows[0]["final_sql"] = "DROP TABLE orders"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    out = tmp_path / "failures"
    bundle = export_inputs(folder, "A", suite_files[0], suite_files[1], out)
    assert bundle["planned_cases"] == 3 and bundle["alignment"][0]["forced_failure"]
    pred = json.loads((out / "predictions.json").read_text())
    assert pred["0"].split(DELIMITER)[0] == FAILURE_SQL
    with pytest.raises(Exception): execute_select(suite_files[2] / "demo/demo.sqlite", FAILURE_SQL)
    assert len((out / "gold.sql").read_text().splitlines()) == 3
    if damage == "empty_file":
        report = build_report({"A": folder}, suite_files[0], tmp_path / "empty_report")
        assert report["groups"]["A"]["model_calls"] is None
        assert report["groups"]["A"]["request_evidence"]["prepared_attempts"] == 3
        assert report["groups"]["A"]["local_result_match_proxy_rate"] == 0


@pytest.mark.parametrize("damage", ["db", "difficulty", "duplicate", "foreign_case", "run_id", "model", "flags", "attempt", "gold", "torn"])
def test_identity_or_audit_conflicts_block_adaptation(tmp_path, suite_files, damage):
    folder = fixture_run(tmp_path, suite_files)
    path = folder / "A.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "db": rows[0]["db_id"] = "wrong"
    elif damage == "difficulty": rows[0]["difficulty"] = "wrong"
    elif damage == "duplicate": rows.append(rows[0])
    elif damage == "foreign_case": rows[0]["case_id"] = 999
    elif damage == "run_id": rows[0]["run_id"] = "wrong"
    elif damage == "model": rows[0]["model_id"] = "wrong"
    elif damage == "flags": rows[0]["repair_enabled"] = True
    elif damage == "attempt": rows[0]["request_attempt_ids"] = ["not-recorded"]
    elif damage == "gold": rows[0]["SQL"] = "must not be forwarded"
    text = "".join(json.dumps(r) + "\n" for r in rows)
    path.write_text(text.rstrip("\n") if damage == "torn" else text)
    with pytest.raises(ValueError):
        export_inputs(folder, "A", suite_files[0], suite_files[1], tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_dataset_and_export_integrity_and_private_output(tmp_path, suite_files):
    folder = fixture_run(tmp_path, suite_files)
    output = tmp_path / "export"
    export_inputs(folder, "A", suite_files[0], suite_files[1], output)
    (output / "predictions.json").write_text('{}')
    with pytest.raises(ValueError): validate_bundle(output)
    with pytest.raises(FileExistsError): export_inputs(folder, "A", suite_files[0], suite_files[1], output)
    with pytest.raises(ValueError): export_inputs(folder, "A", suite_files[0], suite_files[1], tmp_path.parent / "outside_private_root")
    cases = suite_files[1]
    cases.write_text(cases.read_text() + " ")
    with pytest.raises(ValueError): export_inputs(folder, "A", suite_files[0], cases, tmp_path / "wrong_data")


def test_independent_run_id_report_no_model_or_gold_feedback(tmp_path, suite_files, monkeypatch):
    runs = {}
    for group in "ABCD":
        path = tmp_path / f"run_{group}"
        run_suite(*suite_files, path, groups=(group,), execution="mock", client_factory=lambda g: ScriptedSDK())
        runs[group] = path
    monkeypatch.setattr('bird_starter.ablation.Model', lambda **kw: pytest.fail("Report/export instantiated SDK"))
    output = tmp_path / "report"
    result = build_report(runs, suite_files[0], output)
    assert len({r["source_run_id"] for r in result["groups"].values()}) == 4
    assert len(result["paired_comparisons"]) == 6 and result["planned_cases_per_group"] == 3
    assert all(r["official_ex"] is None for r in result["groups"].values())
    rows = [json.loads(line) for line in (output / "per_case.jsonl").read_text().splitlines()]
    assert len(rows) == 12 and "GOLD_ONLY_SUITE_SENTINEL" not in (output / "per_case.jsonl").read_text()
    assert all(row["official_ex"] is None for row in rows)
    with pytest.raises(FileExistsError): build_report(runs, suite_files[0], output)


def test_dry_run_export_blocked_and_mixed_measurements_rejected(tmp_path, suite_files):
    live_mock = fixture_run(tmp_path, suite_files, groups=("A",))
    dry = tmp_path / "dry"
    run_suite(*suite_files, dry, groups=("B",))
    with pytest.raises(ValueError): export_inputs(dry, "B", suite_files[0], suite_files[1], tmp_path / "no_predictions")
    with pytest.raises(ValueError): build_report({"A": live_mock, "B": dry}, suite_files[0], tmp_path / "mixed")


def test_official_engine_readiness_does_not_fabricate_score():
    status = engine_readiness()
    assert status["official_ex"] is None and status["scoring_logic_modified"] is False
    assert status["commit"] == "abd11b6db92a1c9f809b32f7564c7c71b34d67f0"
    assert status["status"] == "BLOCKED_END_TO_END_UNVERIFIED"


def test_failure_placeholder_is_actual_sqlite_syntax_error(tiny_db):
    import sqlite3
    before = file_sha256(tiny_db[1])
    with closing(ro_connect(tiny_db[1])) as conn:
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            conn.execute(FAILURE_SQL).fetchall()
    assert file_sha256(tiny_db[1]) == before


def test_powershell_template_has_no_live_invocations():
    import subprocess
    import shutil
    if shutil.which("pwsh") is None:
        pytest.skip("PowerShell template syntax validation requires pwsh")
    source = Path(__file__).resolve().parents[1] / "docs/phase3c_live_commands.ps1"
    command = """
    $parseTokens = $null
    $parseErrors = $null
    $parsed = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$parseTokens, [ref]$parseErrors)
    if ($parseErrors.Count -gt 0) { exit 1 }
    $commands = $parsed.FindAll({param($node) $node -is [System.Management.Automation.Language.CommandAst]}, $true)
    foreach ($command in $commands) {
        if ($command.GetCommandName() -notin @('Split-Path', 'Join-Path')) { exit 2 }
    }
    """
    # Pass the path as a literal to avoid -Command argument semantics and shell interpolation.
    script = command.replace('$args[0]', "'" + str(source).replace("'", "''") + "'")
    result = subprocess.run(["pwsh", "-NoProfile", "-Command", script], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("damage", ["repair", "link", "budget", "audit_model"])
def test_review_checks_actual_node_limits_and_audit_config(tmp_path, suite_files, damage):
    folder = fixture_run(tmp_path, suite_files)
    path = folder / "A.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if damage == "repair": rows[0]["repair_attempts"] = 1
    elif damage == "link": rows[0]["node_metrics"] = {"schema_linking": {"model_calls": 1}}
    elif damage == "budget":
        config_path = folder / "batch.config.json"
        config = json.loads(config_path.read_text())
        config["group_budgets"]["A"] = 1
        config_path.write_text(json.dumps(config))
    else:
        audit_path = folder / "batch.attempts.jsonl"
        events = [json.loads(line) for line in audit_path.read_text().splitlines()]
        for event in events: event["model_id"] = "other-model"
        audit_path.write_text("".join(json.dumps(event) + "\n" for event in events))
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(ValueError): read_group(folder, "A", suite_files[0])


def test_offline_cli_tools(tmp_path, suite_files):
    folder = fixture_run(tmp_path, suite_files)
    args = ["--run-dir", str(folder), "--group", "A", "--suite-config", str(suite_files[0]),
            "--cases", str(suite_files[1]), "--out-dir", str(tmp_path / "cli_export")]
    assert export_main(args) == 0
    assert report_main(["--run", f"A={folder}", "--suite-config", str(suite_files[0]), "--out-dir", str(tmp_path / "cli_report")]) == 0
    with pytest.raises(SystemExit): report_main(["--run", f"A={folder}", "--run", f"A={folder}", "--out-dir", str(tmp_path / "bad")])
