"""Portable experiment records with one JSON information file.

Legacy sidecars remain readable. Compaction only removes known files after their
contents and final products have been committed and read back successfully.
"""
from __future__ import annotations

import base64
import copy
import csv
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import tempfile

LAYOUT = 'compact_v1'
PAIRED_EXPERIMENT_LAYOUT = 'paired_v1'
_MAX_JSON = 128 * 1024 * 1024


def experiment_segment(meta):
    """Return compatible pairing fields for one independently processable segment."""
    identifier = meta.get('experiment_session_id')
    index, count = meta.get('segment_index'), meta.get('segment_count')
    if (not isinstance(identifier, str) or not identifier.strip()
            or type(index) is not int or type(count) is not int
            or count != 2 or not 1 <= index <= count):
        return {}
    result = dict(experiment_session_id=identifier, segment_index=index, segment_count=count)
    rest = meta.get('rest_duration_s')
    if type(rest) in (int, float) and math.isfinite(rest) and rest >= 0:
        result['rest_duration_s'] = rest
    return result


def _linked(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _folder(folder):
    path = Path(folder)
    if _linked(path) or not path.is_dir():
        raise ValueError('实验记录目录不可用或为链接')
    return path.resolve()


def _local(folder, name):
    path = folder / name
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('记录文件路径超出本次实验目录')
    for part in (path, *path.parents):
        if part == folder:
            break
        if _linked(part):
            raise ValueError('实验记录包含链接文件，无法安全读取或整理')
    if not path.resolve().is_relative_to(folder):
        raise ValueError('记录文件路径超出本次实验目录')
    return path


def _json(path):
    if _linked(path) or path.stat().st_size > _MAX_JSON:
        raise ValueError('实验信息文件路径或大小无效')
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('实验信息必须为对象')
    return value


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path, value):
    if _linked(path):
        raise ValueError('实验信息文件不能为链接')
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=path.parent,
                                         prefix='.session-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if _json(temporary) != value:
            raise ValueError('实验信息写入校验失败')
        if _linked(path):
            raise ValueError('实验信息文件不能为链接')
        temporary.replace(path)
        if _json(path) != value:
            raise ValueError('实验信息保存后校验失败')
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _merge(folder, changes):
    folder = _folder(folder)
    path = _local(folder, 'session.json')
    meta = _json(path)
    meta.update(changes)
    _atomic_json(path, meta)
    return meta


def _timeline_rows(value):
    if not isinstance(value, dict):
        raise ValueError('采集时间线格式无效')
    columns, rows = value.get('columns'), value.get('rows')
    if (not isinstance(columns, list) or not all(isinstance(key, str) and key for key in columns)
            or len(columns) != len(set(columns)) or not isinstance(rows, list)):
        raise ValueError('采集时间线格式无效')
    if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        raise ValueError('采集时间线列数不一致')
    return [dict(zip(columns, row)) for row in rows]


def read_capture_rows(folder, meta=None):
    folder = _folder(folder)
    meta = _json(_local(folder, 'session.json')) if meta is None else meta
    if 'capture_timeline' in meta:
        return _timeline_rows(meta['capture_timeline'])
    path = _local(folder, 'capture.csv')
    if not path.is_file():
        return []
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('原始采集时间线列数不一致')
    return rows


def has_capture_data(folder, meta=None):
    return bool(read_capture_rows(folder, meta))


def _embedded_stimulus(folder, meta):
    image_suffixes = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}
    value = meta.get('stimulus')
    if isinstance(value, dict) and value.get('base64'):
        try:
            raw = base64.b64decode(value['base64'], validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError('保存的实验背景图片无法解码') from error
        if hashlib.sha256(raw).hexdigest() != value.get('sha256'):
            raise ValueError('保存的实验背景图片校验未通过')
        name = value.get('filename') or ''
        supported = Path(name).suffix.lower() in image_suffixes
        duplicate = _local(folder, name) if name and supported else None
        if duplicate is not None and duplicate.is_file() and _sha(duplicate) == value['sha256']:
            return value, duplicate
        return value, None
    annotation = meta.get('annotation') or {}
    if not isinstance(annotation, dict):
        annotation = {}
    name = meta.get('image_path') or annotation.get('image') or ''
    mode = meta.get('background_mode') or annotation.get('background_mode') or (meta.get('scheme') or {}).get('background_mode')
    copies = [candidate for candidate in sorted(folder.glob('stimulus.*')) if candidate.suffix.lower() in image_suffixes]
    if not name and mode != 'solid' and copies:
        name = copies[0].name
    if not name:
        return None, None
    original = Path(name)
    if not original.is_absolute():
        original = _local(folder, name)
    # Moved legacy records often retain an obsolete absolute source path. Their
    # copied stimulus beside session.json is the portable acquisition snapshot.
    candidates = [_local(folder, original.name)]
    candidates.extend(_local(folder, candidate.name) for candidate in copies)
    candidates.append(original)
    selected = None
    mismatched = False
    for candidate in dict.fromkeys(candidates):
        if _linked(candidate):
            raise ValueError('实验背景图片不能为链接')
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in image_suffixes:
            continue
        if candidate.stat().st_size > 50 * 1024 * 1024:
            raise ValueError('实验背景图片超过保存限制')
        raw = candidate.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if meta.get('stimulus_sha256') and meta['stimulus_sha256'] != digest:
            mismatched = True
            continue
        selected = candidate, raw, digest
        break
    if selected is None:
        if mismatched:
            raise ValueError('实验背景图片与采集时的校验值不一致')
        # Old records can still export coordinates without their original image.
        return None, None
    path, raw, digest = selected
    local = path.resolve().is_relative_to(folder)
    if local:
        path = _local(folder, path.resolve().relative_to(folder))
    value = dict(filename=path.name, sha256=digest,
                 mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                 base64=base64.b64encode(raw).decode('ascii'))
    return value, path if local else None


def _delete_verified(folder, candidates):
    # Preflight the whole deletion set before deleting any member.
    checked = []
    for path, digest in candidates:
        path = _local(folder, path.relative_to(folder))
        if not path.exists():
            continue
        if not path.is_file() or _sha(path) != digest:
            raise ValueError('待整理文件已变更，保留原始文件')
        checked.append(path)
    for path in checked:
        path.unlink()
    raw = _local(folder, 'calibration_raw')
    if raw.is_dir() and not any(raw.iterdir()):
        raw.rmdir()


def compact_pending(folder):
    folder = _folder(folder)
    meta = _json(_local(folder, 'session.json'))
    candidates = []
    capture = _local(folder, 'capture.csv')
    if capture.is_file():
        with capture.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            columns = next(reader, [])
            timeline = dict(columns=columns, rows=list(reader))
        _timeline_rows(timeline)
        if 'capture_timeline' in meta and meta['capture_timeline'] != timeline:
            raise ValueError('已保存的采集时间线与 CSV 不一致，保留原始文件')
        meta['capture_timeline'] = timeline
        candidates.append((capture, _sha(capture)))
    if 'capture_timeline' in meta:
        timing = meta.get('camera_video_timing')
        if isinstance(timing, dict):
            timing['authoritative_timestamps'] = 'session.json:capture_timeline'
        elif isinstance(timing, str):
            meta['camera_video_timing'] = timing.replace('capture.csv', 'session.json:capture_timeline')
        environment = meta.get('acquisition_environment') or {}
        camera = environment.get('camera') or {}
        if isinstance(camera.get('timing'), dict):
            camera['timing']['authoritative_timestamps'] = 'session.json:capture_timeline'
    annotation = _local(folder, 'annotation.json')
    if annotation.is_file():
        meta['annotation'] = _json(annotation)
        candidates.append((annotation, _sha(annotation)))
    manifest = _local(folder, 'calibration_raw/manifest.json')
    if manifest.is_file():
        calibration_key = 'pre_calibration_material' if 'pre_calibration_material' in meta else 'calibration'
        calibration = copy.deepcopy(meta.get(calibration_key) or {})
        if calibration.get('manifest_sha256') and _sha(manifest) != calibration['manifest_sha256']:
            raise ValueError('校准素材清单校验失败，保留原始文件')
        embedded = _json(manifest)
        calibration['raw_manifest'] = embedded
        calibration['raw_manifest_sha256'] = canonical_sha256(embedded)
        calibration.pop('manifest', None)
        meta[calibration_key] = calibration
        candidates.append((manifest, _sha(manifest)))
    stimulus, image = _embedded_stimulus(folder, meta)
    if stimulus:
        meta['stimulus'] = stimulus
        meta['image_path'] = stimulus['filename']
    if image is not None:
        candidates.append((image, _sha(image)))
    gaze = _local(folder, 'gaze.csv')
    if gaze.is_file():
        with gaze.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            next(reader, None)
            empty = next(reader, None) is None
        if empty:
            candidates.append((gaze, _sha(gaze)))
    meta['storage_layout'] = LAYOUT
    _atomic_json(_local(folder, 'session.json'), meta)
    _delete_verified(folder, candidates)
    return meta


def _read_embedded(folder, key, legacy):
    folder = _folder(folder)
    session = _local(folder, 'session.json')
    meta = _json(session) if session.is_file() else {}
    if key in meta:
        if not isinstance(meta[key], dict):
            raise ValueError('保存的实验结果格式无效')
        return copy.deepcopy(meta[key])
    path = _local(folder, legacy)
    return _json(path) if path.is_file() else None


def read_result(folder):
    return _read_embedded(folder, 'result', 'coordinate_result.json')


def read_visual_manifest(folder):
    return _read_embedded(folder, 'visualization', 'experiment_visuals.json') or {}


def _basename(value):
    return str(value or '').replace('\\', '/').rsplit('/', 1)[-1]


def _portable_result(value):
    value = copy.deepcopy(value)
    value['participant_json_path'] = 'session.json'
    value.pop('participant_txt_path', None)
    for key in ('txt_path', 'distribution_path', 'video_path'):
        if value.get(key):
            value[key] = _basename(value[key])
    value.pop('poster_path', None)
    for key in ('output_dir', 'source_session'):
        if key in value:
            value[key] = '.'
    exported = value.get('coordinate_export')
    if isinstance(exported, dict):
        for key in list(exported):
            if key.startswith('participant_'):
                exported.pop(key)
        if exported.get('path'):
            exported['path'] = _basename(exported['path'])
    return value


def save_result(folder, result):
    folder = _folder(folder)
    meta = _json(_local(folder, 'session.json'))
    value = _portable_result(result)
    files = dict(meta.get('files') or {})
    files['information'] = 'session.json'
    if value.get('txt_path'):
        files['data'] = value['txt_path']
    _merge(folder, dict(result=value, files=files))
    return value


def save_visual_manifest(folder, manifest):
    folder = _folder(folder)
    if _local(folder, 'session.json').is_file():
        _merge(folder, {'visualization': copy.deepcopy(manifest)})
    else:
        # A time-range report is a child output, never the parent experiment.
        _atomic_json(_local(folder, 'experiment_visuals.json'), manifest)
    return manifest


def save_processing_status(folder, payload):
    _merge(folder, {'processing_status': copy.deepcopy(payload)})
    return payload


def _verify_coordinates(folder, result):
    from coordinates import TXT_FIELDS, EXPERIMENT_TXT_FIELDS, validate_utc_text
    exported = result.get('coordinate_export')
    if not isinstance(exported, dict) or exported.get('verified') is not True:
        raise ValueError('数据 TXT 尚未通过完整校验')
    name = _basename(result.get('txt_path'))
    if not name.endswith('.txt') or name != _basename(exported.get('path')):
        raise ValueError('数据 TXT 文件映射不一致')
    path = _local(folder, name)
    digest = exported.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-fA-F]{64}', digest) or _sha(path) != digest:
        raise ValueError('数据 TXT 校验值不一致')
    expected = result.get('export_rows')
    if (not isinstance(expected, int) or isinstance(expected, bool) or expected < 1
            or exported.get('rows') != expected or result.get('sample_hz') != 20 or exported.get('sample_hz') != 20):
        raise ValueError('数据 TXT 行数或采样率无效')
    count = 0
    previous_time = -math.inf
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, delimiter='\t')
        fields = tuple(next(reader, []))
        if fields not in (TXT_FIELDS, EXPERIMENT_TXT_FIELDS):
            raise ValueError('数据 TXT 表头不正确')
        for row in reader:
            if len(row) != len(fields):
                raise ValueError('数据 TXT 列数不正确')
            cells = dict(zip(fields, row))
            sparse = fields == EXPERIMENT_TXT_FIELDS
            if sparse and (cells['timestamp_utc'] == 'NaN' or not validate_utc_text(cells['timestamp_utc'])):
                raise ValueError('数据 TXT 的 UTC 时间格式不正确')
            values = [float(cells[field]) for field in TXT_FIELDS]
            if not math.isfinite(values[0]) or any(math.isinf(value) for value in values[1:]):
                raise ValueError('数据 TXT 不是有效的 20 Hz 坐标')
            if sparse:
                if values[0] < 0 or values[0] <= previous_time or abs(values[0] * 20 - round(values[0] * 20)) > 1e-6:
                    raise ValueError('数据 TXT 的名义 20 Hz 时间线无效')
            elif abs(values[0] - count / 20) > 1e-7:
                raise ValueError('数据 TXT 不是连续的 20 Hz 坐标')
            previous_time = values[0]
            count += 1
    if count != expected:
        raise ValueError('数据 TXT 行数不一致')
    return path


def verified_result(folder):
    """Return a portable, complete coordinate result or None, without any model."""
    try:
        folder = _folder(folder)
        result = read_result(folder) or {}
        if result.get('mode') != 'experiment' or result.get('is_full_session') is not True:
            return None
        path = _verify_coordinates(folder, result)
        result.update(txt_path=str(path), participant_json_path=str(folder / 'session.json'),
                      output_dir=str(folder), source_session=str(folder))
        return result
    except (OSError, ValueError, TypeError, KeyError):
        return None


def finalize_experiment(folder, stats):
    folder = _folder(folder)
    if stats.get('mode') != 'experiment' or stats.get('is_full_session') is not True:
        raise ValueError('只有完整实验结果可以整理为精简记录')
    data = _verify_coordinates(folder, stats)
    from experiment_visuals import visual_products
    products = visual_products(folder, verify_hashes=True)
    if not products:
        raise ValueError('散点图或注视回放尚未完整保存，保留处理素材')
    visual = read_visual_manifest(folder)
    if (visual.get('txt_sha256') != stats['coordinate_export']['sha256']
            or visual.get('rows') != stats['export_rows'] or visual.get('sample_hz') != 20):
        raise ValueError('散点图或注视回放与当前数据 TXT 不一致，保留处理素材')
    meta = compact_pending(folder)
    value = _portable_result(stats | products)
    candidates = []
    for filename, key in (('inference_complete.json', 'inference'),
                          ('processor_status.json', 'processing_status'),
                          ('raw_cleanup.json', 'raw_cleanup')):
        path = _local(folder, filename)
        if path.is_file():
            meta[key] = _portable_result(_json(path)) if key == 'inference' else _json(path)
            candidates.append((path, _sha(path)))
    meta['result'] = value
    meta['visualization'] = read_visual_manifest(folder)
    meta['files'] = dict(information='session.json', data=data.name,
                         replay='gaze_replay.mp4', distribution='distribution.png')
    # Sidecars below are fully represented by the verified products and JSON.
    known = ['gaze.csv', 'coordinate_result.json', 'coordinate_export.json',
             'experiment_visuals.json', 'replay_poster.png']
    participant = _basename(stats.get('participant_txt_path') or
                            (stats.get('coordinate_export') or {}).get('participant_path'))
    if participant.startswith('participant_') and participant.endswith('.txt'):
        known.append(participant)
    # A preceding save_result has already normalized the participant reference.
    from coordinates import experiment_txt_paths
    known.append(experiment_txt_paths(folder, folder)[1].name)
    for name in dict.fromkeys(known):
        path = _local(folder, name)
        if path.is_file():
            candidates.append((path, _sha(path)))
    # No camera video, calibration ZIP, or unknown path is ever included here.
    _atomic_json(_local(folder, 'session.json'), meta)
    _delete_verified(folder, candidates)
    result = copy.deepcopy(value)
    result.update(txt_path=str(data), participant_json_path=str(folder / 'session.json'),
                  output_dir=str(folder), source_session=str(folder), **products)
    result.pop('poster_path', None)
    return result
