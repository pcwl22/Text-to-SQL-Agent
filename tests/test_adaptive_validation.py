"""P1B tests use synthetic SQLite/question fixtures, never benchmark responses."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace as NS
import uuid

import httpx
from openai import APITimeoutError
import pytest

from bird_starter import adaptive_validation_config as cfg
from bird_starter.adaptive_validation import isolated_runtime, run_validation
from bird_starter.adaptive_validation_budget import (BudgetLedger, amount, consume_authorization,
                                                     usage_cost, verify_live_authorization)
from bird_starter.adaptive_validation_reporting import aggregate
from bird_starter.experiment_sets import file_sha256
from bird_starter.model import Model, ModelFailure
from bird_starter.request_audit import AuditError, RequestLog, inspect_request_log
from bird_starter.schema_routing import AdaptiveSchemaGate, route_schema
from bird_starter.schema_linking import read_schema
from scripts.run_adaptive_live_validation import main
from bird_starter.ablation_mock import MockCompletions


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def prepared(tmp_path, tiny_db, monkeypatch):
    """Twenty synthetic questions on two tiny fixture databases, ten selected."""
    import bird_starter.experiment_review as review
    monkeypatch.setattr(review, "PRIVATE_OUTPUT_ROOT", tmp_path)
    large = tiny_db[0] / "large" / "large.sqlite"
    large.parent.mkdir()
    with sqlite3.connect(large) as conn:
        conn.execute('CREATE TABLE items (' + ','.join(f'column_{i:03d} INTEGER' for i in range(120)) + ')')
    data = dump(tmp_path / "synthetic_cases.json", [{"question_id": i, "db_id": "demo" if i < 10 else "large",
        "question": f"synthetic fixture {i}", "evidence": "", "difficulty": ("simple", "moderate", "challenging")[i % 3],
        "SQL": "SELECT 1 /* GOLD_P1B_FIXTURE_SENTINEL */", "official_ex": "GOLD_SCORE_FIXTURE_SENTINEL"} for i in range(20)])
    dev = dump(tmp_path / "synthetic_dev.json", {"dataset_sha256": file_sha256(data),
        "cases": [{"case_id": i, "db_id": "demo" if i == 0 else "large", "difficulty": "simple"} for i in (0, 10)]})
    config = cfg.read_json(cfg.ROOT / "configs/p1b/adaptive_live_v1.json")
    config.update(dataset_question_count=20, dataset_sha256=file_sha256(data), development_manifest=str(dev),
                  development_manifest_sha256=file_sha256(dev), database_sha256={"demo": file_sha256(tiny_db[1]), "large": file_sha256(large)})
    path = dump(tmp_path / "config.json", config)
    frozen = tmp_path / "frozen"
    manifest = cfg.prepare(path, data, tiny_db[0], frozen)
    return NS(config=config, path=path, frozen=frozen, data=data, root=tiny_db[0], manifest=manifest, tmp=tmp_path)


def run(p, name="run", **kwargs):
    return run_validation(p.frozen, p.data, p.root, p.tmp / name, **kwargs)


def test_freeze_deterministic_stratified_no_gold_or_development_overlap(prepared):
    p = prepared
    m = cfg.prepare(p.path, p.data, p.root, p.tmp / "frozen_second")
    assert m == p.manifest
    assert len(m["cases"]) == len({r["case_id"] for r in m["cases"]}) == 10
    assert not {r["case_id"] for r in m["cases"]} & {0, 10}
    assert m["available_strata"] == {"SKIP_LINKING": 9, "USE_LINKING": 9}
    assert sum(r["db_id"] == "demo" for r in m["cases"]) == 5
    assert "GOLD_" not in json.dumps(m)
    assert [r["case_id"] for r in m["schedule"] if r["group"] == "B"] == [r["case_id"] for r in m["cases"]]
    assert [r["case_id"] for r in m["schedule"] if r["group"] == "E"] == [r["case_id"] for r in m["cases"]]
    assert [r["group"] for r in m["schedule"][:4]] == ["B", "E", "E", "B"]


def test_insufficient_stratum_does_not_substitute_cases():
    records = {i: {"db_id": "tiny"} for i in range(4)}
    with pytest.raises(ValueError, match="Insufficient routing strata"):
        cfg.sample_metadata(records, set(), {"tiny": {"routing_decision": "SKIP_LINKING"}})


@pytest.mark.parametrize("key,value", [("threshold_bytes", 1015), ("seed", 1), ("max_attempts", 41),
                                      ("case_count", 11), ("max_attempts", True)])
def test_invalid_fixed_protocol(prepared, key, value):
    candidate = deepcopy(prepared.config)
    candidate[key] = value
    with pytest.raises(ValueError):
        cfg.validate_config(candidate)


@pytest.mark.parametrize("key,value", [("temperature", 1), ("n", 2), ("n", True), ("max_repairs", 1),
    ("request_retries", 1), ("sdk_retries", 1), ("http_redirects", True), ("max_output_tokens", 4096),
    ("concurrency", 2), ("sql_timeout_seconds", float("nan")), ("token_parameter", "other")])
def test_invalid_common_protocol(prepared, key, value):
    candidate = deepcopy(prepared.config)
    candidate["common"][key] = value
    with pytest.raises(ValueError):
        cfg.validate_config(candidate)


def test_dry_run_is_default_no_sdk_sql_or_network(prepared, monkeypatch):
    monkeypatch.setattr("bird_starter.adaptive_validation.Model", lambda **_: pytest.fail("SDK construction"))
    monkeypatch.setattr("bird_starter.schema_workflow.execute_select", lambda *_a, **_k: pytest.fail("SQL execution"))
    report = run(prepared)
    assert report["execution"] == "dry-run" and report["reserved_attempts"] == 0
    assert report["planned_requests"] == {"B": 20, "E": 15}
    assert report["planned_total_requests"] == 35 <= 40
    assert report["real_model_request_reservations"] == 0
    for g in "BE":
        assert report["groups"][g]["processed_cases"] == 10
        assert report["groups"][g]["official_ex"] is None
    assert report["groups"]["E"]["routing"] == {"SKIP_LINKING": 5, "USE_LINKING": 5}


def test_mock_paired_counts_audit_reconstruction_and_read_only(prepared):
    p = prepared
    before = {db: file_sha256(path) for db, path in (("demo", p.root / "demo/demo.sqlite"), ("large", p.root / "large/large.sqlite"))}
    report = run(p, execution="mock")
    assert report["reserved_attempts"] == 35 and report["real_model_request_reservations"] == 0
    assert report["groups"]["B"]["nodes"]["linking"]["reservations"] == 10
    assert report["groups"]["E"]["nodes"]["linking"]["reservations"] == 5
    assert all(report["groups"][g]["execution_successes"] == 10 for g in "BE")
    for g in "BE":
        rows = [json.loads(line) for line in (p.tmp / "run" / f"{g}.jsonl").read_text().splitlines()]
        assert [r["case_id"] for r in rows] == [r["case_id"] for r in p.manifest["cases"]]
        assert all(r["schema_observation"]["selected_schema"] for r in rows)
        assert all(r["model_calls"] <= 2 for r in rows)
        assert "GOLD_" not in json.dumps(rows)
    assert before == {db: file_sha256(p.root / db / (db + ".sqlite")) for db in before}
    assert report["audit"]["automatic_requests_available"] == 0
    assert report["groups"]["B"]["usage_source"] == "MOCK_OR_NO_USAGE_NOT_PROVIDER_MEASUREMENT"


def test_actual_sdk_parameters_and_prompts_are_paired_and_gold_free(prepared):
    calls = {g: [] for g in "BE"}
    class RecordingSDK(MockCompletions):
        def __init__(self, group):
            super().__init__("read_only_probe")
            self.group = group
        def create(self, **kwargs):
            calls[self.group].append(deepcopy(kwargs))
            return super().create(**kwargs)
    run(prepared, execution="mock", client_factory=RecordingSDK)
    for group in "BE":
        assert "GOLD_" not in json.dumps(calls[group])
        for params in calls[group]:
            assert params["temperature"] == 0 and params["n"] == 1
            assert params["model"] == prepared.config["common"]["model"]
            assert params["max_completion_tokens"] == 2048
    assert len(calls["B"]) == 20 and len(calls["E"]) == 15


@pytest.mark.parametrize("place", ["config", "common", "pricing"])
def test_config_rejects_accidental_credential_fields(prepared, place):
    config = deepcopy(prepared.config)
    (config if place == "config" else config[place])["api_key"] = "FAKE_FIXTURE_NEVER_REAL"
    with pytest.raises(ValueError):
        cfg.validate_config(config)


@pytest.mark.parametrize("cap", [0, 1, 3, 19, 34])
def test_cross_group_shared_cap_and_unrun_denominator(prepared, cap):
    report = run(prepared, execution="mock", max_attempts=cap)
    assert report["reserved_attempts"] == cap
    assert report["stopped_reason"] == "model_budget_exceeded"
    assert all(report["groups"][g]["planned_cases"] == 10 for g in "BE")


def test_output_and_freeze_exclusive_no_resume(prepared):
    run(prepared)
    with pytest.raises(FileExistsError):
        run(prepared)
    with pytest.raises(FileExistsError):
        cfg.prepare(prepared.path, prepared.data, prepared.root, prepared.frozen)


@pytest.mark.parametrize("target", ["manifest.json", "config.json", "freeze.json"])
def test_frozen_tampering_rejected_before_model(prepared, target):
    p = prepared.frozen / target
    p.write_text(p.read_text() + " ")
    if target == "freeze.json":
        data = cfg.read_json(p)
        data["config_sha256"] = "0" * 64
        dump(p, data)
    with pytest.raises(ValueError):
        run(prepared)


def test_modified_database_rejected_before_model(prepared):
    with sqlite3.connect(prepared.root / "demo/demo.sqlite") as conn:
        conn.execute("INSERT INTO orders(amount) VALUES (11)")
    with pytest.raises(ValueError, match="SQLite fingerprint"):
        run(prepared)


class ScriptedSDK:
    def __init__(self, callback):
        self.callback, self.context = callback, None
    def set_context(self, index, snapshot):
        self.context = snapshot
    def create(self, **kwargs):
        return self.callback(kwargs)


def test_timeout_stops_all_groups_without_replay(prepared):
    calls = []
    def timeout(params):
        calls.append(params)
        raise APITimeoutError(request=httpx.Request("POST", "https://fixture.invalid"))
    report = run(prepared, execution="mock", client_factory=lambda _: ScriptedSDK(timeout))
    assert len(calls) == report["reserved_attempts"] == 1
    assert report["audit"]["indeterminate_attempts"] == 1
    assert report["stopped_reason"] == "model_timeout"
    assert report["groups"]["B"]["planned_cases"] == report["groups"]["E"]["planned_cases"] == 10
    with pytest.raises(FileExistsError):
        run(prepared, execution="mock")


def test_sql_safety_stops_batch_and_preserves_database(prepared):
    calls = []
    def answer(params):
        calls.append(params)
        text = '{"tables": [{"name":"orders", "columns":["id"]}]}' if params["messages"][0]["content"].startswith("Select") else 'DROP TABLE orders'
        return NS(status_code=200, choices=[NS(finish_reason="stop", message=NS(content=text))])
    before = file_sha256(prepared.root / "demo/demo.sqlite")
    report = run(prepared, execution="mock", client_factory=lambda _: ScriptedSDK(answer))
    assert report["stopped_reason"] == "sql_safety_rejection"
    assert len(calls) == 2
    assert file_sha256(prepared.root / "demo/demo.sqlite") == before


def test_runtime_network_guard_and_dotenv_restore(monkeypatch):
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    old = socket.socket.connect
    with isolated_runtime(True):
        with pytest.raises(AssertionError, match="P1B offline"):
            socket.create_connection(("127.0.0.1", 1))
        assert __import__('os').environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert socket.socket.connect is old
    assert "PYTHON_DOTENV_DISABLED" not in __import__('os').environ


@pytest.mark.parametrize("options", [["--live"], ["--allow-api"], ["--mock-llm", "--allow-api"]])
def test_cli_rejects_accidental_live_flags(prepared, options):
    with pytest.raises(SystemExit) as exc:
        main(["--db-root", str(prepared.root), "--out-dir", str(prepared.tmp / "bad"), *options])
    assert exc.value.code == 2
    assert not (prepared.tmp / "bad").exists()


def test_live_without_authorization_never_constructs_sdk(prepared, monkeypatch):
    monkeypatch.setattr("bird_starter.adaptive_validation.Model", lambda **_: pytest.fail("Live client"))
    with pytest.raises(ValueError, match="Live requires separate"):
        run(prepared, execution="live", allow_api=True)


def prices():
    return {"input_per_million": "1", "cached_input_per_million": "0.5", "output_per_million": "2",
            "chat_overhead_token_bound": 1024}


@pytest.mark.parametrize("value", [None, True, -1, "NaN", "Infinity", "garbage"])
def test_invalid_money(value):
    with pytest.raises(ValueError):
        amount(value)


def test_exact_usage_cost_and_cache_coverage():
    event = {"prompt_tokens": 100, "completion_tokens": 10,
             "response_metadata": {"prompt_tokens_details": {"cached_tokens": 40}}}
    assert usage_cost(event, prices()) == Decimal("0.00010")
    event["response_metadata"] = {}
    assert usage_cost(event, prices()) is None
    assert usage_cost({"prompt_tokens": None, "completion_tokens": 0}, prices()) is None


def ledger_model(tmp_path, create, *, price=None, usd=None, cap=2, cost_log=False):
    batch = RequestLog(tmp_path / "attempts.jsonl", "synthetic", "fixture-model", cap)
    ledger = BudgetLedger(batch, pricing=price, max_usd=usd, cost_path=tmp_path / "cost.jsonl" if cost_log else None)
    ledger.context = ("B", 1, "linking")
    model = Model(model="fixture-model", max_retries=0, max_calls=cap, attempt_log=ledger,
                  client=NS(chat=NS(completions=NS(create=create))))
    model.set_case_id(1)
    return batch, ledger, model


def test_prepared_and_cost_fsync_precede_sdk(tmp_path, monkeypatch):
    from bird_starter import request_audit
    original, syncs = request_audit.os.fsync, []
    def fsync(fd):
        syncs.append(fd)
        original(fd)
    monkeypatch.setattr(request_audit.os, "fsync", fsync)
    def create(**_):
        events = [json.loads(s) for s in (tmp_path / "attempts.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "ATTEMPT_PREPARED"
        assert json.loads((tmp_path / "cost.jsonl").read_text().splitlines()[-1])["event"] == "COST_COMMITTED_BEFORE_HTTP"
        assert len(syncs) >= 4
        return NS(status_code=200, choices=[NS(message=NS(content="SELECT 1"), finish_reason="stop")])
    batch, ledger, model = ledger_model(tmp_path, create, cost_log=True)
    try:
        ledger.quote("fixture", "fixture", 2048)
        assert model.ask("fixture", "fixture") == "SELECT 1"
    finally:
        batch.close(); ledger.close()


def test_failed_reservation_fsync_means_no_sdk_call(tmp_path, monkeypatch):
    from bird_starter import request_audit
    batch, ledger, model = ledger_model(tmp_path, lambda **_: pytest.fail("SDK called"))
    monkeypatch.setattr(request_audit.os, "fsync", lambda _: (_ for _ in ()).throw(OSError("fixture fsync")))
    try:
        ledger.quote("fixture", "fixture", 2048)
        with pytest.raises(ModelFailure, match="model_audit_error"):
            model.ask("fixture", "fixture")
        assert batch.reserved == 1 and batch.poisoned
        assert ledger.stop_reason == "model_audit_error"
    finally:
        batch.close()


def test_missing_usage_holds_cost_and_stops_next_request(tmp_path):
    batch, ledger, model = ledger_model(tmp_path, lambda **_: NS(status_code=200,
        choices=[NS(message=NS(content="SELECT 1"), finish_reason="stop")]), price=prices(), usd=1)
    try:
        ledger.quote("fixture", "fixture", 2048)
        model.ask("fixture", "fixture")
        assert ledger.stop_reason == "model_usage_or_billing_unknown"
        assert ledger.committed > 0
        with pytest.raises(ModelFailure):
            ledger.quote("fixture", "fixture", 2048)
        assert batch.reserved == 1
    finally:
        batch.close()


def test_usd_soft_cap_rejects_before_any_reservation(tmp_path):
    batch, ledger, model = ledger_model(tmp_path, lambda **_: pytest.fail("SDK called"), price=prices(), usd="0.0000001")
    try:
        with pytest.raises(ModelFailure, match="cost_budget"):
            ledger.quote("fixture", "fixture", 2048)
        assert batch.reserved == 0
    finally:
        batch.close()


def make_auth(prepared):
    config = cfg.read_json(prepared.frozen / "config.json")
    config["pricing"].update(prices(), status="VERIFIED", billing_mode="per_token", additional_charges=False,
        usage_supported=True, byte_token_bound_verified=True, source_url="https://pricing.fixture.invalid/",
        bound_source_url="https://tokenizer.fixture.invalid/", verified_at=datetime.now(timezone.utc).isoformat())
    auth = {"allow_live": True, "one_batch_only": True, "model": config["common"]["model"],
        "provider": config["common"]["provider"], "base_url": config["common"]["base_url"],
        "config_sha256": file_sha256(prepared.frozen / "config.json"), "manifest_sha256": config["manifest_sha256"],
        "authorization_id": uuid.uuid4().hex, "authorized_by": "synthetic-test-fixture", "max_attempts": 40,
        "max_usd": "1", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "server_hard_cap_verified": False, "accept_local_soft_cap_risk": True}
    return config, auth


@pytest.mark.parametrize("damage", ["model", "provider", "amount", "request_cap", "risk", "expiry", "key_field", "pricing", "usage", "stale_price"])
def test_authorization_fail_closed(prepared, damage):
    config, auth = make_auth(prepared)
    if damage in ("model", "provider"): auth[damage] = "different"
    elif damage == "amount": auth["max_usd"] = None
    elif damage == "request_cap": auth["max_attempts"] = 41
    elif damage == "risk": auth["accept_local_soft_cap_risk"] = False
    elif damage == "expiry": auth["expires_at"] = "2020-01-01T00:00:00+00:00"
    elif damage == "key_field": auth["api_key"] = "FAKE_FIXTURE_NEVER_REAL"
    elif damage == "pricing": config["pricing"]["status"] = "UNVERIFIED"
    elif damage == "usage": config["pricing"]["usage_supported"] = False
    else: config["pricing"]["verified_at"] = "2020-01-01T00:00:00+00:00"
    path = dump(prepared.tmp / "auth.json", auth)
    with pytest.raises(ValueError):
        verify_live_authorization(path, config, prepared.frozen)


def test_single_use_authorization_cannot_move_to_new_output(prepared, monkeypatch):
    import bird_starter.adaptive_validation_budget as budgets
    monkeypatch.setattr(budgets, "ROOT", prepared.tmp)
    config, auth = make_auth(prepared)
    path = dump(prepared.tmp / "auth.json", auth)
    assert verify_live_authorization(path, config, prepared.frozen) == auth
    consume_authorization(auth, "synthetic-run")
    with pytest.raises(FileExistsError):
        consume_authorization(auth, "synthetic-run-in-new-output")


def test_missing_usage_does_not_masquerade_as_group_total():
    rows = {g: [{"processed": False, "model_calls": 0, "node_metrics": {}, "total_latency_ms": None} for _ in range(10)] for g in "BE"}
    events = [{"case_id": "B:1:linking", "prompt_tokens": 100, "completion_tokens": 10, "result_status": "SUCCEEDED", "http_status": 200},
              {"case_id": "B:1:generation", "prompt_tokens": None, "completion_tokens": None, "result_status": "FAILED", "http_status": None}]
    report = aggregate(rows, events, "live")
    assert report["B"]["total_tokens"] is None and report["B"]["prompt_tokens"] is None
    assert report["B"]["known_prompt_tokens_subtotal"] == 100
    assert report["B"]["usage_missing_calls"] == 1 and report["B"]["transmission_count"] == "UNKNOWN"
    assert report["B"]["billed_cost_usd"] == "UNKNOWN"


def test_explicit_user_no_cost_cap_does_not_remove_request_or_billing_checks(prepared):
    config, auth = make_auth(prepared)
    auth.update(max_usd=None, no_monetary_cap=True)
    path = dump(prepared.tmp / "auth.json", auth)
    assert verify_live_authorization(path, config, prepared.frozen)["max_attempts"] == 40
    config["pricing"]["status"] = "UNVERIFIED"
    with pytest.raises(ValueError):
        verify_live_authorization(path, config, prepared.frozen)


def test_subscription_needs_account_proof_and_has_no_token_cost_allocation(prepared):
    config, auth = make_auth(prepared)
    p = config["pricing"]
    p.update(billing_mode="included_subscription", input_per_million=None, output_per_million=None,
             cached_input_per_million=None, account_plan_evidence="synthetic-fixture-only",
             included_model_verified=True, no_incremental_inference_charge_verified=True)
    path = dump(prepared.tmp / "auth.json", auth)
    assert verify_live_authorization(path, config, prepared.frozen) == auth
    assert usage_cost({"prompt_tokens": 10, "completion_tokens": 5}, p) is None
    p["account_plan_evidence"] = None
    with pytest.raises(ValueError):
        verify_live_authorization(path, config, prepared.frozen)
