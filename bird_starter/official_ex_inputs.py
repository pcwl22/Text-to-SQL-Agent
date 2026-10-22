"""Evaluator-only input preparation, NOT an official EX scoring implementation."""
import importlib.util
import json
import os
from pathlib import Path

import sqlglot

from .data import load_cases
from .experiment_sets import file_sha256
from .experiment_review import read_group, reserve_private_output
from .request_audit import write_new_json
from .tools import check_read_only_sql

OFFICIAL_COMMIT = "abd11b6db92a1c9f809b32f7564c7c71b34d67f0"
OFFICIAL_FILES = {"evaluation/evaluation_ex.py": "45cc3bb8cb6edf039e6a8a2897b7b5f06aa23970",
                  "evaluation/evaluation_utils.py": "1c2c9bea73f7c77429848d0c289f4ca19b906e36"}
DELIMITER = "\t----- bird -----\t"
FAILURE_SQL = "__BIRD_PREDICTION_UNAVAILABLE__"
PROTOCOL = "complete-agent-results-only-failed-zero-v1"


def single_line_sql(sql):
    """Keep token lexemes, remove only gaps/comments, verify unchanged parsed AST.

    Literal/identifier line breaks cannot be represented by the official GT
    format and block export, rather than silently rewriting their contents.
    """
    validated = check_read_only_sql(sql)
    tokens = sqlglot.tokenize(validated, read="sqlite")
    pieces = []
    for token in tokens:
        piece = validated[token.start:token.end + 1]
        if any(c in piece for c in "\r\n\t"):
            if token.token_type.name == "IDENTIFIER" or token.token_type.name.endswith("STRING"):
                raise ValueError("SQL has an unrepresentable literal/identifier; reconciliation required")
            piece = " ".join(piece.split())
        pieces.append(piece)
    result = " ".join(pieces)
    original = sqlglot.parse_one(validated, read="sqlite")
    normalized = sqlglot.parse_one(result, read="sqlite")
    for expression in (original, normalized):
        for node in expression.walk():
            node.comments = None
    if original != normalized:
        raise ValueError("Single-line normalization changed SQL structure")
    return result


def engine_readiness():
    missing = [p for p in ("func_timeout", "pymysql", "psycopg2") if importlib.util.find_spec(p) is None]
    from . import config as runtime_config
    evidence_path = runtime_config.PROJECT_ROOT / 'outputs/official_ex_windows_checks/validation.json'
    validated = False
    if not missing and evidence_path.is_file():
        try:
            from .official_ex_windows import verify_source, DEFAULT_SOURCE, SOURCE_SHA256
            verify_source(DEFAULT_SOURCE)
            evidence = json.loads(evidence_path.read_text(encoding='utf-8'))
            validated = (evidence['acceptance'] == 'PASS' and evidence['official_commit'] == OFFICIAL_COMMIT
                         and evidence['source_sha256'] == SOURCE_SHA256 and evidence['originals_unchanged'] is True
                         and evidence['remote_model_requests'] == 0
                         and all(file_sha256(runtime_config.PROJECT_ROOT / p) == h
                                 for p, h in evidence['verified_files'].items()))
        except (KeyError, ValueError, OSError, ImportError):
            validated = False
    if validated:
        return {'status': 'PASS_WINDOWS_SANDBOX_CONTROLS', 'missing_upstream_imports': [], 'blockers': [],
                'official_ex': None, 'scoring_logic_modified': False, 'commit': OFFICIAL_COMMIT,
                'repository': 'https://github.com/bird-bench/mini_dev', 'files': OFFICIAL_FILES,
                'validation_record': str(evidence_path),
                'notice': 'Evaluator controls passed; no model EX score is implied.'}
    return {"status": "BLOCKED_END_TO_END_UNVERIFIED", "missing_upstream_imports": missing,
            "blockers": ["Upstream Windows timeout/multiprocessing path has not been run end to end",
                         "Upstream SQLite connection is read/write; read-only execution integration is unverified"],
            "official_ex": None, "scoring_logic_modified": False,
            "repository": "https://github.com/bird-bench/mini_dev", "commit": OFFICIAL_COMMIT, "files": OFFICIAL_FILES}


def export_inputs(run_dir, group, suite_path, cases_path, out_dir):
    context = read_group(run_dir, group, suite_path)
    config = context["config"]
    if context["batch"]["execution"] == "dry-run":
        raise ValueError("Dry-run results are not predictions for official evaluation")
    if file_sha256(cases_path) != config["dataset_sha256"]:
        raise ValueError("Gold dataset SHA256 mismatch")
    all_cases = {c["case_id"]: c for c in load_cases(cases_path)}
    predictions, gold_lines, difficulty, alignment = {}, [], [], []
    for index, row in enumerate(context["rows"]):
        case = all_cases[row["case_id"]]
        if case["db_id"] != row["db_id"] or case.get("difficulty") != row["difficulty"]:
            raise ValueError("Gold case/database/difficulty does not match predictions")
        gold = single_line_sql(case["SQL"])
        reason = None
        if not row["processed"]:
            reason = "missing_or_not_run"
        elif row["error_category"] or not row["generation_success"] or not row["final_execution_ok"]:
            reason = "agent_failure_including_timeout"
        elif row.get("output_truncated") is not False:
            reason = "truncated_or_unknown_completeness"
        candidate = FAILURE_SQL
        if reason is None:
            try:
                candidate = single_line_sql(row["final_sql"])
            except (ValueError, sqlglot.errors.SqlglotError):
                reason = "unsafe_or_unrepresentable_prediction"
        predictions[str(index)] = candidate + DELIMITER + case["db_id"]
        gold_lines.append(gold + "\t" + case["db_id"])
        difficulty.append({"case_id": case["case_id"], "db_id": case["db_id"], "difficulty": case["difficulty"]})
        alignment.append({"index": index, "case_id": case["case_id"], "db_id": case["db_id"],
                          "forced_failure": reason is not None, "reason": reason})
    output = reserve_private_output(out_dir)
    write_new_json(output / "predictions.json", predictions)
    for name, lines in (("gold.sql", gold_lines), ("difficulty.jsonl", [json.dumps(r, ensure_ascii=False) for r in difficulty])):
        with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    bundle = {"protocol": PROTOCOL, "run_id": context["batch"]["run_id"], "group": group,
              "planned_cases": len(alignment), "measurement_kind": context["rows"][0]["measurement_kind"],
              "dataset_version": config["dataset_version"], "dataset_sha256": config["dataset_sha256"],
              "manifest_sha256": config["manifest_sha256"], "source_result_sha256": context["source_result_sha256"],
              "request_evidence": context["request_evidence"],
              "alignment": alignment, "official_engine": engine_readiness(), "official_ex": None,
              "files_sha256": {p.name: file_sha256(p) for p in output.iterdir()},
              "notice": "Offline evaluator Gold export only; static format compatibility is not an official EX score."}
    write_new_json(output / "bundle.json", bundle)
    validate_bundle(output)
    return bundle


def validate_bundle(folder):
    folder = Path(folder)
    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    if (bundle["protocol"] != PROTOCOL or set(bundle["files_sha256"]) != {"predictions.json", "gold.sql", "difficulty.jsonl"}
            or len({r["case_id"] for r in bundle["alignment"]}) != len(bundle["alignment"])):
        raise ValueError("Unsupported protocol or incomplete alignment/integrity metadata")
    for name, digest in bundle["files_sha256"].items():
        if name not in {"predictions.json", "gold.sql", "difficulty.jsonl"} or file_sha256(folder / name) != digest:
            raise ValueError("Input bundle was modified")
    predictions = json.loads((folder / "predictions.json").read_text(encoding="utf-8"))
    gold = (folder / "gold.sql").read_text(encoding="utf-8").splitlines()
    difficulty = [json.loads(line) for line in (folder / "difficulty.jsonl").read_text(encoding="utf-8").splitlines()]
    n = bundle["planned_cases"]
    if (list(predictions) != [str(i) for i in range(n)] or len(gold) != n or len(difficulty) != n
            or len(bundle["alignment"]) != n):
        raise ValueError("Official inputs have different lengths/order; zip truncation prohibited")
    for index, item in enumerate(bundle["alignment"]):
        predicted, db = predictions[str(index)].split(DELIMITER)
        actual_gold, gold_db = gold[index].split("\t")
        meta = difficulty[index]
        if (item["index"] != index or db != gold_db or db != item["db_id"] or meta["db_id"] != db
                or meta["case_id"] != item["case_id"]):
            raise ValueError("Official input case/database alignment mismatch")
        check_read_only_sql(actual_gold)
        if item["forced_failure"]:
            if predicted != FAILURE_SQL:
                raise ValueError("Forced failures must retain the invalid, non-executable placeholder")
        else:
            check_read_only_sql(predicted)
    return {"aligned_cases": n, "official_ex": None, "format_compatible": True}
