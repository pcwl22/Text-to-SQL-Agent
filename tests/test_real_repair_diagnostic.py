"""Explicit real BIRD SQLite + Mock Repair integration; NOT official EX/model quality."""
import json
import os
from pathlib import Path

import pytest

from bird_starter.repair_diagnostic import run_diagnostic
from bird_starter.experiment_sets import file_sha256


@pytest.mark.bird_integration
def test_real_hand_authored_repair_diagnostic():
    if os.environ.get('BIRD_RUN_INTEGRATION')!='1':
        pytest.skip('Real diagnostic integration requires actual downloaded SQLite data')
    root=Path(__file__).resolve().parents[1]
    config=root/'configs/phase3c/repair_diagnostic_v1.json'
    plan=json.loads(config.read_text(encoding='utf-8'))
    out=Path(os.environ['BIRD_DIAGNOSTIC_REPORT_DIR']) if os.environ.get('BIRD_DIAGNOSTIC_REPORT_DIR') else root/'outputs'/('diagnostic_pytest_'+__import__('uuid').uuid4().hex)
    digest=file_sha256(config)
    s=run_diagnostic(config,Path(os.environ['BIRD_DB_ROOT']),out,execution='mock')
    assert s['planned_positive_cases']==8 and s['execution_successes']==s['semantic_assertion_passes']==8
    assert s['negative_cases']==s['negative_rejections']==4 and s['model_calls']==8
    assert s['measurement_kind']=='OFFLINE_MOCK_NOT_MODEL_QUALITY' and s['official_ex'] is None
    assert s['request_audit']['prepared_attempts']==8 and s['remote_api_requests']==0
    integrity=json.loads((out/'integrity.json').read_text())
    assert integrity['sqlite_unchanged'] and integrity['historical_files_unchanged']
    assert len(integrity['database_after'])==11 and len(plan['schemas'])==8
    assert file_sha256(config)==digest
