"""Static fixtures and offline process tests, never benchmark model accuracy."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from bird_starter.experiment_sets import file_sha256
from bird_starter.official_ex_controls import prepare_controls
import bird_starter.official_ex_windows as win
from bird_starter.official_ex_inputs import FAILURE_SQL, validate_bundle
from bird_starter.tools import ro_connect
from test_ablation import suite_files


@pytest.fixture
def official_source():
    pytest.importorskip('func_timeout')
    pytest.importorskip('pymysql')
    pytest.importorskip('psycopg2')
    if not win.DEFAULT_SOURCE.is_dir():
        pytest.skip('Optional pinned official source cache not installed; no network download in tests')
    return win.verify_source(win.DEFAULT_SOURCE)


def worker_job(tiny_db, official_source):
    return dict(source=str(official_source), db_path=str(tiny_db[1]), copy_root=str(tiny_db[0]),
                index=0, prediction='SELECT amount FROM orders', gold='SELECT amount FROM orders', sql_timeout=2)


def test_credential_whitelist_and_traceback_environment(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'FAKE_NEVER_REAL')
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'FAKE_NEVER_REAL')
    monkeypatch.setenv('CUSTOM_PASSWORD', 'FAKE_NEVER_REAL')
    assert not any(k in win.safe_environment() for k in ('OPENAI_API_KEY','ANTHROPIC_AUTH_TOKEN','CUSTOM_PASSWORD'))
    assert win.safe_environment()['PYTHON_DOTENV_DISABLED'] == '1'
    from conftest import pytest_sessionstart
    pytest_sessionstart(None)
    assert all(k not in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_AUTH_TOKEN','CUSTOM_PASSWORD'))


def test_child_process_sees_no_inherited_credentials(monkeypatch,tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'FAKE_NEVER_REAL')
    text_file=tmp_path/'unicode.sql'; text_file.write_text("SELECT '中文 é'",encoding='utf-8')
    code="import os,sys; assert 'OPENAI_API_KEY' not in os.environ; assert sys.flags.utf8_mode==1; assert open(sys.argv[1]).read()==\"SELECT '\\u4e2d\\u6587 \\u00e9'\"; print('credential_isolation_ok')"
    result = win.run_process([sys.executable,'-c',code,str(text_file)],10)
    assert result['exit_code']==0 and result['reaped'] and 'credential_isolation_ok' in result['stdout']


@pytest.mark.parametrize('sql,expected,status', [
    ('SELECT amount FROM orders',1,'OK'),
    ('SELECT DISTINCT amount FROM orders UNION ALL SELECT amount FROM orders',1,'OK'),
    ('SELECT 999',0,'MISMATCH'),
    (FAILURE_SQL,0,'SQL_ERROR'),
    ('SELECT nonexistent FROM orders',0,'SQL_ERROR'),
    ('DROP TABLE orders',0,'UNSAFE_SQL'),
    ('ATTACH DATABASE \'external.sqlite\' AS ext',0,'UNSAFE_SQL'),
    ("SELECT load_extension('forbidden')",0,'UNSAFE_SQL'),
    ('SELECT 1; DELETE FROM orders',0,'UNSAFE_SQL'),
])
def test_spawn_official_scoring_and_safety(tiny_db,official_source,tmp_path,sql,expected,status):
    job=worker_job(tiny_db,official_source); job['prediction']=sql
    before=file_sha256(tiny_db[1])
    row=win.spawn_case(job,tmp_path/'result.json',10)
    assert row['res']==expected and row['status']==status and row['worker_reaped'] and row['worker_exit_code']==0
    assert file_sha256(tiny_db[1])==before


def test_real_sql_timeout_and_hard_timeout(tiny_db,official_source,tmp_path):
    job=worker_job(tiny_db,official_source)
    job.update(prediction='WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT SUM(x) FROM n',sql_timeout=0.05)
    before=file_sha256(tiny_db[1])
    row=win.spawn_case(job,tmp_path/'timeout.json',5)
    assert row['res']==0 and row['status']=='SQL_TIMEOUT'
    assert row['worker_reaped']
    row=win.spawn_case(job,tmp_path/'hard_timeout.json',0.001)
    assert row['res']==0 and row['status']=='WORKER_HARD_TIMEOUT' and row['worker_reaped']
    assert file_sha256(tiny_db[1])==before


def test_worker_exception_is_zero_and_reaped(tiny_db,official_source,tmp_path):
    job=worker_job(tiny_db,official_source); job['db_path']=str(tmp_path/'absent.sqlite')
    row=win.spawn_case(job,tmp_path/'absent.json',10)
    assert row['res']==0 and row['status']=='WORKER_ERROR' and row['worker_exit_code']!=0 and row['worker_reaped']


@pytest.mark.parametrize('mode', ['normal','timeout','error'])
def test_outer_process_exit_timeout_and_exception(mode):
    code={'normal':"print('fixture success')",'timeout':'import time; time.sleep(30)',
          'error':"raise RuntimeError('fixture exception')"}[mode]
    result=win.run_process([sys.executable,'-c',code],0.2 if mode=='timeout' else 10)
    assert result['reaped']
    assert result['timed_out']==(mode=='timeout')
    assert (result['exit_code']==0)==(mode=='normal')
    if mode=='error': assert 'fixture exception' in result['stderr']


def test_outer_timeout_kills_descendant_tree(tmp_path):
    if os.name!='nt': pytest.skip('Windows taskkill process-tree check')
    marker=tmp_path/'child.pid'
    code="import subprocess,sys,time; from pathlib import Path; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); Path(sys.argv[1]).write_text(str(c.pid)); time.sleep(30)"
    result=win.run_process([sys.executable,'-c',code,str(marker)],1)
    assert result['timed_out'] and result['reaped'] and marker.exists()
    import subprocess
    check=subprocess.run(['pwsh','-NoProfile','-Command',f"if (Get-Process -Id {int(marker.read_text())} -ErrorAction SilentlyContinue) {{ exit 1 }}"],capture_output=True,timeout=10)
    assert check.returncode==0


def test_external_services_blocked(official_source):
    code="from bird_starter.official_ex_windows import install_network_guard; import socket,pymysql,psycopg2; install_network_guard(); funcs=[lambda:socket.create_connection(('127.0.0.1',1)),pymysql.connect,psycopg2.connect]; n=0\nfor f in funcs:\n try:f()\n except PermissionError:n+=1\nassert n==3"
    result=win.run_process([sys.executable,'-c',code],10)
    assert result['exit_code']==0


def test_source_hash_tampering_blocks_before_import(tmp_path,official_source):
    import shutil
    for name in win.SOURCE_SHA256: shutil.copyfile(official_source/name,tmp_path/name)
    (tmp_path/'evaluation_ex.py').write_text('raise RuntimeError("not official")')
    with pytest.raises(ValueError,match='SHA256'): win.load_official(tmp_path)


@pytest.mark.parametrize('changed',[False,True])
def test_readiness_requires_intact_local_validation_evidence(tmp_path,official_source,changed):
    from bird_starter import config
    from bird_starter.official_ex_inputs import engine_readiness, OFFICIAL_COMMIT
    # conftest points config.PROJECT_ROOT at tmp_path; this is only a gate fixture.
    evidence=tmp_path/'outputs/official_ex_windows_checks/validation.json'
    evidence.parent.mkdir(parents=True)
    artifact=tmp_path/'fixture_result.json'; artifact.write_text('fixture only')
    certificate=dict(acceptance='PASS',official_commit=OFFICIAL_COMMIT,source_sha256=win.SOURCE_SHA256,
                     originals_unchanged=True,remote_model_requests=0,
                     verified_files={'fixture_result.json':file_sha256(artifact)})
    evidence.write_text(json.dumps(certificate))
    if changed: artifact.write_text('changed')
    result=engine_readiness()
    assert result['official_ex'] is None
    assert result['status']==('BLOCKED_END_TO_END_UNVERIFIED' if changed else 'PASS_WINDOWS_SANDBOX_CONTROLS')


def test_control_format_fixed_failure_denominator(tmp_path,suite_files,monkeypatch):
    import bird_starter.experiment_review as review
    monkeypatch.setattr(review,'PRIVATE_OUTPUT_ROOT',tmp_path)
    config=json.loads(suite_files[0].read_text()); manifest=Path(config['manifest'])
    root=prepare_controls(suite_files[1],manifest,tmp_path/'controls')
    for scenario in ('gold','invalid','missing'):
        assert validate_bundle(root/scenario)['aligned_cases']==3
    assert json.loads((root/'missing/predictions.json').read_text())['0'].startswith(FAILURE_SQL)
    with pytest.raises(FileExistsError): prepare_controls(suite_files[1],manifest,tmp_path/'controls')


@pytest.mark.parametrize('scenario,correct',[('gold',3),('invalid',2),('missing',2)])
def test_complete_official_fixture_reconciliation(tmp_path,suite_files,official_source,monkeypatch,scenario,correct):
    import bird_starter.experiment_review as review
    monkeypatch.setattr(review,'PRIVATE_OUTPUT_ROOT',tmp_path)
    monkeypatch.setattr(win,'install_network_guard',lambda:None) # test socket guard already denies all connections
    config=json.loads(suite_files[0].read_text()); manifest=Path(config['manifest'])
    controls=prepare_controls(suite_files[1],manifest,tmp_path/'controls')
    before=file_sha256(suite_files[2]/'demo/demo.sqlite')
    result=win.score_bundle(controls/scenario,suite_files[2],tmp_path/'scored',manifest=manifest)
    assert result['correct']==correct and result['planned_cases']==3
    assert result['official_counts']==[1,1,1,3]
    assert sum(x['correct'] for x in result['by_difficulty'].values())==correct
    assert result['official_ex']==correct/3 and not (tmp_path/'scored/database_copies').exists()
    assert file_sha256(suite_files[2]/'demo/demo.sqlite')==before


def test_manifest_mismatch_blocked_before_database_access(tmp_path,suite_files,official_source,monkeypatch):
    import bird_starter.experiment_review as review
    monkeypatch.setattr(review,'PRIVATE_OUTPUT_ROOT',tmp_path)
    monkeypatch.setattr(win,'install_network_guard',lambda:None)
    config=json.loads(suite_files[0].read_text()); manifest=Path(config['manifest'])
    controls=prepare_controls(suite_files[1],manifest,tmp_path/'controls')
    manifest.write_text(manifest.read_text()+' ')
    with pytest.raises(ValueError,match='Manifest'):
        win.score_bundle(controls/'gold',suite_files[2],tmp_path/'scored',manifest=manifest)
    assert not (tmp_path/'scored').exists()


def test_no_output_overwrite(tmp_path):
    with pytest.raises(FileExistsError): win.run_isolated(tmp_path,tmp_path,tmp_path)


@pytest.mark.parametrize('value',[0,-1,float('inf'),float('nan')])
def test_invalid_outer_timeout(value):
    with pytest.raises(ValueError): win.run_process([sys.executable,'-c','pass'],value)
