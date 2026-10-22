"""Synthetic SQLite fixtures only. No paid model or benchmark score in these tests."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from bird_starter import repair_diagnostic as d
from bird_starter.experiment_sets import file_sha256
from bird_starter.request_audit import inspect_request_log
from bird_starter.schema_linking import read_schema

PROJECT=Path(__file__).resolve().parents[1]


@pytest.fixture
def diagnostic_fixture(tmp_path,monkeypatch):
    """Eight tiny fixture DBs, not copies or replacements of real BIRD data."""
    base=json.loads((PROJECT/'configs/phase3c/repair_diagnostic_v1.json').read_text(encoding='utf-8'))
    plan={**base,'purpose':'SYNTHETIC_UNIT_TEST_FIXTURES_NOT_BIRD','source_profiles':{},
          'schemas':{},'database_sha256':{},'positive_cases':[],'semantic_assertions':{}}
    root=tmp_path/'db'
    for i,old in enumerate(base['positive_cases']):
        db=f'unit_{i}';folder=root/db;folder.mkdir(parents=True)
        path=folder/(db+'.sqlite')
        with closing(sqlite3.connect(path)) as c:
            c.executescript('CREATE TABLE items(id INTEGER PRIMARY KEY); CREATE TABLE labels(id INTEGER PRIMARY KEY);'
                            'INSERT INTO items VALUES(1),(2),(3); INSERT INTO labels VALUES(1),(2),(3);')
        full=read_schema(path)['full_schema']
        plan['schemas'][db]={'ddl':full,'sha256':__import__('hashlib').sha256(full.encode()).hexdigest()}
        plan['database_sha256'][db]=file_sha256(path)
        bad=('SELECT COUNT(*) FROM items WHERE (','SELECT COUNT(*) FROM labels WHERE',
             'SELECT COUNT(absent_column) FROM items','SELECT COUNT(DISTINCT absent_column) FROM labels',
             'SELECT COUNT(*) FROM absent_items','SELECT COUNT(*) FROM absent_labels',
             'SELECT COUNT(DISTINCT id) FROM items AS a JOIN labels AS b ON a.id=b.id',
             'SELECT COUNT(DISTINCT id) FROM items AS a JOIN labels AS b ON a.id=b.id')[i]
        cid=old['case_id'];table='labels' if i in (1,3,5) else 'items'
        reference='SELECT COUNT(DISTINCT a.id) FROM items AS a JOIN labels AS b ON a.id=b.id' if i>=6 else f'SELECT COUNT(*) FROM {table}'
        plan['positive_cases'].append({'case_id':cid,'db_id':db,'question':'Fixture row count only.',
            'evidence':'Synthetic test fixture, count rows.','initial_sql':bad,'expected_error_category':old['expected_error_category']})
        plan['semantic_assertions'][cid]={'reference_sql':reference,'required_tables':['items','labels'] if i>=6 else [table],
            'requires_count':True,'requires_distinct':i>=6,'expected_columns':1,'expected_rows':1}
    plan['negative_cases']=[{**c,'db_id':'unit_0','initial_sql':c['initial_sql'].replace('schools','items')} for c in base['negative_cases']]
    config=tmp_path/'plan.json';config.write_text(json.dumps(plan),encoding='utf-8')
    monkeypatch.setattr(d,'ROOT',tmp_path)
    monkeypatch.setattr(d,'historical_hashes',lambda:{})
    return config,root,tmp_path/'outputs',plan


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def lines(path): return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines()]


def test_all_eight_errors_have_real_sqlite_evidence_and_negative_route(diagnostic_fixture):
    config,root,_,_=diagnostic_fixture
    plan=d.load_plan(config)
    inputs,errors,_,neg,_,_,_=d.prepare(plan,root)
    assert len(inputs)==8 and len(neg)==4
    assert all(e['sqlite_errorcode']==sqlite3.SQLITE_ERROR for e in errors.values())
    assert {e['category'] for e in errors.values()}=={'syntax','missing_table','missing_column','ambiguous_column'}
    assert all(n['repair_rejected'] and n['model_calls']==0 for n in neg)
    assert next(n for n in neg if n['kind']=='timeout')['sqlite_errorcode']==sqlite3.SQLITE_INTERRUPT
    assert next(n for n in neg if n['kind']=='permission_denied')['sqlite_errorcode']==sqlite3.SQLITE_AUTH


@pytest.mark.parametrize('scenario,execution,semantic,valid,safe',[
    ('correct',8,8,8,8),('still_wrong',0,0,8,8),('dangerous',0,0,8,0),
    ('semantic_wrong',8,0,8,8),('empty',0,0,0,0),('length',0,0,0,0)])
def test_single_repair_no_retry_with_separate_response_execution_semantics(diagnostic_fixture,scenario,execution,semantic,valid,safe):
    config,root,out,_=diagnostic_fixture
    summary=d.run_diagnostic(config,root,out/scenario,execution='mock',mock_scenario=scenario)
    assert summary['model_calls']==8 and summary['execution_successes']==execution
    assert summary['semantic_assertion_passes']==semantic and summary['valid_repair_responses']==valid
    assert summary['safe_sql_count']==safe and summary['official_ex'] is None
    assert summary['negative_rejection_rate']==1
    rows=lines(out/scenario/'results.jsonl')
    assert all(r['model_calls']==1 and r['repair_attempts']==1 and r['generator_calls']==r['schema_linking_calls']==0 for r in rows)
    assert len(lines(out/scenario/'requests.attempts.jsonl'))==17
    assert read(out/scenario/'integrity.json')['sqlite_unchanged']


def test_exact_formal_prompt_and_evaluator_fields_never_sent(diagnostic_fixture):
    config,root,out,plan=diagnostic_fixture
    plan['positive_cases'][0]['SQL']='GOLD_FIELD_MUST_NOT_ESCAPE'
    config.write_text(json.dumps(plan),encoding='utf-8')
    class Spy(d.DiagnosticMock):
        def create(self,**kw):
            case=next(c for c in self.plan['positive_cases'] if c['case_id']==self.case_id)
            system,user=d.repair_prompt(case['question'],case['evidence'],self.plan['schemas'][case['db_id']]['ddl'],
                                       case['initial_sql'],case['expected_error_category'])
            assert kw['messages']==[{'role':'system','content':system},{'role':'user','content':user}]
            assert 'GOLD_FIELD_MUST_NOT_ESCAPE' not in json.dumps(kw)
            assert 'semantic_assertions' not in user and 'reference_sql' not in user
            assert kw['max_completion_tokens']==2048 and kw['temperature']==0
            assert lines(out/'spy'/'requests.attempts.jsonl')[-1]['event']=='ATTEMPT_PREPARED'
            return super().create(**kw)
    summary=d.run_diagnostic(config,root,out/'spy',execution='mock',client_factory=Spy)
    assert summary['semantic_assertion_passes']==8


@pytest.mark.parametrize('cap',[0,2])
def test_budget_exhaustion_stops_without_refund_or_second_attempt(diagnostic_fixture,cap):
    config,root,out,_=diagnostic_fixture
    s=d.run_diagnostic(config,root,out/'cap',execution='mock',max_calls=cap)
    assert s['model_calls']==cap and not s['complete'] and s['stop_reason']=='model_budget_exceeded'
    assert s['request_audit']['prepared_attempts']==cap
    assert s['request_audit']['automatic_resume_allowed'] is False


@pytest.mark.parametrize('failure',['write','flush','fsync'])
def test_audit_failure_prevents_sdk_and_fails_closed(diagnostic_fixture,monkeypatch,failure):
    config,root,out,_=diagnostic_fixture
    real_prepare=d.RequestLog.prepare
    calls=[]
    class Spy(d.DiagnosticMock):
        def create(self,**kw): calls.append(1);return super().create(**kw)
    def prepare(log,case_id):
        if failure=='fsync':
            real=os.fsync;target=log.stream.fileno()
            def fail(fd):
                if fd==target and not log.stream.closed: raise OSError('fixture audit fsync failure')
                return real(fd)
            monkeypatch.setattr(os,'fsync',fail)
        else:
            stream=log.stream
            class Proxy:
                @property
                def closed(self):return stream.closed
                def write(self,value):
                    if failure=='write':raise OSError('fixture audit write failure')
                    return stream.write(value)
                def flush(self):
                    if failure=='flush':raise OSError('fixture audit flush failure')
                    return stream.flush()
                def fileno(self):return stream.fileno()
                def close(self):return stream.close()
            log.stream=Proxy()
        return real_prepare(log,case_id)
    monkeypatch.setattr(d.RequestLog,'prepare',prepare)
    s=d.run_diagnostic(config,root,out/'audit',execution='mock',client_factory=Spy)
    assert not calls and s['model_calls']==0 and s['stop_reason']=='model_audit_error'


def test_dry_run_constructs_no_model_or_request_reservation(diagnostic_fixture,monkeypatch):
    config,root,out,_=diagnostic_fixture
    monkeypatch.setattr(d,'Model',lambda **kw:pytest.fail('Dry-run must not construct SDK/Model'))
    s=d.run_diagnostic(config,root,out/'dry')
    assert s['model_calls']==s['request_audit']['prepared_attempts']==0
    assert s['semantic_assertion_rate'] is None


@pytest.mark.parametrize('point',['during_sdk','after_result'])
def test_crash_is_durable_and_same_directory_cannot_resume(diagnostic_fixture,point):
    config,root,out,plan=diagnostic_fixture
    script="""
import os,sys
from pathlib import Path
from bird_starter import repair_diagnostic as d
d.ROOT=Path(sys.argv[1]);d.historical_hashes=lambda:{}
point=sys.argv[5]
class Crash(d.DiagnosticMock):
    def create(self,**kw):
        if point=='during_sdk':os._exit(91)
        return super().create(**kw)
if point=='after_result':
    finish=d.RequestLog.finish
    def crash(*a,**kw):finish(*a,**kw);os._exit(91)
    d.RequestLog.finish=crash
d.run_diagnostic(sys.argv[2],sys.argv[3],sys.argv[4],execution='mock',client_factory=Crash)
"""
    env=dict(os.environ,PYTHON_DOTENV_DISABLED='1',LANGSMITH_TRACING='false',LANGCHAIN_TRACING_V2='false')
    result=subprocess.run([sys.executable,'-c',script,str(out.parent),str(config),str(root),str(out/'crash'),point],cwd=PROJECT,env=env,capture_output=True,timeout=40)
    assert result.returncode==91
    saved=read(out/'crash'/'requests.config.json')
    audit=inspect_request_log(out/'crash'/'requests.attempts.jsonl',saved['run_id'],8)
    assert audit['prepared_attempts']==1 and not audit['automatic_resume_allowed']
    assert audit['indeterminate_attempts']==int(point=='during_sdk')
    assert (out/'crash'/'results.jsonl').stat().st_size==0
    before=(out/'crash'/'requests.attempts.jsonl').read_bytes()
    with pytest.raises(FileExistsError):d.run_diagnostic(config,root,out/'crash',execution='mock')
    assert (out/'crash'/'requests.attempts.jsonl').read_bytes()==before


@pytest.mark.parametrize('field,value',[('sdk_retries',1),('request_retries',1),('concurrency',2),('http_redirects',True)])
def test_retry_concurrency_and_redirect_changes_rejected_before_sdk(diagnostic_fixture,field,value):
    config,root,out,plan=diagnostic_fixture
    plan['common'][field]=value;config.write_text(json.dumps(plan),encoding='utf-8')
    with pytest.raises(ValueError): d.run_diagnostic(config,root,out/'bad',execution='mock')


@pytest.mark.parametrize('kwargs',[{'execution':'live'},{'execution':'dry-run','allow_api':True},{'max_calls':9}])
def test_live_requires_new_authorization_and_hard_budget(diagnostic_fixture,kwargs):
    config,root,out,_=diagnostic_fixture
    with pytest.raises(ValueError):d.run_diagnostic(config,root,out/'bad',**kwargs)


def test_indeterminate_sdk_error_stops_remaining_cases_without_retry(diagnostic_fixture):
    import httpx
    from openai import APITimeoutError
    config,root,out,_=diagnostic_fixture
    calls=[]
    class Timeout(d.DiagnosticMock):
        def create(self,**kw):
            calls.append(kw)
            raise APITimeoutError(request=httpx.Request('POST','https://offline.invalid/v1'))
    s=d.run_diagnostic(config,root,out/'timeout',execution='mock',client_factory=Timeout)
    assert len(calls)==s['model_calls']==1 and not s['complete']
    assert s['request_audit']['indeterminate_attempts']==1 and not s['request_audit']['automatic_resume_allowed']


def test_repair_never_reads_bird_case_json_or_invokes_graph(diagnostic_fixture,monkeypatch):
    from bird_starter import data,schema_workflow,ablation
    config,root,out,_=diagnostic_fixture
    def deny(*args,**kw):pytest.fail('No BIRD cases, Generator or Graph allowed')
    monkeypatch.setattr(data,'load_cases',deny)
    monkeypatch.setattr(ablation,'load_cases',deny)
    monkeypatch.setattr(schema_workflow,'build_schema_graph',deny)
    s=d.run_diagnostic(config,root,out/'isolated',execution='mock')
    assert s['semantic_assertion_passes']==8
