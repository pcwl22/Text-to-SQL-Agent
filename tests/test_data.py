"""Tiny synthetic JSON/SQLite fixtures exclusively for offline unit tests."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from bird_starter.data import dataset_paths, find_db, load_cases, validate_db_id


def test_shared_dataset_paths_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("BIRD_CASES_PATH", raising=False)
    monkeypatch.delenv("BIRD_DB_ROOT", raising=False)
    project = Path(__file__).resolve().parents[1]
    assert dataset_paths() == (project / "data/mini_dev_data/mini_dev_sqlite.json",
                               project / "data/mini_dev_data/dev_databases")
    monkeypatch.setenv("BIRD_CASES_PATH", str(tmp_path / "env.json"))
    monkeypatch.setenv("BIRD_DB_ROOT", str(tmp_path / "env databases"))
    assert dataset_paths() == (tmp_path / "env.json", tmp_path / "env databases")
    assert dataset_paths(tmp_path / "cli.json", tmp_path / "cli db") == (
        tmp_path / "cli.json", tmp_path / "cli db")
    monkeypatch.setenv("BIRD_DB_ROOT", "")
    with pytest.raises(ValueError, match="empty"):
        dataset_paths()


def record(**changes):
    return {"db_id": "demo", "question": "fixture question", "evidence": "",
            "SQL": "SELECT 1", "difficulty": "simple", **changes}


def write_cases(tmp_path, rows):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("limit,count", [(0, 0), (1, 1), (2, 2), (100, 2), (None, 2)])
def test_limits(tmp_path, limit, count):
    path = write_cases(tmp_path, [record(question_id=8), record(question_id=2)])
    assert len(load_cases(path, limit=limit)) == count


@pytest.mark.parametrize("limit", [-1, True, 1.5, "1"])
def test_invalid_limits(tmp_path, limit):
    with pytest.raises(ValueError, match="limit"):
        load_cases(tmp_path / "unused.json", limit=limit)


def test_order_ids_and_filter_are_stable(tmp_path):
    path = write_cases(tmp_path, [record(question_id=8), record(difficulty="moderate"),
                                 record(question_id=2)])
    assert [c["case_id"] for c in load_cases(path)] == [8, 1, 2]
    for _ in range(2):
        assert [c["case_id"] for c in load_cases(path, "MODERATE", 1)] == [1]
    assert load_cases(path, "absent") == []


def test_explicit_case_id_without_question_id(tmp_path):
    assert load_cases(write_cases(tmp_path, [record(case_id="fixture-id")]))[0]["case_id"] == "fixture-id"


@pytest.mark.parametrize("rows", [
    [record(question_id=3), record(question_id=3)],
    [record(question_id=1), record()],  # fallback index collision
    [record(question_id=3), record(case_id="3")],
])
def test_duplicate_ids_even_outside_filter_or_limit(tmp_path, rows):
    path = write_cases(tmp_path, rows)
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(path, "absent", 0)


def test_conflicting_ids(tmp_path):
    with pytest.raises(ValueError, match="Conflicting"):
        load_cases(write_cases(tmp_path, [record(question_id=3, case_id=4)]))


@pytest.mark.parametrize("case_id", [None, True, -1, 1.2, "", " padded ", []])
def test_invalid_case_ids(tmp_path, case_id):
    with pytest.raises(ValueError, match="case ID"):
        load_cases(write_cases(tmp_path, [record(question_id=case_id)]))


@pytest.mark.parametrize("field", ["db_id", "question", "SQL", "evidence"])
def test_missing_required_fields(tmp_path, field):
    row = record()
    del row[field]
    with pytest.raises(ValueError, match=f"Missing required field {field}"):
        load_cases(write_cases(tmp_path, [row]))


@pytest.mark.parametrize("changes", [
    {"question": " "}, {"question": 1}, {"SQL": ""}, {"SQL": None},
    {"evidence": None}, {"db_id": 1}, {"difficulty": []},
])
def test_invalid_field_types(tmp_path, changes):
    with pytest.raises(ValueError, match="Invalid"):
        load_cases(write_cases(tmp_path, [record(**changes)]))


@pytest.mark.parametrize("rows", [{}, [None], [1], ["not a case"]])
def test_bad_top_level_or_record(tmp_path, rows):
    with pytest.raises(ValueError):
        load_cases(write_cases(tmp_path, rows))


def test_empty_list_and_bad_files(tmp_path):
    assert load_cases(write_cases(tmp_path, [])) == []
    with pytest.raises(FileNotFoundError, match="Download the official"):
        load_cases(tmp_path / "not-downloaded.json")
    path = tmp_path / "broken.json"
    path.write_text('[{"SQL": "DO_NOT_ECHO_GOLD"', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_cases(path)
    assert "DO_NOT_ECHO_GOLD" not in str(error.value)
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ValueError, match="UTF-8"):
        load_cases(path)


def test_bom_unicode_and_json_lines(tmp_path):
    path = tmp_path / "cases.json"
    rows = [record(question="测试问题"), record(question_id=9)]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8-sig")
    assert load_cases(path)[0]["question"] == "测试问题"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert [row["case_id"] for row in load_cases(path)] == [0, 9]
    single = tmp_path / "one.jsonl"
    single.write_text(json.dumps(record()), encoding="utf-8")
    assert len(load_cases(single)) == 1


@pytest.mark.parametrize("db_id", ["", ".", "..", "../demo", "a/b", "a\\b", "/demo",
                                   "C:demo", "C:\\demo", "a.sqlite", "a b", "NUL", "con",
                                   "COM1", None, 123])
def test_invalid_db_ids(tmp_path, db_id):
    with pytest.raises(ValueError, match="Invalid db_id"):
        find_db(tmp_path, db_id)
    with pytest.raises(ValueError, match="Invalid db_id"):
        validate_db_id(db_id)


@pytest.mark.parametrize("relative", ["demo/demo.sqlite", "demo/sqlite/demo.sqlite",
                                      "demo.sqlite", "demo/extracted/sqlite/demo.sqlite"])
def test_exact_db_layouts(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE fixture (id INTEGER)")
    assert find_db(tmp_path, "demo") == path.resolve()


def test_missing_wrong_name_and_bad_root(tmp_path):
    with pytest.raises(FileNotFoundError, match="root is not a directory"):
        find_db(tmp_path / "missing", "demo")
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "unrelated.sqlite").touch()
    with pytest.raises(FileNotFoundError, match="Cannot locate demo.sqlite"):
        find_db(tmp_path, "demo")
    with pytest.raises(FileNotFoundError, match="root is not a directory"):
        find_db(folder / "unrelated.sqlite", "demo")


def test_ambiguous_copies(tmp_path):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo.sqlite").touch()
    (tmp_path / "demo" / "demo.sqlite").touch()
    with pytest.raises(ValueError, match="Ambiguous"):
        find_db(tmp_path, "demo")


@pytest.mark.parametrize("contents", [b"", b"not a sqlite database"])
def test_find_db_rejects_empty_or_corrupt_database(tmp_path, contents):
    db = tmp_path / "demo.sqlite"
    db.write_bytes(contents)
    with pytest.raises(ValueError, match="no user tables|valid, readable SQLite"):
        find_db(tmp_path, "demo")
    assert db.read_bytes() == contents


@pytest.mark.parametrize("link_kind", ["folder", "file"])
def test_resolved_path_cannot_escape_root(tmp_path, monkeypatch, link_kind):
    # Simulate a resolved link target without requiring Windows symlink privileges.
    root = tmp_path / "root"
    folder = root / "demo"
    folder.mkdir(parents=True)
    db = folder / "demo.sqlite"
    db.touch()
    link = folder if link_kind == "folder" else db
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == link:
            return tmp_path / "outside"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="escaped"):
        find_db(root, "demo")
