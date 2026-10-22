"""Schema evidence checks use synthetic SQLite fixtures, never benchmark data."""
import json

import pytest

from bird_starter.baseline import run_baseline
from bird_starter.data import AgentInput
from bird_starter.error_analysis import observation_status
from bird_starter.observability import observe_schema, text_sha256
from bird_starter.schema_linking import read_schema
from bird_starter.schema_workflow import build_schema_graph, schema_graph_record


class Scripted:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.prompts = []

    def ask(self, system, user):
        self.prompts.append((system, user))
        return next(self.answers)


def test_actual_projected_schema_survives_graph_record(tiny_db):
    model = Scripted('{"tables":[{"name":"orders","columns":["amount"]}]}',
                     "SELECT SUM(amount) FROM orders")
    state = AgentInput("fixture amount", "fixture clue", str(tiny_db[1]), "schema_only").to_state()
    state.update(SQL="GOLD_ONLY_OBSERVATION_FIXTURE", schema_observation={"bad": "UNTRUSTED_INPUT"})
    output = build_schema_graph(model).invoke(state)
    record = schema_graph_record(output)
    evidence = record["schema_observation"]
    assert evidence["selection"]["tables"] == {"orders": ["amount", "id"]}
    assert evidence["selection"]["requested_tables"] == {"orders": ["amount"]}
    assert evidence["selected_schema"] == output["chosen_schema"]
    assert evidence["selected_schema"] in model.prompts[1][1]
    assert evidence["selected_schema_sha256"] == text_sha256(evidence["selected_schema"])
    assert "unrelated" not in evidence["selected_schema"]
    assert "GOLD_ONLY_OBSERVATION_FIXTURE" not in json.dumps(record) + json.dumps(model.prompts)
    assert "UNTRUSTED_INPUT" not in json.dumps(record)
    assert "snapshot" not in record and "question" not in record
    assert record["node_metrics"]["generation"]["model_calls"] == 1


def test_invalid_link_records_full_fallback_without_raw_response(tiny_db):
    invalid = "arbitrary response sk-fixture-secret123"
    model = Scripted(invalid, "SELECT 1")
    output = build_schema_graph(model).invoke(AgentInput("fixture", "", str(tiny_db[1]), "schema_only").to_state())
    evidence = schema_graph_record(output)["schema_observation"]
    assert evidence["selected_schema"] == read_schema(tiny_db[1])["full_schema"]
    assert evidence["fallback_reason"] == output["schema_linking"]["fallback_reason"]
    assert output["schema_linking"]["fallback"] is True
    assert set(evidence["selection"]["tables"]) == {"orders", "unrelated"}
    assert invalid not in json.dumps(evidence)


def test_baseline_schema_is_the_actual_generator_input_and_calls_counted(tiny_db):
    model = Scripted("SELECT SUM(amount) FROM orders")
    result = run_baseline(AgentInput("fixture", "", str(tiny_db[1]), "baseline"), model)
    evidence = result["schema_observation"]
    assert evidence["selected_schema"] in model.prompts[0][1]
    assert evidence["persistence_status"] == "EXACT"
    assert evidence["fallback_reason"] == "disabled"
    assert result["node_metrics"]["generation"]["model_calls"] == 1
    assert result["node_metrics"]["generation"]["prompt_tokens"] is None
    assert result["node_metrics"]["execution"]["tool_calls"] == 1


def test_redaction_never_claims_exact_reconstruction(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key-for-redaction")
    schema = "CREATE TABLE t(x TEXT DEFAULT 'fixture-key-for-redaction');"
    evidence = observe_schema(schema, {"tables": {"t": ["x"]}})
    assert evidence["persistence_status"] == "REDACTED"
    assert "fixture-key-for-redaction" not in json.dumps(evidence)
    assert evidence["selected_schema_sha256"] != evidence["persisted_schema_sha256"]
    assert observation_status({"schema_observation": evidence, "selected_tables": ["t"]}) == "REDACTED_NOT_RECONSTRUCTABLE"


@pytest.mark.parametrize("change", ["schema", "status", "tables", "hash", "metric"])
def test_inconsistent_schema_evidence_rejected(change):
    evidence = observe_schema("CREATE TABLE t(x);", {"tables": {"t": ["x"]}})
    row = {"schema_observation": evidence, "selected_tables": ["t"]}
    if change == "schema": evidence["selected_schema"] += "changed"
    elif change == "status": evidence["persistence_status"] = "REDACTED"
    elif change == "tables": row["selected_tables"] = ["other"]
    elif change == "metric": row["schema_linking"] = {"selected_schema_sha256": "wrong"}
    else: evidence["selected_schema_sha256"] = "wrong"
    with pytest.raises(ValueError):
        observation_status(row)


def test_legacy_hash_does_not_reconstruct_selection():
    assert observation_status({"schema_linking": {"selected_schema_sha256": "anything"}}) == "UNKNOWN_NOT_RECORDED"
