"""Gold-only evaluator controls. Never imported by generation workflows."""
import json
from pathlib import Path

from .data import load_cases
from .experiment_review import reserve_private_output
from .experiment_sets import file_sha256
from .official_ex_inputs import DELIMITER, FAILURE_SQL, PROTOCOL, single_line_sql, validate_bundle
from .request_audit import write_new_json


def prepare_controls(cases_path, manifest_path, out_dir):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if file_sha256(cases_path) != manifest['dataset_sha256']:
        raise ValueError('Control dataset SHA mismatch')
    cases = {c['case_id']: c for c in load_cases(cases_path)}
    metadata = manifest['cases']
    if len({m['case_id'] for m in metadata}) != len(metadata):
        raise ValueError('Duplicate control identity')
    gold = []
    for m in metadata:
        c = cases[m['case_id']]
        if c['db_id'] != m['db_id'] or c['difficulty'] != m['difficulty']:
            raise ValueError('Control case/database/difficulty mismatch')
        gold.append(single_line_sql(c['SQL']))
    output = reserve_private_output(out_dir)
    for scenario in ('gold', 'invalid', 'missing'):
        folder = output / scenario
        folder.mkdir()
        predictions, alignment = {}, []
        for i, m in enumerate(metadata):
            failed = scenario != 'gold' and i == 0
            predictions[str(i)] = (FAILURE_SQL if failed else gold[i]) + DELIMITER + m['db_id']
            alignment.append(dict(index=i, case_id=m['case_id'], db_id=m['db_id'], forced_failure=failed,
                                  reason=('missing_or_not_run' if scenario == 'missing' else 'invalid_prediction') if failed else None))
        write_new_json(folder / 'predictions.json', predictions)
        (folder / 'gold.sql').write_text(''.join(gold[i] + '\t' + m['db_id'] + '\n' for i, m in enumerate(metadata)), encoding='utf-8')
        (folder / 'difficulty.jsonl').write_text(''.join(json.dumps(m) + '\n' for m in metadata), encoding='utf-8')
        bundle = dict(protocol=PROTOCOL, planned_cases=len(metadata), alignment=alignment,
                      measurement_kind='OFFICIAL_EVALUATOR_CONTROL_NOT_MODEL_QUALITY',
                      dataset_sha256=manifest['dataset_sha256'], manifest_sha256=file_sha256(manifest_path),
                      source_result_sha256=None, official_ex=None,
                      notice='Gold predictions are independent evaluator controls, never Agent inputs.',
                      files_sha256={p.name:file_sha256(p) for p in folder.iterdir()})
        write_new_json(folder / 'bundle.json', bundle)
        validate_bundle(folder)
    return output
