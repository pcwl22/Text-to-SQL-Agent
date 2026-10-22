"""Cost estimator tests use explicit synthetic records; no benchmark/model run."""
import json

import pytest

from bird_starter.schema_cost_analysis import estimate_prompt, project_inputs, threshold_basis, selection_overhead
from bird_starter.schema_linking import read_schema, validate_selection
from scripts.analyze_schema_cost import main


def test_projection_discards_evaluation_and_unknown_fields(tmp_path):
    path = tmp_path / "fixture_cases.json"
    path.write_text(json.dumps([{"question_id": 0, "db_id": "demo", "question": "fixture count",
        "evidence": "fixture clue", "SQL": "GOLD_COST_FIXTURE_SENTINEL", "official_ex": 1,
        "schema_routing": {"decision": "untrusted"}}]))
    projected = project_inputs(path)
    assert set(projected[0]) == {"question_id", "db_id", "question", "evidence"}
    assert "GOLD_COST_FIXTURE_SENTINEL" not in json.dumps(projected)


@pytest.mark.parametrize("damage", ["duplicate", "wrong_db", "bad_id", "question_missing", "conflict"])
def test_projection_rejects_invalid_identities(tmp_path, damage):
    row = {"question_id": 1, "db_id": "demo", "question": "fixture", "evidence": ""}
    if damage == "wrong_db": row["db_id"] = "../escape"
    elif damage == "bad_id": row["question_id"] = True
    elif damage == "question_missing": del row["question"]
    elif damage == "conflict": row["case_id"] = 2
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([row, row] if damage == "duplicate" else [row]))
    with pytest.raises(ValueError):
        project_inputs(path)


def test_threshold_uses_unique_schema_sizes_and_no_accuracy_information():
    rows = [{"full_schema_size": b, "official_ex": score} for b, score in [(975, 1), (1014, 0), (2214, 1), (7260, 0), (5380, 1)]]
    # This reduced fixture has another gap: the rule uses actual distribution,
    # never selects a hand-picked threshold from correctness or database names.
    first = threshold_basis(rows)
    for row in rows:
        row["official_ex"] = 1 - row["official_ex"]
        row["db_id"] = "irrelevant"
    assert threshold_basis(rows + [rows[0]] * 20) == first
    assert threshold_basis([{"full_schema_size": 10}]) is None


def test_actual_distribution_rule_has_inclusive_lower_endpoint():
    rows = [{"full_schema_size": n} for n in (975, 1014, 2214, 2255, 2940, 3004, 3937, 4538, 4752, 5380, 7260)]
    assert threshold_basis(rows)["max_full_schema_bytes"] == 1014
    assert threshold_basis(rows)["next_schema_bytes"] == 2214


def test_estimates_are_content_byte_heuristics_not_usage():
    assert estimate_prompt(("表", "列")) == 2  # six UTF-8 bytes, no chat envelope
    assert estimate_prompt(("", "")) == 0


def test_output_overhead_is_explicitly_canonical_not_raw(tiny_db):
    selection = validate_selection('{"tables":[{"name":"orders","columns":["amount"]}]}', read_schema(tiny_db[1]))
    report = selection_overhead(selection)
    assert report["canonical_contract_bytes"] > report["canonical_identifier_bytes"] > 0
    assert report["canonical_structure_bytes"] > 0
    assert report["original_model_response_bytes"] is None


@pytest.mark.parametrize("flag", ["--live", "--allow-api"])
def test_analysis_cli_has_no_live_escape(flag, tmp_path):
    with pytest.raises(SystemExit):
        main(["--db-root", str(tmp_path), "--out-dir", str(tmp_path / "out"), flag])


def test_cli_blocks_network_and_restores_socket(tmp_path, monkeypatch, capsys):
    import socket
    import scripts.analyze_schema_cost as cli
    connect = socket.socket.connect
    def only_offline(*_args):
        with pytest.raises(AssertionError, match="Offline-only"):
            socket.socket().connect(("127.0.0.1", 1))
        return {"planned_cases": 1, "routing_counts": {"SKIP_LINKING": 1}}
    monkeypatch.setattr(cli, "analyze_costs", only_offline)
    assert main(["--db-root", str(tmp_path), "--out-dir", str(tmp_path / "out")]) == 0
    assert socket.socket.connect is connect
    assert "Actual EX=NOT EVALUATED" in capsys.readouterr().out
