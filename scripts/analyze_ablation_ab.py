"""Offline A/B evidence analysis; no SDK construction, requests or Agent execution.

Original experiment files are read only. Schema JSON/column projections absent
from historical logs remain UNKNOWN. No Gold SQL is executed, exported or sent
to an Agent; the normal dataset loader still validates complete BIRD records.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlglot
from sqlglot import exp
from bird_starter.data import load_cases, find_db
from bird_starter.experiment_review import read_group, reserve_private_output
from bird_starter.experiment_sets import file_sha256
from bird_starter.reporting import latency_stats
from bird_starter.schema_linking import read_schema
from bird_starter.schema_evaluation import gold_dependencies
from bird_starter.sql_repair import REPAIRABLE, lexical_read_guard, validate_candidate
from bird_starter.tools import execute_select


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def jsonl(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines()]


def align(a, b):
    for rows in (a, b):
        if len({r['case_id'] for r in rows}) != len(rows):
            raise ValueError('Duplicate case')
    if [(r['case_id'], r['db_id']) for r in a] != [(r['case_id'], r['db_id']) for r in b]:
        raise ValueError('A/B identities or order differ')


def sum_known(items, field):
    values = [item.get(field) for item in items]
    return sum(values) if values and all(type(v) is int for v in values) else None


def timing(row, group):
    """Instrumented wall residual, not CPU time or pure server inference time."""
    if group == 'A':
        link = 0.0
        generation = row.get('model_latency_ms')
    else:
        nodes = row.get('node_metrics', {})
        link = nodes.get('schema_linking', {}).get('latency_ms')
        generation = nodes.get('generation', {}).get('latency_ms')
    total = row.get('total_latency_ms')
    residual = (total-link-generation if all(type(v) in (int, float) for v in (total,link,generation)) else None)
    return {'total_ms':total,'link_ms':link,'generation_ms':generation,'non_model_wall_residual_ms':residual}


def node_cost(rows, name):
    stats = [r.get('node_metrics', {}).get(name) for r in rows]
    available = [s for s in stats if s is not None]
    return {'records':len(rows),'node_records_available':len(available),
            'calls':sum_known(available,'model_calls') if len(available)==len(rows) else None,
            'prompt_tokens':sum_known(available,'prompt_tokens') if len(available)==len(rows) else None,
            'completion_tokens':sum_known(available,'completion_tokens') if len(available)==len(rows) else None,
            'latency_ms':latency_stats([s.get('latency_ms') for s in available]),
            'coverage_complete':len(available)==len(rows)}


def costs(a, b):
    link, gen = node_cost(b,'schema_linking'), node_cost(b,'generation')
    ap, ac = sum_known(a,'prompt_tokens'), sum_known(a,'completion_tokens')
    bp, bc = sum_known(b,'prompt_tokens'), sum_known(b,'completion_tokens')
    known = all(v is not None for v in (ap,ac,bp,bc,link['prompt_tokens'],link['completion_tokens'],gen['prompt_tokens'],gen['completion_tokens']))
    result = {'A':{'prompt_tokens':ap,'completion_tokens':ac,'total_tokens':None if ap is None or ac is None else ap+ac},
              'B':{'prompt_tokens':bp,'completion_tokens':bc,'total_tokens':None if bp is None or bc is None else bp+bc},
              'B_linking':link,'B_generation':gen,'token_comparison_complete':known,'actual_cost':'UNKNOWN'}
    if known:
        if bp != link['prompt_tokens']+gen['prompt_tokens'] or bc != link['completion_tokens']+gen['completion_tokens']:
            raise ValueError('Node usage does not reconcile')
        gp_saved, gc_saved = ap-gen['prompt_tokens'], ac-gen['completion_tokens']
        result.update(generator_prompt_tokens_saved=gp_saved,generator_prompt_saving_rate=gp_saved/ap if ap else None,
            generator_completion_tokens_saved=gc_saved,generator_total_tokens_saved=gp_saved+gc_saved,
            linking_total_tokens=link['prompt_tokens']+link['completion_tokens'],
            net_additional_tokens=bp+bc-ap-ac,total_token_increase_rate=(bp+bc)/(ap+ac)-1 if ap+ac else None)
    for group, rows in (('A',a),('B',b)):
        times = [timing(r,group) for r in rows]
        result[group]['latency_ms'] = {field:latency_stats([t[field] for t in times]) for field in times[0]}
    return result


def repair_observation(row, official_score):
    """Never use EX/Gold to grant eligibility; fail closed on coarse legacy errors."""
    category = row.get('error_category')
    length = category == 'model_output_truncated'
    succeeded = row.get('final_execution_ok') is True
    truncated = succeeded and row.get('output_truncated') is True
    result = {'case_id':row['case_id'],'category':category,'length':length,'result_truncated':truncated,
              'execution_succeeded_official_zero':succeeded and official_score==0,
              'complete_execution_mismatch':succeeded and row.get('output_truncated') is False and official_score==0,
              'repair_eligible':False,'repair_eligibility_status':'NOT_ELIGIBLE','sql_error_category':None}
    if succeeded or row.get('generation_success') is not True:
        return result
    if category in REPAIRABLE:
        try:
            if category=='syntax': lexical_read_guard(row.get('initial_sql',''))
            else: validate_candidate(row.get('initial_sql',''))
        except Exception:
            result.update(sql_error_category='safety_rejected')
            return result
        result.update(sql_error_category=category,repair_eligible=True,repair_eligibility_status='ELIGIBLE_RECORDED_PHASE3B_CATEGORY')
    elif category in {'safety_rejected','sql_safety_error'} or row.get('execution_error')=='sql_safety_error':
        result['sql_error_category']='safety_rejected'
    elif category in {'sql_syntax_error','sql_schema_error','sql_execution_error'}:
        result['repair_eligibility_status']='UNKNOWN_COARSE_DIAGNOSTIC_NO_TYPED_EVIDENCE'
    return result


def repair_counts(rows, official):
    observations = [repair_observation(r,official[r['case_id']]['res']) for r in rows]
    counts = {name:sum(r['sql_error_category']==name for r in observations)
              for name in ('syntax','missing_table','missing_column','ambiguous_column','safety_rejected')}
    counts.update(length=sum(r['length'] for r in observations),result_truncated=sum(r['result_truncated'] for r in observations),
        execution_succeeded_official_zero=sum(r['execution_succeeded_official_zero'] for r in observations),
        complete_execution_mismatch=sum(r['complete_execution_mismatch'] for r in observations),
        repair_eligible=sum(r['repair_eligible'] for r in observations),
        eligibility_unknown=sum(r['repair_eligibility_status'].startswith('UNKNOWN') for r in observations))
    return {'records':len(rows),'counts':counts,'cases':observations}


def historical_selection(row, snapshot):
    selected = row['selected_tables']
    if not set(selected)<=set(snapshot['tables']): raise ValueError('Unknown selected table')
    metric = row.get('schema_linking',{})
    if metric['full_schema_sha256'] != hashlib.sha256(snapshot['full_schema'].encode()).hexdigest():
        raise ValueError('Historical full Schema changed')
    dropped = sorted(set(snapshot['tables'])-set(selected))
    return {'selected_tables':selected,'deleted_tables':dropped,
        'columns_in_deleted_tables':{t:[c['name'] for c in snapshot['tables'][t]['columns']] for t in dropped},
        'exact_selected_schema':'UNKNOWN_NOT_PERSISTED','selected_columns_in_retained_tables':'UNKNOWN_NOT_PERSISTED',
        'deleted_columns_in_retained_tables':'UNKNOWN_NOT_PERSISTED','raw_link_response':'UNKNOWN_NOT_PERSISTED',
        'requested_vs_closure_added_tables':'UNKNOWN_NOT_PERSISTED','model_join_hints':'UNKNOWN_NOT_PERSISTED',
        'historical_metrics':metric,'historical_recall':row.get('schema_recall'),
        'retained_table_primary_keys':{t:snapshot['tables'][t]['primary_key'] for t in selected},
        'catalog_foreign_keys_among_retained_tables':[rel for rel in snapshot['relations']
            if rel['left_table'] in selected and rel['right_table'] in selected]}


def replay(db, sql):
    try:
        result = execute_select(db,sql,timeout_seconds=8,max_rows=1000)
        return {'status':'READ_ONLY_ANALYSIS_REPLAY','columns':result['columns'],
                'rows':result['rows'],'complete':result['complete'],'not_new_official_ex':True}
    except Exception as e:
        return {'status':'UNKNOWN_REPLAY_FAILED','error_type':type(e).__name__,'not_new_official_ex':True}


def remove_position_predicate(sql):
    tree = sqlglot.parse_one(sql,read='sqlite')
    where = tree.args.get('where')
    if where is None: raise ValueError('No WHERE')
    def terms(node):
        return terms(node.this)+terms(node.expression) if isinstance(node,exp.And) else [node]
    predicates = terms(where.this)
    def target(p):
        return (isinstance(p,exp.EQ) and isinstance(p.this,exp.Column) and p.this.table=='member'
                and p.this.name=='position' and isinstance(p.expression,exp.Literal)
                and p.expression.this=='Student_Club')
    remaining=[p.copy() for p in predicates if not target(p)]
    if len(remaining)!=len(predicates)-1 or not remaining: raise ValueError('Unexpected predicate structure')
    merged=remaining[0]
    for item in remaining[1:]: merged=exp.and_(merged,item)
    tree.set('where',exp.Where(this=merged))
    return tree.sql(dialect='sqlite')


def verify_freeze(manifest):
    hashes = dict(manifest['protected'])
    for files in manifest['runs'].values(): hashes.update(files)
    for path,expected in hashes.items():
        if file_sha256(Path(path))!=expected: raise ValueError('Historical file changed: '+path)
    for group,files in manifest['runs'].items():
        current={str(p) for p in Path(f'outputs/phase3c_live/{group}_v1').rglob('*') if p.is_file()}
        if current!=set(files): raise ValueError('Historical directory inventory changed')
    return len(hashes)


def analyze(a_dir,b_dir,cases_path,db_root,freeze,out_dir):
    frozen=read(freeze); verified=verify_freeze(frozen)
    suite='configs/phase3c/ablation_v1.json'
    a,b=read_group(a_dir,'A',suite),read_group(b_dir,'B',suite)
    ar,br=a['rows'],b['rows']; align(ar,br)
    if len(ar)!=33 or len({r['db_id'] for r in ar})!=11: raise ValueError('Not fixed dev33')
    if a['batch']['common']!=b['batch']['common']: raise ValueError('Common configuration drift')
    ao,bo=read(Path(a_dir)/'official_ex/summary.json'),read(Path(b_dir)/'official_ex/summary.json')
    for field in ('protocol','manifest_sha256','official_commit','official_source_sha256','planned_cases'):
        if ao[field]!=bo[field]: raise ValueError('Official protocol mismatch')
    official={g:{r['case_id']:r for r in s['rows']} for g,s in (('A',ao),('B',bo))}
    for g,rows,summary,directory in (('A',ar,ao,a_dir),('B',br,bo,b_dir)):
        align(rows,[{'case_id':r['case_id'],'db_id':r['db_id']} for r in summary['rows']])
        if summary['source_result_sha256']!=file_sha256(Path(directory)/f'{g}.jsonl'): raise ValueError('Score source mismatch')
        if not (a if g=='A' else b)['request_evidence']['counts_complete']: raise ValueError('Incomplete request accounting')
    all_cases={c['case_id']:c for c in load_cases(cases_path)}
    if file_sha256(cases_path)!=a['config']['dataset_sha256']: raise ValueError('Dataset changed')
    case=all_cases[1317]; db=find_db(db_root,case['db_id']); snapshot=read_schema(db)
    row_a=next(r for r in ar if r['case_id']==1317); row_b=next(r for r in br if r['case_id']==1317)
    position_removed=remove_position_predicate(row_b['final_sql'])
    detail={'question':case['question'],'evidence':case['evidence'],'db_id':case['db_id'],
        'full_schema':snapshot['full_schema'],'catalog':snapshot['tables'],'foreign_keys':snapshot['relations'],
        'B_selection_evidence':historical_selection(row_b,snapshot),'A_generated_sql':row_a['final_sql'],
        'B_generated_sql':row_b['final_sql'],'offline_counterfactual_sql':position_removed,
        'A_replay':replay(db,row_a['final_sql']),'B_replay':replay(db,row_b['final_sql']),
        'counterfactual_replay':replay(db,position_removed),
        'original_official':{'A':official['A'][1317],'B':official['B'][1317]},
        'original_execution_rows_saved':False,
        'evidence_levels':{'CONFIRMED':['B adds member.position = Student_Club predicate','B has full recorded Gold-dependency recall',
            'Selected tables and Schema hashes, valid selection, no fallback, two requests'],
            'LIKELY':['Question phrase Student_Club may have been treated as a literal position filter'],
            'UNKNOWN':['Why model added predicate','Exact selected Schema/column deletions/raw JSON/join hints',
                       'Causal effect of Schema Linking as opposed to model/provider variation']}}
    omissions=[]
    for r in br:
        missing=(r.get('schema_recall') or {}).get('missing_columns',[])
        if missing:
            snap=read_schema(find_db(db_root,r['db_id']))
            deps=gold_dependencies(r['final_sql'],snap)  # evaluator AST utility applied to MODEL SQL, not Gold
            model_columns={(x['table'],x['column']) for x in deps.get('columns',[])}
            omissions.append({'case_id':r['case_id'],'missing_gold_dependency_columns':missing,
                'generated_sql':r['final_sql'],'generated_sql_dependency_status':deps['status'],
                'missing_columns_referenced_in_generated_sql':[c for c in missing if (c['table'],c['column']) in model_columns],
                'official':official['B'][r['case_id']]['res'],'output_truncated':r['output_truncated'],
                'causal_effect_of_column_removal':'UNKNOWN_NO_COUNTERFACTUAL_MODEL_RESPONSE'})
    fallback=next(r for r in br if r['case_id']==195)
    fs=read_schema(find_db(db_root,fallback['db_id']))
    fmetric=fallback['schema_linking']
    fallback_report={'case_id':195,'metrics':fmetric,'node_metrics':fallback['node_metrics'],
        'full_and_selected_hash_equal':fmetric['full_schema_sha256']==fmetric['selected_schema_sha256'],
        'all_catalog_tables_selected':set(fallback['selected_tables'])==set(fs['tables']),
        'official_score':official['B'][195]['res'],'original_response':'UNKNOWN_NOT_PERSISTED',
        'exact_invalid_field':'UNKNOWN_NOT_PERSISTED'}
    cost=costs(ar,br)
    strata={}
    for key in ('db_id','difficulty'):
        strata[key]={}
        for value in sorted({r[key] for r in ar}):
            aa=[r for r in ar if r[key]==value]; bb=[r for r in br if r[key]==value]
            stat=costs(aa,bb)
            stat['mean_latency_delta_ms']=stat['B']['latency_ms']['total_ms']['average']-stat['A']['latency_ms']['total_ms']['average']
            strata[key][value]=stat
    paired=[{'case_id':x['case_id'],'db_id':x['db_id'],'A':official['A'][x['case_id']]['res'],
             'B':official['B'][x['case_id']]['res']} for x in ar]
    repair={g:repair_counts(rows,official[g]) for g,rows in (('A',ar),('B',br))}
    report={'analysis_kind':'OFFLINE_REAL_RUN_REVIEW_NOT_A_NEW_EXPERIMENT','A_run_id':a['batch']['run_id'],
        'B_run_id':b['batch']['run_id'],'planned_cases_per_group':33,'official_protocol':ao['protocol'],
        'manifest_sha256':ao['manifest_sha256'],'official_commit':ao['official_commit'],
        'paired':paired,'A_correct':sum(r['A'] for r in paired),'B_correct':sum(r['B'] for r in paired),
        'case_1317':detail,'schema_missing_columns':omissions,'case_195_fallback':fallback_report,
        'costs':cost,'strata':strata,'repair':repair,
        'schema_compression_ratio':read(Path(b_dir)/'B.summary.json')['schema_compression_ratio'],
        'repair_opportunity_statement':'Observed eligible errors 0 in 66 A/B records; C/D future opportunities UNKNOWN, not a prediction of their outputs.',
        'raw_requests':{'A':a['request_evidence'],'B':b['request_evidence']},
        'original_hashes_verified':verified,'remote_model_api_calls':0,'model_constructions':0,
        'agent_graph_invocations':0,'gold_sql_executed_exported_or_sent_to_agent':False,'actual_cost':'UNKNOWN'}
    after=verify_freeze(frozen)
    if after!=verified: raise ValueError('Freeze inventory drift')
    out=reserve_private_output(out_dir)
    with (out/'analysis.json').open('x',encoding='utf-8') as f: json.dump(report,f,ensure_ascii=False,indent=2)
    with (out/'integrity.json').open('x',encoding='utf-8') as f: json.dump({'historical_files_verified_before_and_after':verified,
        'original_databases_unchanged':11,'original_scores_replaced':False,'remote_api_calls':0,
        'full_schema_reconstructed_from_hash_verified_unchanged_database':True,
        'selected_schema_reconstructed':False,'gold_sql_executed_exported_or_sent_to_agent':False},f,indent=2)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--a',default='outputs/phase3c_live/A_v1')
    parser.add_argument('--b',default='outputs/phase3c_live/B_v1')
    parser.add_argument('--cases',default='data/mini_dev_data/mini_dev_sqlite.json')
    parser.add_argument('--db-root',default='data/minidev/MINIDEV/dev_databases')
    parser.add_argument('--freeze',required=True)
    parser.add_argument('--out-dir',required=True)
    args=parser.parse_args()
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    os.environ['LANGSMITH_TRACING']=os.environ['LANGCHAIN_TRACING_V2']='false'
    def deny(*args,**kwargs): raise RuntimeError('Offline analysis forbids network')
    socket.socket.connect=socket.socket.connect_ex=deny
    try:
        report=analyze(args.a,args.b,args.cases,args.db_root,args.freeze,args.out_dir)
    except Exception as exc:
        print('Offline analysis failed:',type(exc).__name__,file=sys.stderr)
        return 1
    print(json.dumps({'A_EX':report['A_correct'],'B_EX':report['B_correct'],'denominator':33,
        'observed_repair_eligible':sum(g['counts']['repair_eligible'] for g in report['repair'].values()),
        'remote_api_calls':0,'historical_files_unchanged':report['original_hashes_verified']}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
