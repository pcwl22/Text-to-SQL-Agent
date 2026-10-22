"""Dataset loading for the classic 500-row BIRD Mini-Dev SQLite split."""
from __future__ import annotations
import json
import os
from contextlib import closing
from dataclasses import dataclass, asdict
from pathlib import Path
import re
import sqlite3


def dataset_paths(cases: str | Path | None = None, db_root: str | Path | None = None) -> tuple[Path, Path]:
    """Explicit CLI paths > shared environment settings > project defaults."""
    project = Path(__file__).resolve().parents[1]
    cases = cases if cases is not None else os.environ.get(
        "BIRD_CASES_PATH", str(project / "data/mini_dev_data/mini_dev_sqlite.json"))
    db_root = db_root if db_root is not None else os.environ.get(
        "BIRD_DB_ROOT", str(project / "data/mini_dev_data/dev_databases"))
    if not str(cases).strip() or not str(db_root).strip():
        raise ValueError("Dataset paths must not be empty")
    return Path(cases), Path(db_root)


@dataclass(frozen=True)
class AgentInput:
    """Explicit generation allowlist; evaluation records must stay outside State."""
    question: str
    evidence: str
    db_path: str
    mode: str

    @classmethod
    def from_case(cls, case: dict, db_path: str | Path, mode: str, *, no_evidence: bool = False):
        return cls(question=case["question"], evidence="" if no_evidence else case["evidence"],
                   db_path=str(db_path), mode=mode)

    def to_state(self) -> dict:
        return asdict(self)


def _check_database(path: Path) -> None:
    """Check the located file without writes or executing any question SQL."""
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL LIMIT 1"
            ).fetchone()
            if table is None:
                raise ValueError(f"Database contains no user tables: {path}")
    except sqlite3.Error as exc:
        raise ValueError(f"SQLite {type(exc).__name__}: cannot inspect read-only database {path}; "
                         "verify that the file is a valid, readable SQLite database") from None


def validate_db_id(db_id: str) -> str:
    """Accept portable directory names, never paths or Windows device names."""
    reserved = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)
    }
    if (not isinstance(db_id, str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", db_id) is None
            or db_id.upper() in reserved):
        raise ValueError("Invalid db_id: expected a portable database name, not a path")
    return db_id


def _read_records(path: Path) -> list:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Cases file not found: {path}. Download the official classic Mini-Dev "
            "SQLite questions separately; see docs/DATASET.md."
        ) from None
    except UnicodeError:
        raise ValueError(f"Cases file must be UTF-8 JSON or JSON Lines: {path}") from None
    try:
        records = json.loads(text)
    except json.JSONDecodeError:
        # Hugging Face JSON shards may use one record per line.
        try:
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        except json.JSONDecodeError:
            # Do not echo source snippets: they can contain evaluation-only SQL.
            raise ValueError(f"Invalid cases JSON/JSON Lines: {path}") from None
        if not records:
            raise ValueError(f"Empty cases file: {path}")
    if isinstance(records, dict) and path.suffix.lower() in {".jsonl", ".ndjson"}:
        records = [records]
    if not isinstance(records, list):
        raise ValueError(f"Expected a JSON list or JSON Lines of case objects: {path}")
    return records


def load_cases(path: str | Path, difficulty: str | None = None, limit: int | None = None) -> list[dict]:
    """Validate all records, preserve file order, then filter and limit.

    Raw SQL is retained exclusively for the offline evaluator. Never pass these
    complete records to a model, graph state, schema service or MCP tool.
    """
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a non-negative integer or None")
    if difficulty is not None and not isinstance(difficulty, str):
        raise ValueError("difficulty must be a string or None")
    selected, seen = [], set()
    for index, record in enumerate(_read_records(Path(path))):
        if not isinstance(record, dict):
            raise ValueError(f"Expected a case object at index {index}")
        for key in ("db_id", "question", "SQL", "evidence"):
            if key not in record:
                raise ValueError(f"Missing required field {key} in case {index}")
            if not isinstance(record[key], str) or (key != "evidence" and not record[key].strip()):
                raise ValueError(f"Invalid {key} in case {index}: expected a string"
                                 + (" (empty allowed)" if key == "evidence" else " (non-empty)"))
        validate_db_id(record["db_id"])
        if "difficulty" in record and not isinstance(record["difficulty"], str):
            raise ValueError(f"Invalid difficulty in case {index}: expected a string")
        case_id = record.get("question_id", record.get("case_id", index))
        if not ((type(case_id) is int and case_id >= 0)
                or (isinstance(case_id, str) and case_id.strip() == case_id and case_id)):
            raise ValueError(f"Invalid case ID at index {index}")
        if "question_id" in record and "case_id" in record and record["case_id"] != case_id:
            raise ValueError(f"Conflicting question_id/case_id at index {index}")
        if str(case_id) in seen:
            raise ValueError(f"Duplicate case ID at index {index}")
        seen.add(str(case_id))
        if not difficulty or record.get("difficulty", "").lower() == difficulty.lower():
            selected.append({**record, "case_id": case_id})
    return selected if limit is None else selected[:limit]


def find_db(db_root: str | Path, db_id: str) -> Path:
    """Find exactly one named SQLite file; reject escapes and ambiguous copies."""
    validate_db_id(db_id)
    base = Path(db_root).resolve()
    if not base.is_dir():
        raise FileNotFoundError(f"Database root is not a directory: {base}. "
                                "Extract the official DB package; see docs/DATASET.md.")
    folder = base / db_id
    if not folder.resolve().is_relative_to(base):
        raise ValueError("Database path escaped the configured data root")
    candidates = [base / f"{db_id}.sqlite"]
    if folder.is_dir():
        candidates.extend(folder.rglob(f"{db_id}.sqlite"))
    matches = set()
    for path in candidates:
        if path.is_file():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(base):
                raise ValueError("Database path escaped the configured data root")
            matches.add(resolved)
    if len(matches) > 1:
        raise ValueError(f"Ambiguous database {db_id}: multiple matching SQLite files under {base}")
    if matches:
        path = matches.pop()
        _check_database(path)
        return path
    raise FileNotFoundError(f"Cannot locate {db_id}.sqlite under {base}. Extract the official DB package first.")
