"""Unlimited-cost Live policy with synthetic credentials and in-memory HTTP only."""
from copy import deepcopy
import json
import os
from types import SimpleNamespace as NS

import httpx
import pytest

from test_adaptive_validation import prepared, dump, make_auth, ledger_model
from bird_starter import adaptive_validation_budget as budgets
from bird_starter.adaptive_validation import NodeModel, run_validation
from bird_starter.adaptive_validation_budget import RECORD_ONLY, BudgetLedger, verify_live_authorization, usage_cost
from bird_starter.adaptive_validation_config import read_json
from bird_starter.adaptive_validation_connection import check_connection, credentials_ready, verify_connection_receipt
from bird_starter.adaptive_validation_reporting import aggregate
from bird_starter.model import Model, ModelFailure
from bird_starter.request_audit import RequestLog


def unlimited_auth(p):
    config, auth = make_auth(p)
    auth.update(cost_policy=RECORD_ONLY, max_usd=None, no_monetary_cap=True,
                max_attempts=39, catalog_allowance=1, max_total_http_reservations=40)
    return config, auth


def test_explicit_unlimited_policy_does_not_require_quotes_or_server_fee_caps(prepared):
    config, auth = unlimited_auth(prepared)
    config["pricing"] = deepcopy(prepared.config["pricing"])
    auth["accept_local_soft_cap_risk"] = False
    path = dump(prepared.tmp / "auth.json", auth)
    assert verify_live_authorization(path, config, prepared.frozen) == auth
    assert usage_cost({"prompt_tokens": 100, "completion_tokens": 20}, config["pricing"]) is None


@pytest.mark.parametrize("field,value", [("max_attempts", 40), ("catalog_allowance", 2),
    ("max_total_http_reservations", 41), ("no_monetary_cap", False), ("cost_policy", "unknown")])
def test_unlimited_cost_never_relaxes_combined_reservation_hard_cap(prepared, field, value):
    config, auth = unlimited_auth(prepared)
    auth[field] = value
    with pytest.raises(ValueError):
        verify_live_authorization(dump(prepared.tmp / "auth.json", auth), config, prepared.frozen)


def test_unlimited_usage_missing_is_recorded_not_priced_or_retried(tmp_path):
    batch = RequestLog(tmp_path / "attempts.jsonl", "fixture", "fixture-model", 2)
    ledger = BudgetLedger(batch, pricing={"status": "UNVERIFIED"}, record_only=True)
    ledger.context = ("B", 1, "linking")
    try:
        ledger.quote("synthetic", "synthetic", 2048)
        first = ledger.prepare(1)
        ledger.finish(first, "SUCCEEDED", http_status=200)
        assert ledger.stop_reason is None
        ledger.context = ("B", 1, "generation")
        ledger.quote("synthetic", "synthetic", 2048)
        second = ledger.prepare(1)
        ledger.finish(second, "SUCCEEDED", http_status=200, prompt_tokens=5, completion_tokens=3)
        assert batch.reserved == 2
        assert usage_cost({"prompt_tokens": 5, "completion_tokens": 3}, {"status": "UNVERIFIED"}) is None
        with pytest.raises(Exception):
            ledger.quote("synthetic", "synthetic", 2048); ledger.prepare(1)
        assert batch.reserved == 2
    finally:
        batch.close()


@pytest.mark.parametrize("http,status,category,continues", [(200,"FAILED","model_output_truncated",True),
    (200,"FAILED","empty_response",True), (None,"INDETERMINATE","model_timeout",False),
    (401,"FAILED","model_authentication_error",False)])
def test_completed_content_failure_is_distinct_from_unknown_replay(tmp_path,http,status,category,continues):
    batch = RequestLog(tmp_path / "attempts.jsonl", "fixture", "fixture-model", 2)
    ledger = BudgetLedger(batch, record_only=True)
    ledger.context = ("B",1,"linking")
    try:
        ledger.quote("fixture","fixture",2048)
        attempt=ledger.prepare(1)
        ledger.finish(attempt,status,http_status=http,error_category=category)
        assert (ledger.stop_reason is None) == continues
        assert batch.reserved==1
    finally:
        batch.close()


def test_completed_truncation_can_advance_to_distinct_node_without_retry(tmp_path):
    answers = iter([NS(status_code=200,model="fixture-model",usage=NS(prompt_tokens=4,completion_tokens=8),
                       choices=[NS(message=NS(content="incomplete"),finish_reason="length")]),
                    NS(status_code=200,model="fixture-model",usage=NS(prompt_tokens=4,completion_tokens=2),
                       choices=[NS(message=NS(content="SELECT 1"),finish_reason="stop")])])
    batch=RequestLog(tmp_path/'attempts.jsonl','fixture','fixture-model',2)
    ledger=BudgetLedger(batch,record_only=True)
    adapter=Model(model='fixture-model',max_retries=0,max_calls=2,attempt_log=ledger,
                  client=NS(chat=NS(completions=NS(create=lambda **_:next(answers)))))
    wrapper=NodeModel(adapter,ledger,{'model':'fixture-model','max_output_tokens':2048},'live')
    wrapper.group,wrapper.identity='B',1
    try:
        with pytest.raises(ModelFailure,match='model_output_truncated'):
            wrapper.ask('Select relevant SQLite','fixture')
        assert ledger.stop_reason is None
        assert wrapper.ask('Generate SQLite','fixture')=='SELECT 1'
        assert batch.reserved==2
        lines=[json.loads(s) for s in batch.path.read_text().splitlines()]
        assert [r['case_id'] for r in lines if r['event']=='ATTEMPT_PREPARED']==['B:1:linking','B:1:generation']
    finally:
        batch.close()


def test_safe_environment_check_never_returns_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','SYNTHETIC_CREDENTIAL_NEVER_REAL')
    monkeypatch.setenv('OPENAI_MODEL','fixture-model')
    monkeypatch.setenv('OPENAI_BASE_URL','https://fixture.invalid/v1')
    result=credentials_ready({'model':'fixture-model','base_url':'https://fixture.invalid/v1'})
    assert result['credential_present'] and 'SYNTHETIC_CREDENTIAL' not in json.dumps(result)
    with pytest.raises(ValueError):
        credentials_ready({'model':'different','base_url':'https://fixture.invalid/v1'})


@pytest.mark.parametrize('response_status,model_present,success',[(200,True,True),(200,False,False),(302,True,False),(401,True,False)])
def test_single_catalog_reservation_and_no_redirect_or_replay(prepared,monkeypatch,response_status,model_present,success):
    monkeypatch.setattr(budgets,'ROOT',prepared.tmp)
    monkeypatch.setenv('OPENAI_API_KEY','SYNTHETIC_CREDENTIAL_NEVER_REAL')
    monkeypatch.delenv('OPENAI_MODEL',raising=False)
    monkeypatch.delenv('OPENAI_BASE_URL',raising=False)
    config,auth=unlimited_auth(prepared)
    auth_path=dump(prepared.tmp/'auth.json',auth)
    calls=[]
    def handle(request):
        calls.append(request.url.path)
        log=prepared.tmp/'catalog/catalog.attempts.jsonl'
        assert json.loads(log.read_text().splitlines()[-1])['event']=='ATTEMPT_PREPARED'
        return httpx.Response(response_status,json={'data':[{'id':config['common']['model']}] if model_present else []},headers={'Location':'https://other.invalid/'})
    client=httpx.Client(transport=httpx.MockTransport(handle),follow_redirects=False)
    try:
        if success:
            result=check_connection(prepared.frozen,prepared.data,prepared.root,prepared.tmp/'catalog',auth_path,client=client)
            assert result['status']=='PASS' and result['catalog_reservations']==1
            assert verify_connection_receipt(auth,config)['status']=='PASS'
        else:
            with pytest.raises(ValueError):
                check_connection(prepared.frozen,prepared.data,prepared.root,prepared.tmp/'catalog',auth_path,client=client)
            with pytest.raises((ValueError,OSError)):
                verify_connection_receipt(auth,config)
        with pytest.raises(FileExistsError):
            check_connection(prepared.frozen,prepared.data,prepared.root,prepared.tmp/'catalog_again',auth_path,client=client)
        assert len(calls)==1
        assert 'SYNTHETIC_CREDENTIAL' not in (prepared.tmp/'catalog/connection.json').read_text()
    finally:
        client.close()


def test_live_cannot_start_without_successful_catalog_receipt(prepared,monkeypatch):
    monkeypatch.setattr(budgets,'ROOT',prepared.tmp)
    _,auth=unlimited_auth(prepared)
    path=dump(prepared.tmp/'auth.json',auth)
    monkeypatch.setattr('bird_starter.adaptive_validation.Model',lambda **_:pytest.fail('Network client constructed'))
    with pytest.raises((ValueError,OSError)):
        run_validation(prepared.frozen,prepared.data,prepared.root,prepared.tmp/'live',execution='live',allow_api=True,authorization=path)
