"""Independent, hand-authored SQL repair diagnostics; never an ablation or BIRD EX.

Reuse the formal Repair prompt, adapter, taxonomy and executor without invoking
Generator/Schema Linking. Expected queries/results stay outside repair inputs.
"""
from contextlib import closing
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace as NS
import uuid

from sqlglot import exp
import sqlglot

from .ablation import offline_dotenv
from .data import find_db
from .experiment_sets import file_sha256
from .model import Model, ModelFailure, TrackedModel, extract_sql
from .reporting import latency_stats
from .request_audit import RequestLog, inspect_request_log, write_new_json, AUDIT_VERSION
from .schema_linking import read_schema
from .schema_evaluation import gold_dependencies
from .sql_repair import (REPAIRABLE, REPAIR_PROMPT_VERSION, classify_error,
                         lexical_read_guard, validate_candidate, repair_prompt)
from .tools import execute_select, ro_connect, _query_authorizer

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'repair-diagnostic-v1'
CONTENT_ERRORS = {'empty_response', 'model_output_truncated'}


@dataclass(frozen=True)
class RepairInput:
    question: str
    evidence: str
    schema: str
    initial_sql: str
    category: str
    db_path: Path


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if (plan['version'] != VERSION or plan['positive_count'] != 8 or plan['negative_count'] != 4
            or plan['max_http_attempts'] != 8 or plan['concurrency'] != 1 or plan['request_retries'] != 0
            or plan['repair_prompt_version'] != REPAIR_PROMPT_VERSION):
        raise ValueError('Unsupported diagnostic protocol')
    pos, neg = plan['positive_cases'], plan['negative_cases']
    ids = [c['case_id'] for c in pos+neg]
    if (len(pos) != 8 or len(neg) != 4 or len(set(ids)) != 12
            or len({c['db_id'] for c in pos}) != plan['positive_database_count']
            or set(plan['semantic_assertions']) != {c['case_id'] for c in pos}
            or any(c['expected_error_category'] not in REPAIRABLE for c in pos)):
        raise ValueError('Invalid frozen diagnostic cohort')
    for path, digest in plan['source_profiles'].items():
        if file_sha256(ROOT/path) != digest:
            raise ValueError('Frozen formal implementation/configuration changed')
    common = plan['common']
    if (common['request_retries'] != 0 or common['sdk_retries'] != 0 or common['concurrency'] != 1
            or common['http_redirects'] is not False or common['n'] != 1):
        raise ValueError('Diagnostics require serial execution and no retries/redirects')
    return plan


def initial_error(db_path, sql):
    """Same Phase 3B validate/execute/classify; actual SQLite error probe also saved."""
    try:
        validate_candidate(sql)
        execute_select(db_path, sql, timeout_seconds=8, max_rows=1000)
    except Exception as exc:
        category = str(classify_error(exc, sql))
    else:
        raise ValueError('Positive case has no initial SQL error')
    # Only known positive read-only lexical candidates may reach EXPLAIN. This
    # verifies SQLite rejects even a candidate rejected earlier by AST parsing.
    lexical_read_guard(sql)
    with closing(ro_connect(db_path)) as conn:
        conn.enable_load_extension(False)
        conn.set_authorizer(_query_authorizer)
        try:
            conn.execute('EXPLAIN QUERY PLAN '+sql).fetchall()
        except sqlite3.Error as exc:
            code, name = exc.sqlite_errorcode, exc.sqlite_errorname
        else:
            raise ValueError('SQLite probe unexpectedly succeeded')
    return {'category':category, 'sqlite_errorcode':code, 'sqlite_errorname':name,
            'native_diagnostic_sent_to_model':False}


def negative_error(case, db_path):
    sql, kind = case['initial_sql'], case['kind']
    try:
        if kind == 'permission_denied':
            validate_candidate(sql)
            with closing(ro_connect(db_path)) as conn:
                conn.set_authorizer(lambda action,*args: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ
                                    else _query_authorizer(action,*args))
                conn.execute(sql).fetchall()
        elif kind == 'timeout':
            validate_candidate(sql)
            execute_select(db_path,sql,timeout_seconds=case['timeout_seconds'],max_rows=1)
        elif kind in {'dangerous_sql','dangerous_extension'}:
            validate_candidate(sql)  # Never execute dangerous negatives, even EXPLAIN.
        else:
            raise ValueError('Unknown negative type')
    except ValueError as exc:
        from .tools import SQLSafetyError
        if not isinstance(exc, SQLSafetyError):
            raise
        return {'category':str(classify_error(exc,sql)), 'sqlite_errorcode':None}
    except sqlite3.Error as exc:
        return {'category':str(classify_error(exc,sql)), 'sqlite_errorcode':getattr(exc,'sqlite_errorcode',None)}
    raise ValueError('Negative case did not produce expected rejection')


def prepare(plan, db_root):
    paths = {db:find_db(db_root,db) for db in plan['database_sha256']}
    hashes = {db:file_sha256(path) for db,path in paths.items()}
    if hashes != plan['database_sha256']:
        raise ValueError('Frozen database bytes changed')
    snapshots = {}
    for db, frozen in plan['schemas'].items():
        actual = read_schema(paths[db])
        if (actual['full_schema'] != frozen['ddl']
                or hashlib.sha256(frozen['ddl'].encode()).hexdigest() != frozen['sha256']):
            raise ValueError('Frozen diagnostic schema changed')
        snapshots[db] = actual
    inputs, errors, expected = {}, {}, {}
    for case in plan['positive_cases']:
        cid, db = case['case_id'], case['db_id']
        err = initial_error(paths[db],case['initial_sql'])
        if err['category'] != case['expected_error_category'] or err['sqlite_errorcode'] != sqlite3.SQLITE_ERROR:
            raise ValueError('Initial error differs from frozen expectation')
        inputs[cid] = RepairInput(case['question'],case['evidence'],plan['schemas'][db]['ddl'],
                                  case['initial_sql'],err['category'],paths[db])
        errors[cid] = err
        oracle = plan['semantic_assertions'][cid]
        validate_candidate(oracle['reference_sql'])
        result = execute_select(paths[db],oracle['reference_sql'],timeout_seconds=8,max_rows=1000)
        if (not result['complete'] or len(result['columns']) != 1 or len(result['rows']) != 1
                or type(result['rows'][0][0]) is not int):
            raise ValueError('Frozen semantic oracle is not a bounded scalar count')
        expected[cid] = result['rows']
    negatives = []
    for case in plan['negative_cases']:
        error = negative_error(case,paths[case['db_id']])
        if error['category'] != case['expected_error_category'] or error['category'] in REPAIRABLE:
            raise ValueError('Negative did not fail closed')
        negatives.append({'case_id':case['case_id'],'db_id':case['db_id'],'kind':case['kind'],
                          **error,'repair_rejected':True,'model_calls':0,'request_attempt_ids':[]})
    return inputs, errors, expected, negatives, snapshots, paths, hashes


def repair_once(agent, model, *, timeout=8, max_rows=1000, max_prompt_chars=200000):
    result = {'repaired_sql':'','response_valid':False,'sql_safety_pass':False,
              'execution_ok':False,'output_complete':None,'error_category':None}
    if agent.category not in REPAIRABLE:
        result['error_category']='repair_not_eligible'
        return result, None
    system,user = repair_prompt(agent.question,agent.evidence,agent.schema,agent.initial_sql,agent.category)
    if len(system)+len(user)>max_prompt_chars:
        result['error_category']='model_prompt_limit'
        return result,None
    try:
        sql = extract_sql(model.ask(system,user))  # Exactly one ask, never a Generator call.
        if not sql:
            raise ModelFailure('empty_response')
        result.update(repaired_sql=sql,response_valid=True)
    except ModelFailure as exc:
        result['error_category']=exc.category
        return result,None
    except Exception:
        result['error_category']='model_error'
        return result,None
    try:
        validate_candidate(sql)
        result['sql_safety_pass']=True
        output=execute_select(agent.db_path,sql,timeout_seconds=timeout,max_rows=max_rows)
        result.update(execution_ok=True,output_complete=output['complete'])
        return result,output
    except Exception as exc:
        result['error_category']=str(classify_error(exc,sql))
        return result,None


def semantic_assertion(sql, output, assertion, expected, snapshot):
    """Evaluator-only oracle; never part of RepairInput or repair_prompt()."""
    if output is None or not output['complete']:
        return False
    if len(output['columns'])!=assertion['expected_columns'] or len(output['rows'])!=assertion['expected_rows']:
        return False
    deps=gold_dependencies(sql,snapshot)  # MODEL SQL, not a BIRD Gold query.
    if deps['status']!='RESOLVED' or not set(assertion['required_tables'])<=set(deps['tables']):
        return False
    counts=list(sqlglot.parse_one(sql,read='sqlite').find_all(exp.Count))
    if assertion['requires_count'] and not counts:
        return False
    if assertion['requires_distinct'] and not any(isinstance(c.this,exp.Distinct) for c in counts):
        return False
    return output['rows']==expected


class DiagnosticMock:
    """Scripted test responses are not evidence of real model repair ability."""
    def __init__(self, plan, scenario='correct'):
        self.plan,self.scenario,self.case_id,self.calls=plan,scenario,None,[]

    def set_case(self,case_id):
        self.case_id=case_id

    def create(self,**kwargs):
        self.calls.append(kwargs)
        payload=json.loads(kwargs['messages'][1]['content'])
        if set(payload)!={'question','evidence','schema','original_sql','error_category'}:
            raise AssertionError('Unexpected Repair prompt fields')
        text=self.plan['semantic_assertions'][self.case_id]['reference_sql']
        if self.scenario=='still_wrong': text='SELECT 1 FROM __diagnostic_missing_after_repair__'
        if self.scenario=='dangerous': text='DROP TABLE forbidden'
        if self.scenario=='semantic_wrong': text='SELECT COUNT(*) FROM '+self.plan['semantic_assertions'][self.case_id]['required_tables'][0]+' WHERE 0'
        if self.scenario=='empty': text=''
        return NS(status_code=200,_request_id='mock-'+str(len(self.calls)),model=self.plan['common']['model'],
            choices=[NS(message=NS(content=text),finish_reason='length' if self.scenario=='length' else 'stop')],
            usage=NS(prompt_tokens=32,completion_tokens=16))


def historical_hashes():
    paths=[]
    for directory in ('outputs/phase3c_live/A_v1','outputs/phase3c_live/B_v1','outputs/phase2_live'):
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file())
    return {str(p):file_sha256(p) for p in paths}


def summarize(rows, negatives, audit, execution):
    n=8
    calls=sum(r['model_calls'] for r in rows)
    missing=sum(r['model_calls']-r.get('usage_reported_calls',0) for r in rows)
    count=lambda key:sum(r.get(key) is True for r in rows)
    return {'measurement_kind':'REAL_DIAGNOSTIC' if execution=='live' else 'OFFLINE_MOCK_NOT_MODEL_QUALITY' if execution=='mock' else 'DRY_RUN',
        'planned_positive_cases':n,'processed_cases':sum(r['processed'] for r in rows),
        'valid_repair_responses':count('response_valid'),'response_valid_rate':None if execution=='dry-run' else count('response_valid')/n,
        'safe_sql_count':count('sql_safety_pass'),'safe_sql_rate':None if execution=='dry-run' else count('sql_safety_pass')/n,
        'execution_successes':count('execution_ok'),'execution_success_rate':None if execution=='dry-run' else count('execution_ok')/n,
        'semantic_assertion_passes':count('semantic_assertion_pass'),'semantic_assertion_rate':None if execution=='dry-run' else count('semantic_assertion_pass')/n,
        'negative_cases':len(negatives),'negative_rejections':sum(r['repair_rejected'] for r in negatives),
        'negative_rejection_rate':sum(r['repair_rejected'] for r in negatives)/len(negatives),
        'model_calls':calls,'prompt_tokens':None if missing else sum(r.get('prompt_tokens') or 0 for r in rows),
        'completion_tokens':None if missing else sum(r.get('completion_tokens') or 0 for r in rows),
        'usage_missing_calls':missing,'repair_latency_ms':latency_stats([r.get('model_latency_ms') for r in rows if r['model_calls']]),
        'error_category_counts':dict(Counter(r['error_category'] for r in rows if r['error_category'])),
        'request_audit':audit,'official_ex':None,'actual_cost':None,
        'notice':'Hand-authored structural diagnostics, not BIRD EX or natural-error ablation quality.'}


def run_diagnostic(config, db_root, out_dir, *, execution='dry-run', allow_api=False,
                   max_calls=8, mock_scenario='correct', client_factory=None):
    if execution not in {'dry-run','mock','live'} or type(max_calls) is not int or not 0<=max_calls<=8:
        raise ValueError('Invalid diagnostic mode or budget')
    if (execution=='live' and not allow_api) or (allow_api and execution!='live'):
        raise ValueError('A new diagnostic-specific authorization is required')
    if client_factory is not None and execution!='mock':
        raise ValueError('Injected clients are Mock only')
    plan=load_plan(config)
    before_history=historical_hashes()
    inputs, errors, expected, negatives, snapshots, paths, before=prepare(plan,db_root)
    folder=Path(out_dir).resolve()
    if not folder.is_relative_to((ROOT/'outputs').resolve()) or folder==(ROOT/'outputs').resolve():
        raise ValueError('Outputs must be private and fresh')
    folder.mkdir(parents=True,exist_ok=False)  # Cross-process exclusive namespace; never resume.
    run_id=uuid.uuid4().hex
    common=plan['common']
    write_new_json(folder/'requests.config.json',{'run_id':run_id,'created_at_utc':datetime.now(timezone.utc).isoformat(),
        'diagnostic_version':VERSION,'execution':execution,'measurement_family':'INDEPENDENT_REPAIR_DIAGNOSTIC',
        'max_model_calls':max_calls,'request_audit_version':AUDIT_VERSION,'attempt_log_file':'requests.attempts.jsonl',
        'plan_sha256':file_sha256(config),'config_path':str(Path(config).resolve()),'common':common,
        'positive_ids':[c['case_id'] for c in plan['positive_cases']],'negative_ids':[c['case_id'] for c in plan['negative_cases']],
        'generator_enabled':False,'schema_linking_enabled':False,'db_root':str(Path(db_root).resolve()),
        'schema_sha256':{db:item['sha256'] for db,item in plan['schemas'].items()},'database_sha256':before,
        'initial_error_checks':errors,'api_authorized':execution=='live' and allow_api,'automatic_resume_allowed':False})
    write_new_json(folder/'negative_results.json',negatives)
    write_new_json(folder/'semantic_oracle_results.json',expected)
    log=RequestLog(folder/'requests.attempts.jsonl',run_id,common['model'],max_calls)
    rows=[]; tracked=model=None; stop=None
    sdk=None
    try:
        if execution!='dry-run':
            try:
                if execution=='mock': sdk=client_factory(plan) if client_factory else DiagnosticMock(plan,mock_scenario)
                with offline_dotenv(execution!='live'):
                    model=Model(model=common['model'],base_url=common['base_url'],temperature=common['temperature'],
                        request_timeout=common['request_timeout_seconds'],max_retries=0,max_calls=max_calls,
                        max_output_tokens=common['max_output_tokens'],token_parameter=common['token_parameter'],
                        attempt_log=log,client=NS(chat=NS(completions=sdk)) if sdk else None)
                tracked=TrackedModel(model)
            except Exception:
                stop='model_configuration_error'
        with (folder/'results.jsonl').open('x',encoding='utf-8') as stream:
            for case in plan['positive_cases']:
                cid=case['case_id']
                row={'run_id':run_id,'case_id':cid,'db_id':case['db_id'],'initial_error':errors[cid],
                    'processed':False,'response_valid':False,'sql_safety_pass':False,'execution_ok':False,
                    'semantic_assertion_pass':False,'model_calls':0,'usage_reported_calls':0,'prompt_tokens':None,
                    'completion_tokens':None,'request_attempt_ids':[],'error_category':None,'repaired_sql':'',
                    'generator_calls':0,'schema_linking_calls':0,'repair_attempts':0,
                    'measurement_kind':'REAL_DIAGNOSTIC' if execution=='live' else 'DRY_RUN' if execution=='dry-run' else 'OFFLINE_MOCK_NOT_MODEL_QUALITY'}
                if stop:
                    row.update(error_category=stop,status='not_run')
                elif execution=='dry-run':
                    row.update(processed=True,status='dry_run_ok')
                else:
                    tracked.reset();model.set_case_id(cid)
                    if sdk is not None and callable(getattr(sdk,'set_case',None)): sdk.set_case(cid)
                    started=time.monotonic()
                    row.update(processed=True,repair_attempts=1)
                    result,output=repair_once(inputs[cid],tracked,timeout=common['sql_timeout_seconds'],
                        max_rows=common['max_rows'],max_prompt_chars=common['max_prompt_chars'])
                    row.update(result,**tracked.metrics)
                    row['agent_latency_ms']=round((time.monotonic()-started)*1000,3)
                    if output is not None:
                        row['semantic_assertion_pass']=semantic_assertion(result['repaired_sql'],output,
                            plan['semantic_assertions'][cid],expected[cid],snapshots[case['db_id']])
                        row['result_row_count']=len(output['rows'])
                    row['status']='execution_succeeded' if row['execution_ok'] else 'failed'
                    err=row['error_category']
                    if err and err not in CONTENT_ERRORS and err not in {'syntax','missing_table','missing_column','ambiguous_column','safety_rejected','timeout','resource_limit'}:
                        stop=err
                stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush();os.fsync(stream.fileno())
                rows.append(row)
    finally:
        try:
            if model: model.close()
        finally:
            log.close()
            after={db:file_sha256(path) for db,path in paths.items()}
            history_after=historical_hashes()
            write_new_json(folder/'integrity.json',{'database_before':before,'database_after':after,
                'sqlite_unchanged':before==after,'historical_files':len(before_history),
                'historical_files_unchanged':before_history==history_after})
            if after!=before or history_after!=before_history:
                raise RuntimeError('Original database/history bytes changed')
    audit=inspect_request_log(log.path,run_id,max_calls)
    summary=summarize(rows,negatives,audit,execution)
    summary.update(run_id=run_id,stop_reason=stop,complete=stop is None,
                   remote_api_requests=0 if execution!='live' else None)
    write_new_json(folder/'summary.json',summary)
    return summary
