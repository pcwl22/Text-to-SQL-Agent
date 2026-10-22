"""Offline validation tests using tiny fixtures, never a simulated benchmark."""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from scripts import validate_dataset as validator


@pytest.fixture
def dataset_fixture(tmp_path):
    root = tmp_path / "databases"
    folder = root / "demo"
    folder.mkdir(parents=True)
    db = folder / "demo.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE "fixture""table" (id INTEGER PRIMARY KEY, name TEXT)')
        conn.execute('CREATE TABLE second (amount INTEGER)')
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([
        {"question_id": n, "db_id": "demo", "question": "fixture only", "evidence": "",
         "SQL": "EVALUATOR_ONLY_SENTINEL"} for n in (2, 9)
    ]), encoding="utf-8")
    return path, root, db


def fixture_profile(monkeypatch, path):
    # Test-only substitution of expectations. The CLI has no relaxed-count mode.
    monkeypatch.setattr(validator, "EXPECTED_CASES", 2)
    monkeypatch.setattr(validator, "EXPECTED_DATABASES", 1)
    monkeypatch.setattr(validator, "EXPECTED_GIT_BLOB", validator.file_fingerprints(path)["git_blob_sha1"])


def test_fingerprints(tmp_path):
    path = tmp_path / "bytes"
    path.write_bytes(b"fixture\n")
    result = validator.file_fingerprints(path)
    assert result["bytes"] == 8
    assert result["sha256"] == hashlib.sha256(b"fixture\n").hexdigest()
    assert result["git_blob_sha1"] == hashlib.sha1(b"blob 8\0fixture\n").hexdigest()


def test_default_profile_rejects_tiny_fixture(dataset_fixture):
    path, root, _ = dataset_fixture
    report = validator.validate_dataset(path, root)
    assert (report["case_count"], report["database_count"]) == (2, 1)
    assert report["ok"] is False
    assert any("Expected 500 cases" in e for e in report["errors"])
    assert any("Expected 11 databases" in e for e in report["errors"])
    assert any("pinned official" in e for e in report["errors"])
    assert "EVALUATOR_ONLY_SENTINEL" not in json.dumps(report)


def test_fixture_integrity_schema_read_only_and_cli(dataset_fixture, monkeypatch, capsys):
    path, root, db = dataset_fixture
    fixture_profile(monkeypatch, path)
    before = db.read_bytes()
    assert validator.main(["--cases", str(path), "--db-root", str(root)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["ok"] is True
    assert report["version_verified"] is True
    assert report["json_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (report["matched_question_count"], report["unmatched_question_count"]) == (2, 0)
    entry = report["databases"][0]
    assert entry["question_count"] == 2
    assert entry["integrity_check"] == "ok"
    assert entry["table_count"] == 2
    assert entry["schema_preview"][0]["table"] == 'fixture"table'
    assert entry["schema_preview"][0]["columns"][0]["name"] == "id"
    assert "EVALUATOR_ONLY_SENTINEL" not in output
    assert db.read_bytes() == before
    with closing(validator.ro_connect(db)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden (x)")


def test_version_mismatch_even_if_counts_match(dataset_fixture, monkeypatch):
    path, root, _ = dataset_fixture
    fixture_profile(monkeypatch, path)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    report = validator.validate_dataset(path, root)
    assert report["ok"] is False
    assert any("Cases bytes differ" in e for e in report["errors"])
    assert report["version_verified"] is False
    assert report["observed_source_revision"] is None


def test_missing_cases_cli_is_actionable_and_standalone(tmp_path):
    script = Path(validator.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--cases", str(tmp_path / "not-downloaded.json"),
         "--db-root", str(tmp_path / "missing")],
        cwd=tmp_path, text=True, capture_output=True, timeout=30,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["case_count"] is None and report["database_count"] is None
    assert "Download the official" in report["errors"][0]
    assert not result.stderr


def test_missing_database_and_corruption(dataset_fixture, monkeypatch):
    path, root, db = dataset_fixture
    fixture_profile(monkeypatch, path)
    db.unlink()
    report = validator.validate_dataset(path, root)
    assert report["ok"] is False
    assert "Cannot locate" in report["databases"][0]["error"]
    assert (report["matched_question_count"], report["unmatched_question_count"]) == (0, 2)
    db.write_bytes(b"not a sqlite database")
    report = validator.validate_dataset(path, root)
    assert "SQLite DatabaseError" in report["databases"][0]["error"]
    assert report["ok"] is False


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timeouts(tmp_path, value):
    with pytest.raises(ValueError, match="positive"):
        validator.validate_dataset(tmp_path / "unused", tmp_path, timeout_seconds=value)
    with pytest.raises(ValueError, match="positive"):
        validator.inspect_database(tmp_path / "unused", value)
    with pytest.raises(SystemExit) as exc:
        validator.main([f"--timeout-seconds={value}"])
    assert exc.value.code == 2


def test_bounded_preview(dataset_fixture):
    _, _, db = dataset_fixture
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE aaa (" + ", ".join(f"c{n} TEXT" for n in range(25)) + ")")
        for n in range(5):
            conn.execute(f"CREATE TABLE extra{n} (id INTEGER)")
    preview = validator.inspect_database(db)
    assert len(preview["schema_preview"]) == 3
    assert preview["tables_truncated"] is True
    assert len(preview["schema_preview"][0]["columns"]) == 20
    assert preview["schema_preview"][0]["columns_truncated"] is True


def test_integrity_timeout_is_reported(dataset_fixture, monkeypatch):
    path, root, db = dataset_fixture
    fixture_profile(monkeypatch, path)
    with closing(sqlite3.connect(db)) as conn:
        conn.executemany("INSERT INTO second VALUES (?)", [(n,) for n in range(1000)])
        conn.commit()
    report = validator.validate_dataset(path, root, timeout_seconds=1e-12)
    assert report["ok"] is False
    assert "timed out" in report["databases"][0]["error"]


def test_empty_database_is_not_accepted(dataset_fixture, monkeypatch):
    path, root, db = dataset_fixture
    fixture_profile(monkeypatch, path)
    db.write_bytes(b"")
    report = validator.validate_dataset(path, root)
    assert report["ok"] is False
    assert "no user tables" in report["databases"][0]["error"]


def test_invalid_json_still_has_checksum_and_no_source_text(tmp_path):
    path = tmp_path / "bad.json"
    path.write_bytes(b'{"SQL": "SECRET_FIXTURE_VALUE"')
    report = validator.validate_dataset(path, tmp_path)
    assert report["ok"] is False
    assert report["json_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "SECRET_FIXTURE_VALUE" not in json.dumps(report)


def test_named_cli_entry_point(tmp_path):
    script = Path(validator.__file__).with_name("validate_bird_data.py")
    result = subprocess.run(
        [sys.executable, str(script), "--cases", str(tmp_path / "absent.json"),
         "--db-root", str(tmp_path)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["json_sha256"] is None
    assert not result.stderr


def test_validator_reads_shared_environment(dataset_fixture, monkeypatch, capsys):
    path, root, _ = dataset_fixture
    fixture_profile(monkeypatch, path)
    monkeypatch.setenv("BIRD_CASES_PATH", str(path))
    monkeypatch.setenv("BIRD_DB_ROOT", str(root))
    assert validator.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["db_root"] == str(root.resolve())
    monkeypatch.setenv("BIRD_DB_ROOT", str(root / "wrong"))
    assert validator.main(["--db-root", str(root)]) == 0


def test_schema_compatibility_flag_fails_closed_without_gold_logs(dataset_fixture, monkeypatch, capsys):
    path, root, _ = dataset_fixture
    fixture_profile(monkeypatch, path)
    assert validator.main(["--cases", str(path), "--db-root", str(root),
                           "--check-schema-compatibility"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["schema_compatibility"]["failed"] == 2
    assert "EVALUATOR_ONLY_SENTINEL" not in output
