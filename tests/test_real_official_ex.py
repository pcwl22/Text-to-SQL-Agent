"""Opt-in REAL BIRD evaluator controls. Gold-vs-Gold is not model accuracy."""
import json
import os
from pathlib import Path

import pytest

from bird_starter.data import dataset_paths, find_db
from bird_starter.experiment_sets import file_sha256
from bird_starter.official_ex_controls import prepare_controls
from bird_starter.official_ex_windows import DEFAULT_MANIFEST, DEFAULT_SOURCE, run_isolated, verify_source


@pytest.mark.bird_integration
def test_real_bird_official_ex_controls(tmp_path):
    if os.environ.get('BIRD_RUN_INTEGRATION')!='1':
        pytest.skip('Opt-in real BIRD official EX controls; no fixture substitution')
    verify_source(DEFAULT_SOURCE)
    cases,dbroot=dataset_paths()
    assert file_sha256(DEFAULT_MANIFEST)=='7136f72aa9e0b78ccab8b29dfb077247a082e541604b4f8841fede2a63ba6dd8'
    manifest=json.loads(DEFAULT_MANIFEST.read_text())
    assert len(manifest['cases'])==33 and len({c['db_id'] for c in manifest['cases']})==11
    before={db:file_sha256(find_db(dbroot,db)) for db in {c['db_id'] for c in manifest['cases']}}
    root=Path(os.environ.get('BIRD_OFFICIAL_EX_REPORT_DIR',str(tmp_path/'official_ex')))
    controls=prepare_controls(cases,DEFAULT_MANIFEST,root/'controls')
    for scenario,correct in [('gold',33),('invalid',32),('missing',32)]:
        dest=root/scenario
        process=run_isolated(controls/scenario,dbroot,dest)
        assert process['exit_code']==0 and not process['timed_out'],process
        result=json.loads((dest/'summary.json').read_text())
        assert result['planned_cases']==33 and result['correct']==correct
        assert result['official_counts']==[11,11,11,33]
        assert result['official_ex']==correct/33 and result['scoring_logic_modified'] is False
        assert [r['case_id'] for r in result['rows']]==[r['case_id'] for r in manifest['cases']]
        assert all(r['worker_reaped'] for r in result['rows'])
        assert result['copy_sha256_before']==result['copy_sha256_after']
        assert not (dest/'database_copies').exists()
        if scenario!='gold': assert result['rows'][0]['res']==0
        assert sum(r['res'] for r in result['rows'][1:])==32
    assert before=={db:file_sha256(find_db(dbroot,db)) for db in before}
