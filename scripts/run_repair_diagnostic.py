"""Independent repair-only diagnostics. Default dry-run; no generator or EX."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bird_starter.repair_diagnostic import ROOT,run_diagnostic
from bird_starter.data import dataset_paths


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default=str(ROOT/'configs/phase3c/repair_diagnostic_v1.json'))
    p.add_argument('--db-root',default=None)
    p.add_argument('--out-dir',required=True)
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--mock-llm',action='store_true')
    mode.add_argument('--live',action='store_true')
    p.add_argument('--allow-api',action='store_true')
    p.add_argument('--max-model-calls',type=int,default=8)
    p.add_argument('--mock-scenario',choices=('correct','still_wrong','dangerous','semantic_wrong','empty','length'),default='correct')
    a=p.parse_args()
    if a.live!=a.allow_api: p.error('Live diagnostic requires new explicit --live --allow-api authorization')
    try:
        _,db_root=dataset_paths(db_root=a.db_root)
        result=run_diagnostic(a.config,db_root,a.out_dir,execution='live' if a.live else 'mock' if a.mock_llm else 'dry-run',
                              allow_api=a.allow_api,max_calls=a.max_model_calls,mock_scenario=a.mock_scenario)
    except Exception as exc:
        p.error('Diagnostic rejected ('+type(exc).__name__+'); preserve files, do not retry or overwrite')
    print('Saved independent diagnostic:',result['run_id'],'calls:',result['model_calls'],'kind:',result['measurement_kind'],'official EX: NOT APPLICABLE')
    return 0 if result['complete'] else 1


if __name__=='__main__':
    raise SystemExit(main())
