"""Offline validation of user-downloaded classic Mini-Dev; never emit Gold SQL."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

# Support the documented `python scripts/validate_dataset.py` invocation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.data import dataset_paths, find_db, load_cases
from bird_starter.tools import ro_connect


SOURCE_REVISION = "f65faf4ae3b638c1fa6df1d3370c8d92c8366301"
SOURCE_FILE = "data/mini_dev_sqlite-00000-of-00001.json"
SOURCE_URL = f"https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/{SOURCE_REVISION}/{SOURCE_FILE}"
# Git blob SHA-1 from the official Hub tree API, not a raw-file SHA-1 or SHA-256.
EXPECTED_GIT_BLOB = "f1ee766b3e438395cd3f8282adc10d483f6d91de"
EXPECTED_CASES = 500
EXPECTED_DATABASES = 11


def file_fingerprints(path: Path) -> dict:
    """Stream hashes without printing file contents or loading large DBs in RAM."""
    size = path.stat().st_size
    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1(f"blob {size}\0".encode("ascii"), usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            git_blob.update(chunk)
    return {"bytes": size, "sha256": sha256.hexdigest(), "git_blob_sha1": git_blob.hexdigest()}


def inspect_database(path: Path, timeout_seconds: float = 30.0) -> dict:
    """Read integrity and bounded DDL/column previews; never execute case SQL."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    start = time.monotonic()
    with closing(ro_connect(path)) as conn:
        conn.set_progress_handler(lambda: int(time.monotonic() - start > timeout_seconds), 1000)
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check(10)")]
        if integrity != ["ok"]:
            # SQLite diagnostics can contain data values. Keep only their count.
            raise ValueError(f"SQLite integrity_check failed ({len(integrity)} diagnostic rows)")
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        if table_count == 0:
            raise ValueError("Database contains no user tables; verify the official package extraction")
        tables = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 3"
        ).fetchall()
        preview = []
        for name, ddl in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = conn.execute(f"PRAGMA table_info({quoted})").fetchmany(21)
            preview.append({
                "table": name, "ddl": (ddl or "")[:2000], "ddl_truncated": len(ddl or "") > 2000,
                "columns": [{"name": c[1], "type": c[2], "not_null": bool(c[3]),
                             "primary_key": bool(c[5])} for c in columns[:20]],
                "columns_truncated": len(columns) > 20,
            })
    return {"integrity_check": "ok", "table_count": table_count,
            "schema_preview": preview, "tables_truncated": table_count > len(preview)}


def validate_dataset(cases_path: str | Path, db_root: str | Path, *, timeout_seconds: float = 30.0,
                     check_compatibility: bool = False) -> dict:
    """Return a local audit report; content identity and integrity are separate checks."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    cases_path, db_root = Path(cases_path).resolve(), Path(db_root).resolve()
    report = {
        "dataset_name": "BIRD Mini-Dev", "version_verified": False,
        "observed_source_revision": None, "json_sha256": None,
        "profile": "classic-mini-dev-sqlite-500", "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": SOURCE_URL, "source_revision": SOURCE_REVISION,
        "expected_git_blob_sha1": EXPECTED_GIT_BLOB,
        "expected_case_count": EXPECTED_CASES, "expected_database_count": EXPECTED_DATABASES,
        "cases_path": str(cases_path), "db_root": str(db_root),
        "case_count": None, "database_count": None, "databases": [], "errors": [], "ok": False,
        "matched_question_count": None, "unmatched_question_count": None,
    }
    try:
        # Fingerprint malformed files too, without copying source text into logs.
        if cases_path.is_file():
            report["cases_fingerprint"] = file_fingerprints(cases_path)
            report["json_sha256"] = report["cases_fingerprint"]["sha256"]
            report["version_verified"] = report["cases_fingerprint"]["git_blob_sha1"] == EXPECTED_GIT_BLOB
            if report["version_verified"]:
                report["observed_source_revision"] = SOURCE_REVISION
        cases = load_cases(cases_path)
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report
    db_ids = sorted({case["db_id"] for case in cases})
    question_counts = Counter(case["db_id"] for case in cases)
    report.update(case_count=len(cases), database_count=len(db_ids))
    if len(cases) != EXPECTED_CASES:
        report["errors"].append(f"Expected {EXPECTED_CASES} cases, found {len(cases)}; do not mix Mini-Dev V2 or partial splits")
    if len(db_ids) != EXPECTED_DATABASES:
        report["errors"].append(f"Expected {EXPECTED_DATABASES} databases, found {len(db_ids)}")
    if report["cases_fingerprint"]["git_blob_sha1"] != EXPECTED_GIT_BLOB:
        report["errors"].append("Cases bytes differ from the pinned official SQLite revision; download source_url from this report without reformatting")
    for db_id in db_ids:
        entry = {"db_id": db_id, "question_count": question_counts[db_id], "ok": False}
        try:
            path = find_db(db_root, db_id)
            entry["path"] = str(path)
            entry.update(inspect_database(path, timeout_seconds))
            entry["fingerprint"] = file_fingerprints(path)
            entry["ok"] = True
        except (OSError, ValueError, sqlite3.Error) as exc:
            # Avoid SQL/data snippets in SQLite exceptions.
            error = (f"SQLite {type(exc).__name__}: integrity/schema inspection failed or timed out"
                     if isinstance(exc, sqlite3.Error) else str(exc))
            entry["error"] = error
            report["errors"].append(f"Database {db_id}: {error}")
        report["databases"].append(entry)
    matched = sum(entry["question_count"] for entry in report["databases"] if entry["ok"])
    report.update(matched_question_count=matched, unmatched_question_count=len(cases) - matched)
    if check_compatibility:
        from bird_starter.evaluation import schema_compatibility_report
        paths = {entry["db_id"]: entry["path"] for entry in report["databases"] if entry["ok"]}
        report["schema_compatibility"] = schema_compatibility_report(cases, paths)
        if report["schema_compatibility"]["failed"]:
            report["errors"].append("Offline evaluator schema compatibility check failed; see case IDs, no gold SQL is logged")
    report["ok"] = not report["errors"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=None, help="Defaults to BIRD_CASES_PATH or project layout")
    parser.add_argument("--db-root", default=None, help="Defaults to BIRD_DB_ROOT or project layout")
    parser.add_argument("--check-schema-compatibility", action="store_true",
                        help="Offline evaluator-only EXPLAIN of gold SQL; never sent to Agent tools")
    parser.add_argument("--timeout-seconds", type=float, default=30.0,
                        help="SQLite VM inspection deadline per database (default: 30)")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and positive")
    cases_path, db_root = dataset_paths(args.cases, args.db_root)
    report = validate_dataset(cases_path, db_root, timeout_seconds=args.timeout_seconds,
                              check_compatibility=args.check_schema_compatibility)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
