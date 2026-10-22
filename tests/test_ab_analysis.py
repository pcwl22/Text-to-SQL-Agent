"""Synthetic metadata/SQL fixtures test offline accounting, not model quality."""
import hashlib

import pytest

from scripts.analyze_ablation_ab import (
    align, costs, historical_selection, node_cost, repair_counts,
    repair_observation, remove_position_predicate, timing, verify_freeze,
)


def row(**updates):
    value={'case_id':1,'db_id':'fixture','generation_success':True,'final_execution_ok':True,
        'error_category':None,'output_truncated':False,'initial_sql':'SELECT 1',
        'prompt_tokens':100,'completion_tokens':20,'model_latency_ms':10.,'total_latency_ms':12.,
        'node_metrics':{'schema_linking':{'model_calls':1,'prompt_tokens':110,'completion_tokens':10,'latency_ms':7.},
                        'generation':{'model_calls':1,'prompt_tokens':20,'completion_tokens':10,'latency_ms':3.}}}
    return {**value,**updates}


@pytest.mark.parametrize('change',[{'case_id':2},{'db_id':'other'}])
def test_alignment_rejects_identity_or_database_drift(change):
    with pytest.raises(ValueError): align([row()],[row(**change)])


def test_alignment_does_not_silently_zip_truncate_or_accept_duplicates():
    with pytest.raises(ValueError): align([row()],[])
    with pytest.raises(ValueError): align([row(),row()],[row(),row()])


@pytest.mark.parametrize('official',[0,1])
@pytest.mark.parametrize('truncated',[True,False])
def test_execution_success_never_qualifies_for_repair(official,truncated):
    observation=repair_observation(row(output_truncated=truncated),official)
    assert not observation['repair_eligible']
    assert observation['sql_error_category'] is None
    assert observation['complete_execution_mismatch'] == (not truncated and official==0)


def test_length_and_evaluator_placeholder_errors_are_not_real_sql_syntax_errors():
    r=row(generation_success=False,final_execution_ok=False,error_category='model_output_truncated')
    result=repair_counts([r],{1:{'res':0,'status':'SQL_ERROR'}})
    assert result['counts']['length']==1
    assert result['counts']['syntax']==result['counts']['repair_eligible']==0


@pytest.mark.parametrize('category',['missing_table','missing_column','ambiguous_column'])
def test_recorded_frozen_categories_require_safe_candidate(category):
    assert repair_observation(row(final_execution_ok=False,error_category=category),0)['repair_eligible']
    unsafe=repair_observation(row(final_execution_ok=False,error_category=category,initial_sql='DROP TABLE x'),0)
    assert not unsafe['repair_eligible'] and unsafe['sql_error_category']=='safety_rejected'


def test_coarse_legacy_schema_error_stays_unknown_without_typed_diagnostic():
    v=repair_observation(row(final_execution_ok=False,error_category='sql_schema_error'),0)
    assert v['repair_eligibility_status'].startswith('UNKNOWN') and not v['repair_eligible']


def test_unknown_model_usage_is_not_replaced_with_zero_or_complete_cost():
    b=row(prompt_tokens=130,completion_tokens=20)
    result=costs([row()],[b])
    assert result['generator_prompt_tokens_saved']==80
    assert result['net_additional_tokens']==30
    assert result['linking_total_tokens']==120
    result=costs([row()],[row(node_metrics={},prompt_tokens=None)])
    assert not result['token_comparison_complete']
    assert result['B_generation']['calls'] is None
    assert 'net_additional_tokens' not in result


def test_latency_residual_is_separate_from_node_time_and_missing_is_unknown():
    assert timing(row(),'B')['non_model_wall_residual_ms']==2
    assert timing(row(node_metrics={}), 'B')['non_model_wall_residual_ms'] is None
    assert node_cost([row(node_metrics={})],'generation')['prompt_tokens'] is None


def test_schema_hash_and_table_evidence_do_not_invent_column_projection():
    full='CREATE TABLE kept(id INTEGER); CREATE TABLE removed(x TEXT);'
    snapshot={'full_schema':full,'tables':{'kept':{'columns':[{'name':'id'}],'primary_key':['id']},
                'removed':{'columns':[{'name':'x'}],'primary_key':[]}},'relations':[]}
    r={'selected_tables':['kept'],'schema_linking':{'full_schema_sha256':hashlib.sha256(full.encode()).hexdigest()}}
    evidence=historical_selection(r,snapshot)
    assert evidence['columns_in_deleted_tables']=={'removed':['x']}
    assert evidence['exact_selected_schema'].startswith('UNKNOWN')
    assert evidence['selected_columns_in_retained_tables'].startswith('UNKNOWN')
    r['schema_linking']['full_schema_sha256']='bad'
    with pytest.raises(ValueError): historical_selection(r,snapshot)


def test_counterfactual_only_removes_specific_model_predicate_and_remains_select():
    sql="SELECT COUNT(*) FROM member WHERE member.position = 'Student_Club' AND member.t_shirt_size = 'Medium'"
    altered=remove_position_predicate(sql)
    assert 'position' not in altered and 'Medium' in altered and altered.startswith('SELECT')
    with pytest.raises(ValueError): remove_position_predicate('SELECT 1')


def test_history_hash_mismatch_fails_without_changing_evidence(tmp_path):
    p=tmp_path/'original.json'
    p.write_text('fixture',encoding='utf-8')
    with pytest.raises(ValueError): verify_freeze({'protected':{str(p):'bad'},'runs':{}})
    assert p.read_text()=='fixture'
