"""Metadata-only fixed sample manifests; no SQL/prompt/model interaction."""
from collections import Counter
import hashlib
import json
from pathlib import Path


def make_dev_manifest(records, dataset_sha256, source_revision):
    metadata = [{"case_id": r["case_id"], "db_id": r["db_id"], "difficulty": r.get("difficulty", "unknown")}
                for r in records]
    selected = []
    for db_id in sorted({r["db_id"] for r in metadata}):
        candidates = sorted((r for r in metadata if r["db_id"] == db_id), key=lambda r: r["case_id"])
        chosen = []
        for difficulty in ("simple", "moderate", "challenging"):
            match = next((r for r in candidates if r["difficulty"].lower() == difficulty and r not in chosen), None)
            if match:
                chosen.append(match)
        for row in candidates:
            if len(chosen) == 3:
                break
            if row not in chosen:
                chosen.append(row)
        if len(chosen) != 3:
            raise ValueError("Each database must provide three distinct cases")
        selected.extend(chosen)
    return {"manifest_version": "bird-dev33-v1", "purpose": "development_schema_evaluation_not_held_out_test",
            "dataset_sha256": dataset_sha256, "source_revision": source_revision,
            "selection_rule": "db_id ascending; first case_id in simple/moderate/challenging; fill missing strata by lowest unused case_id",
            "selection_uses_gold_sql": False, "cases": selected,
            "case_count": len(selected), "database_count": len({r["db_id"] for r in selected}),
            "difficulty_counts": dict(Counter(r["difficulty"] for r in selected))}


def select_manifest_cases(records, path, dataset_sha256):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if manifest["dataset_sha256"] != dataset_sha256:
            raise ValueError()
        rows = manifest["cases"]
        if not isinstance(rows, list) or not rows:
            raise ValueError()
        lookup = {r["case_id"]: r for r in records}
        chosen, seen = [], set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"case_id", "db_id", "difficulty"}:
                raise ValueError()
            ident = row["case_id"]
            record = lookup[ident]
            if ident in seen or row["db_id"] != record["db_id"] or row["difficulty"] != record.get("difficulty", "unknown"):
                raise ValueError()
            seen.add(ident)
            chosen.append(record)
        return chosen
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("Invalid sample manifest, dataset fingerprint or case metadata mismatch") from None


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
