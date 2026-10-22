"""Offline Gold-only schema recall evaluation. No model imports, SDK, credentials or API calls."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bird_starter.data import load_cases, find_db
from bird_starter.experiment_sets import select_manifest_cases, file_sha256
from bird_starter.schema_linking import read_schema
from bird_starter.schema_evaluation import evaluate_selection, summarize_schema
from bird_starter.request_audit import write_new_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--selections", required=True, help="Recorded validated schema selections, never prompts")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    cases = select_manifest_cases(load_cases(args.cases), args.manifest, file_sha256(args.cases))
    output = Path(args.out).resolve()
    protected = {Path(p).resolve() for p in (args.cases, args.manifest, args.selections)}
    if output in protected or output.is_relative_to(Path(args.db_root).resolve()) or output.exists():
        parser.error("Evaluation output must be fresh and outside dataset/input files")
    selections = {}
    for line in Path(args.selections).read_text(encoding="utf-8-sig").splitlines():
        row = json.loads(line)
        if row["case_id"] in selections:
            parser.error("Duplicate selection case_id")
        selections[row["case_id"]] = row
    expected = {c["case_id"] for c in cases}
    if set(selections) - expected:
        parser.error("Selections contain cases outside the manifest")
    snapshots, records, missing = {}, [], []
    for case in cases:
        ident, db_id = case["case_id"], case["db_id"]
        if ident not in selections:
            missing.append(ident)
            continue
        row = selections[ident]
        if row.get("db_id") != db_id:
            parser.error("Selection db_id differs from manifest")
        if db_id not in snapshots:
            snapshots[db_id] = read_schema(find_db(args.db_root, db_id))
        records.append({"case_id": ident, "db_id": db_id, "schema_linking": row.get("schema_linking", {}),
                        "schema_evaluation": evaluate_selection(case["SQL"], snapshots[db_id], row.get("schema_selection", {})),
                        "offline_mock": row.get("offline_mock", False)})
    report = {"dataset_sha256": file_sha256(args.cases), "manifest_sha256": file_sha256(args.manifest),
              "planned_cases": len(cases), "missing_selection_case_ids": missing,
              "all_selections_offline_mock": all(r["offline_mock"] for r in records) if records else None,
              "summary": summarize_schema(records), "records": records,
              "model_api_calls": 0, "gold_used_only_in_offline_evaluator": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    write_new_json(output, report)
    print(json.dumps({"evaluated": len(records), "missing": len(missing), "out": str(output), "model_api_calls": 0}))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
