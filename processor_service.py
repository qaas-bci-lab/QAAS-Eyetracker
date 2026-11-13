"""Offline experiment-only discovery, processing and participant/file mapping."""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from coordinates import session_mode, TXT_FIELDS, EXPERIMENT_TXT_FIELDS, validate_utc_text
from record_layout import read_result, finalize_experiment, experiment_segment
from processing_hardware import resolve_device, plan_execution


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_RECORDS = set()
_ACTIVE_BATCHES = 0


def _record_key(folder):
    return Path(os.path.normcase(os.path.abspath(folder)))


@contextmanager
def _claim_record(folder):
    """Fail without waiting on a simultaneous duplicate or nested record."""
    key = _record_key(folder)
    with _ACTIVE_LOCK:
        if any(key == other or key in other.parents or other in key.parents for other in _ACTIVE_RECORDS):
            raise ValueError('本次记录或其关联目录正在处理，请等待完成后再试')
        _ACTIVE_RECORDS.add(key)
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_RECORDS.discard(key)


@contextmanager
def _batch_threads(jobs, cpu_threads=None):
    """Configure only while idle, including the interval before workers claim paths."""
    global _ACTIVE_BATCHES
    plan = plan_execution('cpu', jobs=jobs, cpu_threads=cpu_threads)
    with _ACTIVE_LOCK:
        if cpu_threads is not None and (_ACTIVE_BATCHES or _ACTIVE_RECORDS):
            raise ValueError('已有处理任务正在运行，请完成后再应用新的 CPU 推理线程预算')
        if not _ACTIVE_BATCHES and not _ACTIVE_RECORDS:
            from affnet_tracker import configure_torch_threads
            configure_torch_threads(plan['threads_per_job'])
        _ACTIVE_BATCHES += 1
    try:
        yield
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE_BATCHES -= 1


SKIP_DIRECTORIES = {'.git', '.venv', 'venv', '__pycache__', '.cache', '.packager',
                    '.packager_tool', 'models', 'vendor', 'assets', 'backups',
                    'release_installer', 'release_single', 'node_modules'}


def _linked(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0)
                                        & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
    except OSError:
        return True


def _json(path):
    result = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('记录信息格式不正确')
    return result


def _record_folder(folder):
    folder = Path(folder).expanduser()
    if _linked(folder) or not folder.is_dir():
        raise ValueError('记录目录不可用或包含链接')
    folder = folder.resolve()
    # Inference, calibration and report writers all work inside this directory.
    # Reject linked members before any of them can follow a pre-existing target.
    for member in folder.iterdir():
        if _linked(member):
            raise ValueError('记录包含链接，无法安全处理')
    for calibration_name in ('calibration_raw', 'pre_calibration', 'post_calibration'):
        calibration = folder / calibration_name
        if calibration.is_dir():
            for current, directories, files in os.walk(calibration, followlinks=False):
                for name in directories + files:
                    if _linked(Path(current) / name):
                        raise ValueError('校准素材包含链接，无法安全处理')
    return folder


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _local_output(folder, value):
    """A moved recording can use its original output basenames, never other folders."""
    if not isinstance(value, str) or not value:
        raise ValueError('结果文件路径缺失')
    # Handle recorded Windows names even when a directory was moved to another host.
    name = value.replace('\\', '/').rsplit('/', 1)[-1]
    target = folder / name
    if not name or name in ('.', '..') or _linked(target) or not target.is_file():
        raise ValueError('结果文件不存在')
    return target


def completed_result(folder):
    """Only verified full-session exports count as complete, so stale partials stay visible."""
    try:
        folder = _record_folder(folder)
        meta = _json(folder / 'session.json')
        result = read_result(folder)
        if not isinstance(result, dict):
            return None
        if result.get('mode') != 'experiment' or result.get('is_full_session') is not True:
            return None
        data = _local_output(folder, result.get('txt_path') or (meta.get('files') or {}).get('data'))
        participant = None
        exported = result.get('coordinate_export')
        if not isinstance(exported, dict) or exported.get('verified') is not True:
            return None
        if (exported.get('rows') != result.get('export_rows') or exported.get('sample_hz') != 20
                or result.get('sample_hz') != exported.get('sample_hz')):
            return None
        digest = exported.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest) or _sha(data) != digest.lower():
            return None
        if isinstance(meta.get('result'), dict):
            if (meta.get('files') or {}).get('data') != data.name or not isinstance(meta.get('participant'), dict):
                return None
        else:
            participant = _local_output(folder, result.get('participant_txt_path'))
            digest = exported.get('participant_sha256')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest) or _sha(participant) != digest.lower():
                return None
            with participant.open(encoding='utf-8-sig', newline='') as file:
                fields = dict(csv.reader(file, delimiter='\t'))
            if fields.get('数据文件') != data.name:
                return None
        count = 0
        previous_time = -math.inf
        with data.open(encoding='utf-8-sig', newline='') as file:
            reader = csv.reader(file, delimiter='\t')
            fields = tuple(next(reader, []))
            if fields not in (TXT_FIELDS, EXPERIMENT_TXT_FIELDS):
                return None
            for index, row in enumerate(reader):
                if len(row) != len(fields):
                    return None
                values = dict(zip(fields, row))
                sparse = fields == EXPERIMENT_TXT_FIELDS
                if sparse and (values['timestamp_utc'] == 'NaN' or not validate_utc_text(values['timestamp_utc'])):
                    return None
                timestamp = float(values['time_s'])
                if not math.isfinite(timestamp):
                    return None
                if sparse:
                    if timestamp < 0 or timestamp <= previous_time or abs(timestamp * 20 - round(timestamp * 20)) > 1e-6:
                        return None
                elif abs(timestamp - index / 20.) > .00001:
                    return None
                previous_time = timestamp
                for field in TXT_FIELDS[1:]:
                    value = values[field]
                    if value.lower() != 'nan' and not math.isfinite(float(value)):
                        return None
                count += 1
        if count <= 0 or count != result.get('export_rows') or result.get('sample_hz') != 20:
            return None
        return result | {'txt_path': str(data), 'participant_txt_path': str(participant) if participant else '',
                         'participant_json_path': str(folder / 'session.json'),
                         'output_dir': str(folder)}
    except (OSError, ValueError, TypeError, csv.Error):
        return None


def load_record(folder):
    folder = _record_folder(folder)
    meta = _json(folder / 'session.json')
    if session_mode(meta) != 'experiment':
        raise ValueError('数据处理版仅处理实验记录')
    person = meta.get('participant') if isinstance(meta.get('participant'), dict) else {}
    segment = experiment_segment(meta)
    identifier = person.get('participant_id')
    scheme = meta.get('scheme') if isinstance(meta.get('scheme'), dict) else {}
    result = completed_result(folder)
    from experiment_visuals import visual_products
    visuals = visual_products(folder, verify_hashes=True) if result else None
    raw = (folder / 'camera.mp4').is_file()
    status = '已完成' if result else '待处理'
    error = ''
    unfinished = meta.get('acquisition_state') == 'calibration_only' or meta.get('actual_duration_s') == 0
    if unfinished:
        status = '未完成采集'
    if result and (result.get('valid_predictions', 0) < 1 or result.get('export_rows', 0) <= result.get('missing_samples', 0)):
        status = '无有效坐标'
        error = '；'.join(str(reason) for reason in (result.get('invalid_reasons') or {})) or '本次预测没有有效坐标'
    if not result or not visuals:
        try:
            prior = meta.get('processing_status') or _json(folder / 'processor_status.json')
            if prior.get('status') == 'failed':
                status, error = '处理失败', str(prior.get('error') or '')
        except (OSError, ValueError):
            pass
    return dict(path=str(folder), name=str(person.get('name') or ''),
                participant_id='' if identifier is None else str(identifier), sex=str(person.get('sex') or ''),
                age=person.get('age') if person.get('age') is not None else '',
                created=str(meta.get('created_local') or folder.name),
                scheme_name=str(scheme.get('name') or '实验'), model_id=str(meta.get('model_id') or scheme.get('model_id') or ''),
                status=status, complete=result is not None,
                processable=not unfinished and (result is None or visuals is None),
                has_raw=raw, error=error,
                txt_path=result['txt_path'] if result else '',
                participant_txt_path=result['participant_txt_path'] if result else '',
                participant_json_path=str(folder / 'session.json'),
                export_rows=result.get('export_rows', 0) if result else 0,
                visuals_complete=visuals is not None,
                distribution_path=visuals['distribution_path'] if visuals else '',
                video_path=visuals['video_path'] if visuals else '',
                poster_path=visuals['poster_path'] if visuals else '', **segment)


def segment_label(record):
    segment = experiment_segment(record)
    return f"第 {segment['segment_index']} / {segment['segment_count']} 段" if segment else ''


def scan_sessions(root):
    root = Path(root).expanduser()
    if _linked(root) or not root.is_dir():
        raise ValueError('请选择有效的原始数据文件夹')
    root = root.resolve()
    records = []
    for current, directories, files in os.walk(root, followlinks=False):
        current = Path(current)
        directories[:] = [name for name in directories
                          if name.casefold() not in SKIP_DIRECTORIES and not name.startswith('.')
                          and not _linked(current / name)]
        if 'session.json' not in files or _linked(current / 'session.json'):
            continue
        directories[:] = []
        try:
            records.append(load_record(current))
        except (OSError, ValueError, TypeError):
            continue
    groups = {}
    for row in sorted(records, key=lambda row: (row['created'], row['path']), reverse=True):
        # Keep the latest experiment first and its segments together in acquisition order.
        key = ('experiment', row['experiment_session_id']) if 'experiment_session_id' in row else ('path', row['path'])
        groups.setdefault(key, []).append(row)
    return [row for group in groups.values() for row in sorted(group, key=lambda row: row.get('segment_index', 0))]


def _save_status(folder, status, error=''):
    payload = dict(status=status, error=error, updated=datetime.now().astimezone().isoformat())
    from record_layout import save_processing_status
    try:
        save_processing_status(folder, payload)
    except OSError:
        pass


def process_record(folder, retain_raw_rgb=True, on_progress=None, *, device='cpu', batch_size=None, cpu_threads=None):
    """Keep the original session model and skip already verified complete records."""
    folder = _record_folder(folder)
    if cpu_threads is not None:
        plan = plan_execution(device, jobs=1, cpu_threads=cpu_threads)
        with _batch_threads(1, plan['cpu_threads_budget']):
            with _claim_record(folder):
                return _process_record(folder, retain_raw_rgb, on_progress, device=device, batch_size=batch_size)
    with _claim_record(folder):
        return _process_record(folder, retain_raw_rgb, on_progress, device=device, batch_size=batch_size)


def _process_record(folder, retain_raw_rgb, on_progress, *, device, batch_size):
    row = load_record(folder)
    emit = on_progress or (lambda percent, message: None)
    if row['complete']:
        try:
            from experiment_visuals import ensure_session_visuals
            products = ensure_session_visuals(folder, on_progress=emit)
            finalize_experiment(folder, completed_result(folder) | products)
            result = load_record(folder)
            _save_status(folder, 'complete')
            return result
        except Exception as error:
            _save_status(folder, 'failed', str(error))
            raise
    if not row['processable']:
        raise ValueError('本次记录未完成正式采集')
    try:
        from offline import infer_session
        from reporting import report
        inference_options = {}
        selected_device = resolve_device(device)
        if selected_device != 'cpu':
            inference_options['device'] = selected_device
        if batch_size is not None:
            inference_options['batch_size'] = batch_size
        infer_session(folder, progress=None, **inference_options,
                      on_progress=lambda percent, message: emit(round(percent * .8), message))
        report(folder, make_video=True, retain_raw_rgb=bool(retain_raw_rgb), compact_experiment=True,
               on_progress=lambda percent, message: emit(80 + round(percent * .2), message))
        result = load_record(folder)
        if not result['complete']:
            raise ValueError('数据 TXT 与人员信息校验未通过')
        _save_status(folder, 'complete')
        emit(100, '处理完成')
        return result
    except Exception as error:
        _save_status(folder, 'failed', str(error))
        raise


def process_batch(folders, retain_raw_rgb=True, *, on_progress=None, on_record=None, should_stop=None,
                  device='cpu', jobs=1, batch_size=None, cpu_threads=None):
    """Process independent records, preserving input order and finish-current stop."""
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 8:
        raise ValueError('同时处理数量必须为 1 至 8 的整数')
    selected_device = resolve_device(device)
    # Normalize lexical aliases but keep original paths until the linked-folder
    # checks inside process_record. Never resolve away a selected symbolic link.
    unique = {}
    for folder in folders:
        unique.setdefault(_record_key(folder), str(Path(folder).absolute()))
    folders = list(unique.values())
    keys = list(unique)
    if any(first in second.parents or second in first.parents
           for index, first in enumerate(keys) for second in keys[index + 1:]):
        raise ValueError('请勿同时选择一条记录及其内部子目录')
    if not folders:
        return []
    jobs = 1 if selected_device.startswith('cuda:') else min(jobs, len(folders))
    if selected_device.startswith('cuda:'):
        if cpu_threads is not None and (isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or cpu_threads < 1):
            raise ValueError('CPU 推理线程预算必须为正整数')
        cpu_threads = min(4, os.cpu_count() or 1)
    plan = plan_execution('cpu', jobs=jobs, cpu_threads=cpu_threads)
    jobs = plan['jobs']
    with _batch_threads(jobs, cpu_threads):
        return _run_batch(folders, retain_raw_rgb, on_progress=on_progress, on_record=on_record,
                          should_stop=should_stop, selected_device=selected_device, jobs=jobs,
                          batch_size=batch_size)


def _run_batch(folders, retain_raw_rgb, *, on_progress, on_record, should_stop,
               selected_device, jobs, batch_size):
    completed = {}
    percentages = [0.] * len(folders)
    progress_lock = threading.RLock()
    last_percent = 0

    def progress(index, percent, message):
        nonlocal last_percent
        with progress_lock:
            percentages[index] = max(percentages[index], min(100., max(0., float(percent))))
            overall = max(last_percent, round(sum(percentages) / len(folders)))
            last_percent = overall
            if on_progress:
                on_progress(overall, f'{index + 1} / {len(folders)}  {message}')

    options = {}
    if selected_device != 'cpu':
        options['device'] = selected_device
    if batch_size is not None:
        options['batch_size'] = batch_size

    def run(index):
        folder = folders[index]
        try:
            row = process_record(folder, retain_raw_rgb=retain_raw_rgb,
                                 on_progress=lambda percent, message: progress(index, percent, message), **options)
        except Exception as error:
            try:
                row = load_record(folder)
            except Exception:
                row = dict(path=folder, complete=False, txt_path='', participant_txt_path='')
            row.update(status='处理失败', error=str(error))
        progress(index, 100, row.get('status', '处理结束'))
        return row

    def finished(index, row):
        completed[index] = row
        if on_record:
            on_record(row)

    if jobs == 1:
        for index in range(len(folders)):
            if should_stop and should_stop():
                break
            finished(index, run(index))
    else:
        with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix='eye-record') as executor:
            running = {}
            next_index = 0
            while running or next_index < len(folders):
                while len(running) < jobs and next_index < len(folders):
                    if should_stop and should_stop():
                        break
                    future = executor.submit(run, next_index)
                    running[future] = next_index
                    next_index += 1
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: running[item]):
                    index = running.pop(future)
                    finished(index, future.result())
    return [completed[index] for index in sorted(completed)]


MAPPING_COLUMNS = [('name', '姓名'), ('participant_id', '编号'), ('sex', '性别'), ('age', '年龄'),
                   ('created', '实验时间'), ('scheme_name', '实验方案'), ('model_id', '模型'),
                   ('status', '处理状态'), ('txt_path', '数据TXT'), ('participant_json_path', '人员信息JSON'),
                   ('path', '记录文件夹'), ('experiment_session_id', '实验编号'),
                   ('segment_index', '段号'), ('segment_count', '总段数')]


def export_mapping(records, destination):
    destination = Path(destination).resolve()
    descriptor, temporary = tempfile.mkstemp(prefix='.participant_mapping_', suffix='.csv', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([title for _, title in MAPPING_COLUMNS])
            for record in records:
                writer.writerow([record.get(key, '') for key, _ in MAPPING_COLUMNS])
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination
