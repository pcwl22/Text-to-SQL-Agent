"""Offline Windows sandbox for the pinned, unmodified official EX scorer.

Gold inputs live only in this evaluator. Each case uses a spawn worker; a
separate CLI subprocess provides a second, whole-run hard timeout boundary.
"""
import hashlib
import importlib.util
import json
import math
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

from .data import find_db
from .experiment_review import reserve_private_output
from .experiment_sets import file_sha256
from .official_ex_inputs import OFFICIAL_COMMIT, DELIMITER, FAILURE_SQL, validate_bundle
from .request_audit import write_new_json
from .tools import check_read_only_sql, ro_connect, _query_authorizer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'outputs' / 'official_ex_source' / OFFICIAL_COMMIT
DEFAULT_MANIFEST = ROOT / 'configs/phase3a/bird_dev33_v1.json'
SOURCE_SHA256 = {
    'evaluation_ex.py': 'da1bbcd4530be83692d7c650c814ea9704bb710d0c953eb75d02ccb38233cf89',
    'evaluation_utils.py': 'f6943d249caac5aeaef9bce21d43dbf29dcef85a0c965a76df032a9542f308bf',
}


class WindowsJob:
    """Kernel-enforced child-tree lifetime; no taskkill permission dependency."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                        ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                        ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n,ctypes.c_uint64) for n in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Limits(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                        ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        limits=Limits(); limits.basic.flags=0x2000 # KILL_ON_JOB_CLOSE
        try:
            if not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle,1):
            raise OSError('Unable to terminate evaluator job')

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle=None


def safe_environment():
    # Whitelist instead of copying os.environ then trying to redact secrets.
    names = ('SystemRoot', 'WINDIR', 'PATH', 'PATHEXT', 'COMSPEC', 'TEMP', 'TMP')
    env = {n: os.environ[n] for n in names if n in os.environ}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHON_DOTENV_DISABLED='1',
               LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')
    return env


def verify_source(folder):
    folder = Path(folder).resolve()
    for name, expected in SOURCE_SHA256.items():
        if file_sha256(folder / name) != expected:
            raise ValueError('Official source SHA256 mismatch')
    return folder


def load_official(folder):
    folder = verify_source(folder)
    modules = []
    for name in ('evaluation_utils', 'evaluation_ex'):
        spec = importlib.util.spec_from_file_location(name, folder / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def deny_network(*args, **kwargs):
    raise PermissionError('External services disabled in offline EX sandbox')


def install_network_guard():
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network
    import pymysql
    import psycopg2
    pymysql.connect = deny_network
    psycopg2.connect = deny_network


def _case_worker(job, result_path):
    """Spawn-only entry point. Never construct Model or load dotenv."""
    install_network_guard()
    utils, official = load_official(job['source'])
    db = Path(job['db_path']).resolve(strict=True)
    copy_root = Path(job['copy_root']).resolve(strict=True)
    if not db.is_relative_to(copy_root):
        raise ValueError('Worker database escaped private copy root')
    started = time.monotonic()
    detail = {'status': 'OK', 'connections': 0, 'query_only': None}

    def connect(sql_dialect, db_path):
        if sql_dialect != 'SQLite' or Path(db_path).resolve() != db:
            raise ValueError('Only assigned SQLite copy is permitted')
        conn = ro_connect(db)
        conn.enable_load_extension(False)
        if conn.execute('PRAGMA query_only').fetchone() != (1,):
            conn.close()
            raise RuntimeError('Read-only connection check failed')
        detail['query_only'] = True
        conn.set_authorizer(_query_authorizer)
        conn.set_progress_handler(lambda: int(time.monotonic() - started > job['sql_timeout']), 1000)
        detail['connections'] += 1
        return conn

    original_execute = utils.execute_sql

    def execute(*args):
        try:
            return original_execute(*args)
        except Exception:
            detail['status'] = 'SQL_TIMEOUT' if time.monotonic() - started >= job['sql_timeout'] else 'SQL_ERROR'
            raise

    # Compatibility hooks only. Official execute_sql/calculate_ex and aggregation
    # bodies stay byte-identical. The execute wrapper preserves return/exception.
    utils.connect_db = connect
    official.execute_sql = execute
    original_timeout = official.func_timeout

    def timeout_call(*args, **kwargs):
        try:
            return original_timeout(*args, **kwargs)
        except official.FunctionTimedOut:
            detail['status'] = 'SQL_TIMEOUT'
            raise

    official.func_timeout = timeout_call
    try:
        if job['prediction'] != FAILURE_SQL:
            check_read_only_sql(job['prediction'])
        check_read_only_sql(job['gold'])
    except Exception:
        row = {'sql_idx': job['index'], 'res': 0}
        detail['status'] = 'UNSAFE_SQL'
    else:
        row = official.execute_model(job['prediction'], job['gold'], str(db), job['index'], job['sql_timeout'], 'SQLite')
        if detail['status'] == 'OK' and row['res'] == 0:
            detail['status'] = 'MISMATCH'
    row.update(detail)
    row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 2)
    write_new_json(result_path, row)


def spawn_case(job, result_path, hard_timeout):
    """No retries. Kill and join a worker that exceeds the hard wall clock."""
    if not math.isfinite(hard_timeout) or hard_timeout <= 0:
        raise ValueError('Invalid worker timeout')
    process = mp.get_context('spawn').Process(target=_case_worker, args=(job, str(result_path)))
    started = time.monotonic()
    process.start()
    pid = process.pid
    process.join(hard_timeout)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join(5)
        if process.is_alive():
            process.kill()
            process.join(5)
    if process.is_alive():
        raise RuntimeError('Unable to reap worker')
    exit_code = process.exitcode
    process.close()
    if timed_out:
        result = {'sql_idx': job['index'], 'res': 0, 'status': 'WORKER_HARD_TIMEOUT'}
    elif exit_code != 0 or not Path(result_path).exists():
        result = {'sql_idx': job['index'], 'res': 0, 'status': 'WORKER_ERROR'}
    else:
        result = json.loads(Path(result_path).read_text(encoding='utf-8'))
    result.update(worker_pid=pid, worker_exit_code=exit_code, worker_reaped=True,
                  wall_ms=round((time.monotonic() - started) * 1000, 2))
    return result


def score_bundle(bundle_dir, db_root, out_dir, *, source=DEFAULT_SOURCE,
                 sql_timeout=30.0, worker_timeout=40.0, manifest=DEFAULT_MANIFEST):
    """Run inside an isolated CLI process; returns fixed-denominator official EX."""
    if not (0 < sql_timeout < worker_timeout <= 600) or not all(map(math.isfinite, (sql_timeout, worker_timeout))):
        raise ValueError('Require finite 0 < SQL timeout < worker timeout <= 600')
    validate_bundle(bundle_dir)
    source = verify_source(source)
    install_network_guard()
    utils, official = load_official(source)
    bundle_dir = Path(bundle_dir).resolve()
    bundle = json.loads((bundle_dir / 'bundle.json').read_text(encoding='utf-8'))
    n = bundle['planned_cases']
    cohort = json.loads(Path(manifest).read_text(encoding='utf-8'))
    difficulties = utils.load_jsonl(str(bundle_dir / 'difficulty.jsonl'))
    expected = cohort['cases']
    if (file_sha256(manifest) != bundle['manifest_sha256'] or len(expected) != n
            or cohort['dataset_sha256'] != bundle['dataset_sha256']
            or any(expected[i] != difficulties[i] for i in range(n))):
        raise ValueError('Pinned Manifest/cohort mismatch')
    if n <= 0:
        raise ValueError('Empty evaluation prohibited')
    output = reserve_private_output(out_dir)
    copies = output / 'database_copies'
    copies.mkdir()
    paths, before, copied = {}, {}, {}
    try:
        for item in bundle['alignment']:
            db_id = item['db_id']
            if db_id in paths:
                continue
            original = find_db(db_root, db_id).resolve()
            # Private copy must never resolve into or overwrite the source tree.
            if copies.resolve().is_relative_to(Path(db_root).resolve()):
                raise ValueError('Copies may not be stored inside original database root')
            before[db_id] = file_sha256(original)
            dest = copies / db_id / (db_id + '.sqlite')
            dest.parent.mkdir()
            shutil.copyfile(original, dest)
            copied[db_id] = file_sha256(dest)
            if copied[db_id] != before[db_id]:
                raise ValueError('Database copy hash mismatch')
            paths[db_id] = original
        # Use actual official input readers. Pre-validation prevents zip truncation
        # and the upstream financial fallback. No zip is used in this runner.
        predictions, _ = utils.package_sqls(str(bundle_dir / 'predictions.json'), str(copies) + os.sep, mode='pred')
        gold, db_places = utils.package_sqls(str(bundle_dir / 'gold.sql'), str(copies) + os.sep, mode='gt')
        if len(predictions) != n or len(gold) != n or len(db_places) != n:
            raise ValueError('Official reader count mismatch')
        rows = []
        for index in range(n):
            item = bundle['alignment'][index]
            if Path(db_places[index]).resolve() != (copies / item['db_id'] / (item['db_id'] + '.sqlite')).resolve():
                raise ValueError('Official reader database mismatch')
            job = {'source': str(source), 'db_path': db_places[index], 'copy_root': str(copies),
                   'index': index, 'prediction': predictions[index], 'gold': gold[index], 'sql_timeout': sql_timeout}
            row = spawn_case(job, output / f'worker_{index}.json', worker_timeout)
            row.update(case_id=item['case_id'], db_id=item['db_id'])
            rows.append(row)
            with (output / 'results.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(row) + '\n'); stream.flush(); os.fsync(stream.fileno())
        if [r['sql_idx'] for r in rows] != list(range(n)):
            raise ValueError('Official result order/count mismatch')
        scores = official.compute_acc_by_diff(rows, str(bundle_dir / 'difficulty.jsonl'))
        by_difficulty = {d: {'count': sum(r['difficulty'] == d for r in difficulties),
                              'correct': sum(rows[i]['res'] for i in range(n) if difficulties[i]['difficulty'] == d)}
                         for d in ('simple', 'moderate', 'challenging')}
        official.print_data(list(scores[:4]), scores[4], metric='EX', result_log_file=str(output / 'official_stdout_table.txt'))
        count = sum(r['res'] for r in rows)
        assert scores[4][-1] == n and abs(scores[3] - count / n * 100) < 1e-9
        summary = {'status': 'COMPLETED', 'planned_cases': n, 'correct': count, 'official_ex': count / n,
                   'official_percentages': list(scores[:4]), 'official_counts': scores[4], 'by_difficulty': by_difficulty,
                   'protocol': bundle['protocol'], 'measurement_kind': bundle['measurement_kind'],
                   'manifest_sha256': bundle['manifest_sha256'], 'source_result_sha256': bundle['source_result_sha256'],
                   'official_commit': OFFICIAL_COMMIT, 'official_source_sha256': SOURCE_SHA256,
                   'num_cpus': 1, 'start_method': 'spawn', 'sql_timeout': sql_timeout, 'worker_timeout': worker_timeout,
                   'scoring_logic_modified': False, 'rows': rows, 'copy_sha256_before': copied,
                   'copy_sha256_after': {k: file_sha256(copies/k/(k+'.sqlite')) for k in paths}}
    finally:
        after = {k: file_sha256(p) for k, p in paths.items()}
        write_new_json(output / 'database_integrity.json', {'original_before': before, 'original_after': after,
                       'originals_unchanged': before == after})
        if before != after:
            raise RuntimeError('Original database integrity failure')
        # All child processes were joined before any copy cleanup.
        shutil.rmtree(copies)
    if summary['copy_sha256_before'] != summary['copy_sha256_after']:
        raise RuntimeError('Read-only evaluation copy changed')
    write_new_json(output / 'summary.json', summary)
    return summary


def run_process(command, timeout):
    """Capture bounded-lifetime process output; kill full Windows tree on timeout."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Invalid outer timeout')
    child = subprocess.Popen(command, env=safe_environment(), cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
    timed_out = False
    job = None
    try:
        try:
            if os.name == 'nt':
                job = WindowsJob(child)
            stdout, stderr = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if job is not None: job.terminate()
            else: child.kill()
            stdout, stderr = child.communicate(timeout=15)
    except BaseException:
        if job is not None: job.close()
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=15)
        raise
    finally:
        if job is not None: job.close()
    return {'exit_code': child.returncode, 'timed_out': timed_out, 'stdout': stdout, 'stderr': stderr,
            'pid': child.pid, 'reaped': child.poll() is not None}


def run_isolated(bundle_dir, db_root, out_dir, *, source=DEFAULT_SOURCE,
                 sql_timeout=30.0, worker_timeout=40.0, outer_timeout=1500.0, manifest=DEFAULT_MANIFEST):
    target = Path(out_dir).resolve()
    if target.exists() or target.with_name(target.name + '.process.json').exists():
        raise FileExistsError('Evaluation destination already exists')
    if not target.is_relative_to((ROOT / 'outputs').resolve()):
        raise ValueError('Evaluation outputs must remain private')
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT/'scripts/run_official_ex.py'), '--child', '--bundle', str(Path(bundle_dir).resolve()),
               '--db-root', str(Path(db_root).resolve()), '--out-dir', str(target), '--source', str(Path(source).resolve()),
               '--sql-timeout', str(sql_timeout), '--worker-timeout', str(worker_timeout)]
    command += ['--manifest', str(Path(manifest).resolve())]
    result = run_process(command, outer_timeout)
    write_new_json(target.with_name(target.name + '.process.json'), result)
    return result
