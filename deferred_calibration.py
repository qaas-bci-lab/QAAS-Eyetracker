"""Portable, versioned raw calibration clips. No model imports on acquisition."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import zipfile

PROTOCOL = 'deferred_rgb_v1'
RAW_DIRECTORY = 'calibration_raw'
MAX_FRAMES = 20000
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save_json(path, value):
    from record_layout import _atomic_json
    _atomic_json(Path(path), value)


def binding(metadata):
    """Bind clips to the recorded geometry, person and selected model."""
    value = {key: metadata.get(key) for key in ('model_id', 'config', 'participant', 'calibration_id')}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def save_material(folder, metadata, clips, targets, *, model_hash, runtime_hash=None,
                  completed=True, stop_reason=None, summary_key='calibration', update_session_state=True):
    """Commit JPEG archive, then manifest, then the session's pending reference."""
    folder = Path(folder)
    root = folder / RAW_DIRECTORY
    root.mkdir(parents=True, exist_ok=True)
    cfg = metadata['config']
    count = int(cfg['calibration_points'])
    dwell = float(cfg.get('calibration_dwell_s', 3))
    start = float(cfg.get('calibration_exclude_start_ms', 500)) / 1000
    end = dwell - float(cfg.get('calibration_exclude_end_ms', 0)) / 1000
    entries = []
    counts = {index: 0 for index in range(count)}
    total = 0
    archive = root / 'frames.zip'
    pending = root / 'frames.zip.partial'
    if len(clips) > MAX_FRAMES:
        raise ValueError('校准素材帧数超出限制，请缩短每点校准时长')
    with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED) as output:
        for index, (encoded, mono, snapshot) in enumerate(clips):
            if not encoded or len(encoded) > MAX_FRAME_BYTES:
                raise ValueError('校准图片为空或超出大小限制')
            total += len(encoded)
            if total > MAX_TOTAL_BYTES:
                raise ValueError('校准素材超出容量限制，请缩短每点校准时长')
            target_id = int(snapshot['target_id'])
            offset = mono / 1e9 - snapshot['target_since']
            if target_id not in counts or not start <= offset < end:
                raise ValueError('校准帧不在对应注视点的有效采样窗口内')
            name = f'frames/{index:06d}.jpg'
            output.writestr(name, encoded)
            entries.append(dict(name=name, sha256=hashlib.sha256(encoded).hexdigest(),
                                byte_size=len(encoded), monotonic_ns=mono,
                                timestamp_unix_ns=snapshot.get('timestamp_unix_ns'),
                                target_id=target_id, target=snapshot['target'],
                                target_started_monotonic_s=snapshot['target_since'],
                                captured_offset_s=offset))
            counts[target_id] += 1
    pending.replace(archive)
    sufficient = len(targets) == count and all(value >= 5 for value in counts.values())
    manifest = dict(protocol=PROTOCOL, version=1, status='captured' if completed and sufficient else 'interrupted',
                    binding_sha256=binding(metadata), model_id=metadata['model_id'],
                    model_sha256=model_hash, runtime_sha256=runtime_hash,
                    target_count=count, targets=copy.deepcopy(targets),
                    target_dwell_s=dwell, capture_window_s=[start, end],
                    frame_interval_ns=50_000_000, frame_count=len(entries),
                    frame_bytes=total, counts={str(key): value for key, value in counts.items()},
                    archive='frames.zip', frames=entries, stop_reason=stop_reason)
    save_json(root / 'manifest.json', manifest)
    summary = dict(protocol=PROTOCOL, status='pending' if completed and sufficient else 'incomplete',
                   target_count=count, valid_frames=None, captured_frames=len(entries),
                   model_sha256=model_hash, runtime_sha256=runtime_hash,
                   affine=None, head_adapted=False,
                   manifest=RAW_DIRECTORY + '/manifest.json',
                   manifest_sha256=digest_file(root / 'manifest.json'),
                   capture_window_s=[start, end], target_dwell_s=dwell,
                   exclude_start_ms=start * 1000, exclude_end_ms=(dwell-end) * 1000)
    metadata[summary_key] = summary
    if update_session_state:
        metadata['acquisition_state'] = 'awaiting_start' if completed and sufficient else 'calibration_only'
        if stop_reason:
            metadata['stop_reason'] = stop_reason
    save_json(folder / 'session.json', metadata)
    return summary


def read_material(folder, metadata, *, summary_key='calibration'):
    """Validate all paths, sizes, hashes and timing before any model work."""
    from record_layout import _folder, _local, canonical_sha256
    folder = _folder(folder)
    calibration = metadata.get(summary_key) or {}
    if calibration.get('protocol') != PROTOCOL:
        raise ValueError('校准素材协议不受支持')
    manifest_path = _local(folder, RAW_DIRECTORY + '/manifest.json')
    if 'raw_manifest' in calibration:
        manifest = calibration['raw_manifest']
        if not isinstance(manifest, dict) or canonical_sha256(manifest) != calibration.get('raw_manifest_sha256'):
            raise ValueError('校准素材清单校验失败，保存信息可能已变更')
    else:
        if calibration.get('manifest') != RAW_DIRECTORY + '/manifest.json' or not manifest_path.is_file():
            raise ValueError('缺少原始校准素材 manifest.json')
        if manifest_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('校准清单路径或大小无效')
        if digest_file(manifest_path) != calibration.get('manifest_sha256'):
            raise ValueError('校准素材清单校验失败，文件可能已变更')
        manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('protocol') != PROTOCOL or manifest.get('version') != 1:
        raise ValueError('校准素材版本不受支持')
    if manifest.get('status') != 'captured':
        raise ValueError('本次校准采集未完成，请重新采集校准和实验')
    if manifest.get('binding_sha256') != binding(metadata):
        raise ValueError('校准素材与本次人员、模型或屏幕相机参数不匹配')
    count = int(metadata['config']['calibration_points'])
    if count not in (5, 9) or manifest.get('target_count') != count or calibration.get('target_count') != count:
        raise ValueError('校准点数量与实验方案不匹配')
    if manifest.get('model_sha256') != calibration.get('model_sha256') or manifest.get('runtime_sha256') != calibration.get('runtime_sha256'):
        raise ValueError('校准模型校验值不匹配')
    frames = manifest.get('frames')
    if not isinstance(frames, list) or not 0 < len(frames) <= MAX_FRAMES or len(frames) != manifest.get('frame_count'):
        raise ValueError('校准图片数量无效')
    archive = _local(folder, RAW_DIRECTORY + '/frames.zip')
    if manifest.get('archive') != 'frames.zip' or not archive.is_file() or not archive.resolve().is_relative_to(folder):
        raise ValueError('缺少完整的校准图片压缩包')
    if archive.stat().st_size > MAX_TOTAL_BYTES + MAX_FRAMES * 256:
        raise ValueError('校准图片压缩包过大')
    targets = {int(target['target_id']): target for target in manifest.get('targets', [])}
    if set(targets) != set(range(count)):
        raise ValueError('校准点位清单不完整')
    cfg = metadata['config']
    dwell = float(cfg.get('calibration_dwell_s', 3))
    window = [float(cfg.get('calibration_exclude_start_ms', 500)) / 1000,
              dwell - float(cfg.get('calibration_exclude_end_ms', 0)) / 1000]
    if window != manifest.get('capture_window_s') or dwell != manifest.get('target_dwell_s'):
        raise ValueError('校准有效采样窗口不匹配')
    counts = {index: 0 for index in range(count)}
    clips = []
    total = 0
    previous_mono = -1
    try:
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if len(members) != len(frames) or len({member.filename for member in members}) != len(members):
                raise ValueError('校准压缩包图片条目不一致')
            for index, entry in enumerate(frames):
                name = f'frames/{index:06d}.jpg'
                if entry.get('name') != name:
                    raise ValueError('校准压缩包路径无效')
                member = source.getinfo(name)
                if not 0 < member.file_size <= MAX_FRAME_BYTES or member.file_size != entry.get('byte_size'):
                    raise ValueError('校准图片大小不一致或过大')
                total += member.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError('校准素材总大小超出限制')
                raw = source.read(member)
                if hashlib.sha256(raw).hexdigest() != entry.get('sha256'):
                    raise ValueError('校准图片校验失败')
                target_id = entry.get('target_id')
                offset = float(entry.get('captured_offset_s', -1))
                mono = int(entry.get('monotonic_ns', -1))
                target = targets.get(target_id)
                if (target is None or entry.get('target') != target['target'] or mono <= previous_mono
                        or not window[0] <= offset < window[1]
                        or abs(mono / 1e9 - float(entry['target_started_monotonic_s']) - offset) > 1e-6):
                    raise ValueError('校准图片的点位或采样时间无效')
                if not all(math.isfinite(float(value)) for value in target['target']):
                    raise ValueError('校准目标坐标无效')
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != 'JPEG' or min(image.size) <= 0 or max(image.size) > 8192 or image.width * image.height > 24000000:
                        raise ValueError('校准图片尺寸或格式无效')
                clips.append((raw, entry))
                counts[target_id] += 1
                previous_mono = mono
    except (zipfile.BadZipFile, KeyError, OSError) as error:
        raise ValueError('无法读取完整的原始校准图片') from error
    if any(value < 5 for value in counts.values()):
        raise ValueError('每个校准点至少需要5帧原始图片')
    return manifest, clips


def fit_material(folder, metadata, tracker, *, on_progress=None):
    """Use the original model's calibrator after validating saved acquisition clips."""
    import cv2
    import numpy as np
    manifest, clips = read_material(folder, metadata)
    calibration = copy.deepcopy(metadata['calibration'])
    if tracker.model_hash != calibration.get('model_sha256'):
        raise ValueError('当前模型与校准采集时的模型不同')
    if calibration.get('runtime_sha256') and calibration['runtime_sha256'] != getattr(tracker, 'runtime_hash', None):
        raise ValueError('AFF-Net推理文件与原始校准素材不匹配')
    tracker.head = None
    tracker.affine = None
    samples = []
    counts = {index: 0 for index in range(manifest['target_count'])}
    for index, (raw, entry) in enumerate(clips):
        frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError('校准图片无法解码')
        try:
            sample = tracker.infer(frame)
            samples.append(sample | {key: entry[key] for key in ('target_id', 'target', 'captured_offset_s')})
            counts[entry['target_id']] += 1
        except ValueError:
            pass
        if on_progress and (index % 5 == 0 or index + 1 == len(clips)):
            on_progress(round(65 * (index+1) / len(clips)), f'处理校准图片 {index+1}/{len(clips)}')
    missing = [str(index+1) for index, value in counts.items() if value < 5]
    if missing:
        raise ValueError('个人校准失败，点 ' + '、'.join(missing) + ' 少于5帧有效人脸预测。原始素材已保留，请检查画面后重新采集')
    try:
        result = tracker.calibrate(samples, metadata['config'].get('use_maml', True),
                                   on_progress=lambda percent, text: on_progress(65+round(percent*.35), text) if on_progress else None)
        affine = np.asarray(result.get('affine'), dtype=float)
        if result.get('target_count') != manifest['target_count'] or affine.shape != (3, 2) or not np.isfinite(affine).all():
            raise ValueError('个人校准输出无效')
        if result.get('head_adapted') and not tracker.head:
            raise ValueError('个人校准缺少已适配参数')
    except Exception as error:
        raise ValueError('个人校准计算失败：' + str(error)) from error
    folder = Path(folder)
    if tracker.head is not None:
        pending = folder / 'personal_maml.partial.npz'
        np.savez(pending, **{key: value.detach().cpu().numpy() for key, value in tracker.head.items()})
        pending.replace(folder / 'personal_maml.npz')
    calibration.update(result)
    calibration.update(status='complete', deferred=True, raw_counts=manifest['counts'])
    latest = json.loads((folder / 'session.json').read_text(encoding='utf8'))
    if binding(latest) != binding(metadata):
        raise ValueError('实验信息在校准处理时发生改变，原始素材已保留')
    latest['calibration'] = calibration
    save_json(folder / 'session.json', latest)
    metadata.clear()
    metadata.update(latest)
    if on_progress:
        on_progress(100, '个人校准已完成')
    return calibration
