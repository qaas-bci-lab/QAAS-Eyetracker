"""Experiment-only plots and replay derived from the final, verified 20 Hz TXT.

This module never opens the RGB recording, initializes a model, or queries the
processing computer's display. Missing samples stay missing in both products.
"""
from __future__ import annotations

import csv
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from threading import Lock

import cv2
import numpy as np

VERSION = 4
FILES = {'distribution_path': 'distribution.png', 'video_path': 'gaze_replay.mp4'}
_GAZE_OPACITY = .5
_HUE_RGB = np.array([(230, 43, 49), (246, 128, 29), (244, 199, 30), (37, 170, 73),
                     (20, 177, 186), (39, 98, 222), (149, 58, 206)]) / 255.
_SHADE_COUNT = 256
_plot_lock = Lock()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _local_file(folder, value):
    name = str(value or '').replace('\\', '/').rsplit('/', 1)[-1]
    path = folder / name
    if (not name or name in ('.', '..') or not path.is_file() or path.is_symlink()
            or (hasattr(path, 'is_junction') and path.is_junction())):
        raise ValueError('实验结果文件缺失或路径不可用')
    return path


def _geometry(meta):
    cfg = meta['config']
    values = [float(cfg[key]) for key in
              ('physical_width', 'physical_height', 'screen_width_mm', 'screen_height_mm')]
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError('采集端屏幕尺寸无效，无法生成注视回放')
    width, height, sw, sh = values
    if not .1 <= height / width <= 10:
        raise ValueError('采集端屏幕比例无效')
    origin = meta.get('origin', (meta.get('scheme') or {}).get('origin', {'x': .5, 'y': .5}))
    ox, oy = float(origin['x']), float(origin['y'])
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in (ox, oy)):
        raise ValueError('实验原点无效，无法还原屏幕坐标')
    return dict(width=width, height=height, sw=sw / 10., sh=sh / 10., ox=ox, oy=oy)


def _background(meta, session, geometry):
    width = min(1280, max(2, round(geometry['width'])))
    width -= width % 2
    height = max(2, round(width * geometry['height'] / geometry['width']))
    if height > 2160:
        width = max(2, round(width * 2160 / height))
        width -= width % 2
        height = max(2, round(width * geometry['height'] / geometry['width']))
    height -= height % 2
    scheme = meta.get('scheme') or {}
    annotation = meta.get('annotation') or {}
    sidecar = session / 'annotation.json'
    if not annotation and sidecar.is_file() and not sidecar.is_symlink():
        try:
            annotation = json.loads(sidecar.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            annotation = {}
    if not isinstance(annotation, dict):
        annotation = {}
    mode = meta.get('background_mode') or annotation.get('background_mode') or scheme.get('background_mode')
    color = meta.get('background_color') or annotation.get('background_color') or scheme.get('background_color', '#f0f0f0')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        color = '#f0f0f0'
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    background = np.full((height, width, 3), rgb[::-1], np.uint8)
    background_binding = dict(mode='solid', color=color)
    matte = meta.get('background_image_matte', '#151515')
    if not isinstance(matte, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', matte):
        matte = '#151515'
    matte_bgr = tuple(int(matte[index:index + 2], 16) for index in (5, 3, 1))
    raw_path = str(meta.get('image_path') or annotation.get('image') or '')
    stimulus = meta.get('stimulus')
    local_copies = sorted(path for path in session.glob('stimulus.*')
                          if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'})
    if mode == 'solid' or (mode != 'image' and not (raw_path or stimulus or local_copies)):
        return background, background_binding
    if isinstance(stimulus, dict) and stimulus.get('base64'):
        try:
            encoded = base64.b64decode(stimulus['base64'], validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError('保存的实验背景图片无法解码') from error
        digest = hashlib.sha256(encoded).hexdigest()
        if digest != stimulus.get('sha256'):
            raise ValueError('保存的实验背景图片校验未通过')
        image = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise ValueError('保存的实验背景图片无法读取')
        return _fit_background(background, image, matte_bgr), dict(mode='image', sha256=digest, matte=matte)
    original = Path(raw_path) if raw_path else None
    if original is not None and not original.is_absolute():
        relative = (session / original).resolve()
        if not relative.is_relative_to(session.resolve()):
            raise ValueError('记录中的背景图片路径超出本次实验目录')
    candidates = []
    if raw_path:
        candidates.append(session / raw_path.replace('\\', '/').rsplit('/', 1)[-1])
    candidates.extend(local_copies)
    if original is not None:
        candidates.append(original if original.is_absolute() else session / original)
    digest = meta.get('stimulus_sha256')
    image_path = next((path for path in candidates if path.is_file() and not path.is_symlink()
                       and not (hasattr(path, 'is_junction') and path.is_junction())
                       and (not digest or _sha(path) == digest)), None)
    if image_path is None:
        raise ValueError('找不到本次实验保存的背景图片，请同时复制背景素材')
    image = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError('实验背景图片无法读取')
    return _fit_background(background, image, matte_bgr), dict(mode='image', sha256=_sha(image_path), matte=matte)


def _fit_background(background, image, matte_bgr):
    height, width = background.shape[:2]
    scale = min(width / image.shape[1], height / image.shape[0])
    image = cv2.resize(image, (max(1, round(image.shape[1] * scale)),
                             max(1, round(image.shape[0] * scale))))
    background[:] = matte_bgr
    if image.dtype == np.uint16:
        image = (image // 257).astype(np.uint8)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        alpha = image[..., 3:].astype(np.uint16)
        color = image[..., :3].astype(np.uint16)
        matte = np.asarray(matte_bgr, dtype=np.uint16)
        image = ((color * alpha + matte * (255 - alpha) + 127) // 255).astype(np.uint8)
    x, y = (width - image.shape[1]) // 2, (height - image.shape[0]) // 2
    background[y:y + image.shape[0], x:x + image.shape[1]] = image
    return background


def _samples(folder, result, *, with_fields=False):
    from coordinates import TXT_FIELDS, EXPERIMENT_TXT_FIELDS, validate_utc_text
    path = _local_file(folder, result.get('txt_path'))
    exported = result.get('coordinate_export') or {}
    if not exported.get('verified') or _sha(path) != exported.get('sha256'):
        raise ValueError('数据 TXT 校验未通过，不能生成注视图像')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, delimiter='\t')
        fields = tuple(next(reader, []))
        if fields not in (TXT_FIELDS, EXPERIMENT_TXT_FIELDS):
            raise ValueError('数据 TXT 表头不正确')
        rows = []
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
                if (values[0] < 0 or (rows and values[0] <= rows[-1][0])
                        or abs(values[0] * 20 - round(values[0] * 20)) > 1e-6):
                    raise ValueError('数据 TXT 的名义 20 Hz 时间线无效')
            elif rows and not math.isclose(values[0] - rows[-1][0], .05, abs_tol=1e-7):
                raise ValueError('数据 TXT 不是连续的 20 Hz 坐标')
            rows.append(values)
    if not rows or len(rows) != result.get('export_rows') or result.get('sample_hz') != 20:
        raise ValueError('数据 TXT 行数或采样率与保存信息不一致')
    samples = np.asarray(rows, dtype=float)
    return (samples, fields) if with_fields else samples


def _binding(meta, result, geometry, background, timeline=None):
    payload = dict(version=VERSION, geometry=geometry, background=background,
                   txt_sha256=result['coordinate_export']['sha256'],
                   rows=result['export_rows'], sample_hz=20)
    if timeline is not None:
        payload['replay_timeline'] = timeline
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf8')).hexdigest()


def visual_products(folder, *, binding=None, verify_hashes=False):
    """Read only completed products; returned paths are rebuilt after a folder move."""
    folder = Path(folder)
    try:
        from record_layout import read_visual_manifest
        manifest = read_visual_manifest(folder)
        if not isinstance(manifest, dict):
            return None
        if manifest.get('version') != VERSION or (binding and manifest.get('binding') != binding):
            return None
        for name in FILES.values():
            path = _local_file(folder, name)
            entry = manifest['files'][name]
            if path.stat().st_size != entry['bytes'] or entry['bytes'] <= 0:
                return None
            if verify_hashes and _sha(path) != entry['sha256']:
                return None
        return {key: str((folder / name).resolve()) for key, name in FILES.items()} | dict(
            video_created=True, visual_rows=manifest['rows'], visual_frames=manifest.get('frames', manifest['rows']), visual_sample_hz=20,
            visuals_created=False, poster_path=str((folder / FILES['video_path']).resolve()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _screen_point(x, y, geometry, shape):
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    normalized_x = geometry['ox'] + x / geometry['sw']
    normalized_y = geometry['oy'] - y / geometry['sh']
    if not 0 <= normalized_x < 1 or not 0 <= normalized_y < 1:
        return None
    # Convert to display pixels only after checking the original coordinates.
    return (int(normalized_x * shape[1]), int(normalized_y * shape[0]))


def _time_colors(frame_count):
    """Assign 256 light-to-dark shades per hue to frames in time order."""
    base_count, remainder = divmod(frame_count, len(_HUE_RGB))
    colors = np.empty((frame_count, 3))
    start = 0
    for index, hue in enumerate(_HUE_RGB):
        count = base_count + (index < remainder)
        if count:
            light, dark = .58 + .42 * hue, .68 * hue
            levels = np.arange(count) * (_SHADE_COUNT - 1) // max(1, count - 1)
            colors[start:start + count] = light + (dark - light) * (levels[:, None] / (_SHADE_COUNT - 1))
        start += count
    return colors


def _draw_frame(background, samples, index, geometry, color):
    frame = background.copy()
    point = _screen_point(*samples[index, 1:3], geometry, frame.shape)
    if point is not None:
        x, y = point
        left, top = max(0, x - 8), max(0, y - 8)
        region = np.s_[top:y + 9, left:x + 9]
        overlay = frame[region]
        center = (x - left, y - top)
        bgr = tuple(map(int, np.rint(255 * color[::-1])))
        cv2.circle(overlay, center, 7, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(overlay, center, 4, bgr, -1, cv2.LINE_AA)
        cv2.addWeighted(overlay, _GAZE_OPACITY, background[region], 1 - _GAZE_OPACITY, 0, dst=overlay)
    return frame


def _write_png(path, image):
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('无法生成注视图像')
    encoded.tofile(str(path))


def _elapsed_label(time_s):
    minutes, centiseconds = divmod(round(time_s * 100), 6000)
    return f'{minutes}:{centiseconds / 100:05.2f}'.rstrip('0').rstrip('.')


def _distribution(path, samples, geometry, background, colors):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle
    fig = Figure(figsize=(10, 7), dpi=150, layout='constrained')
    FigureCanvasAgg(fig)
    ax, time_strip = fig.subplots(2, 1, gridspec_kw={'height_ratios': [25, 1]})
    sw, sh, ox, oy = (geometry[key] for key in ('sw', 'sh', 'ox', 'oy'))
    left, right, bottom, top = -ox * sw, (1 - ox) * sw, -(1 - oy) * sh, oy * sh
    ax.imshow(cv2.cvtColor(background, cv2.COLOR_BGR2RGB), extent=(left, right, bottom, top), alpha=.65)
    finite = np.isfinite(samples[:, 1:3]).all(axis=1)
    points = samples[finite]
    if len(points):
        ax.scatter(points[:, 1], points[:, 2], s=10, c=colors[finite], alpha=_GAZE_OPACITY, linewidths=0)
    else:
        ax.text(.5, .5, 'No valid gaze samples', transform=ax.transAxes, ha='center', va='center')
    ax.add_patch(Rectangle((left, bottom), sw, sh, fill=False, edgecolor='#888888', linewidth=1))
    ax.scatter([0], [0], marker='+', s=100, c='#111111', linewidths=1.5)
    ax.axhline(0, color='#bbbbbb', linewidth=.6)
    ax.axvline(0, color='#bbbbbb', linewidth=.6)
    # Include every finite exported point, including predictions outside the screen.
    xmin = min(left, float(points[:, 1].min())) if len(points) else left
    xmax = max(right, float(points[:, 1].max())) if len(points) else right
    ymin = min(bottom, float(points[:, 2].min())) if len(points) else bottom
    ymax = max(top, float(points[:, 2].max())) if len(points) else top
    ax.set(xlim=(xmin - sw * .03, xmax + sw * .03), ylim=(ymin - sh * .03, ymax + sh * .03),
           xlabel='X from experiment origin (cm)', ylabel='Y from experiment origin (cm)',
           title='Gaze distribution')
    ax.set_aspect('equal', adjustable='box')
    time_strip.imshow(colors[np.newaxis, :, :], aspect='auto', interpolation='nearest',
                      extent=(0, len(samples), 0, 1))
    positions = [0, len(samples)] if len(samples) > 1 else [.5]
    labels = [_elapsed_label(samples[0, 0])]
    if len(samples) > 1:
        labels.append(_elapsed_label(samples[-1, 0]))
    time_strip.set_xticks(positions, labels)
    time_strip.set_yticks([])
    time_strip.tick_params(axis='x', length=0, labelsize=9)
    time_strip.set_frame_on(False)
    fig.savefig(path)
    fig.clear()


def generate_experiment_visuals(folder, meta, result, *, session=None, on_progress=None):
    """Build all products before committing them; TXT and raw data are untouched."""
    folder = Path(folder).resolve()
    session = Path(session or folder).resolve()
    emit = on_progress or (lambda percent, message: None)
    emit(0, '核对已保存的数据 TXT')
    from coordinates import EXPERIMENT_TXT_FIELDS
    samples, fields = _samples(folder, result, with_fields=True)
    timeline = None
    if fields == EXPERIMENT_TXT_FIELDS:
        start = float(result.get('start_s', 0.) or 0.)
        end = float(result.get('end_s', samples[-1, 0] + .05))
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError('注视回放时段无效')
        first_slot = max(0, math.ceil(start * 20 - 1e-7))
        slots = {round(float(row[0]) * 20): index for index, row in enumerate(samples)}
        stop_slot = max(math.ceil(end * 20 - 1e-7), max(slots) + 1)
        if min(slots) < first_slot or stop_slot - first_slot > 24 * 3600 * 20:
            raise ValueError('注视回放时间线超出实验时段')
        frame_slots = range(first_slot, stop_slot)
        timeline = dict(mode='nominal_sparse_v1', start_slot=first_slot, stop_slot=stop_slot)
    else:
        frame_slots = range(len(samples))
        slots = {index: index for index in frame_slots}
    frame_count = len(frame_slots)
    geometry = _geometry(meta)
    background, background_binding = _background(meta, session, geometry)
    from replay_stimulus import ReplayStimulus
    stimulus = ReplayStimulus(background, meta, geometry)
    if stimulus.binding:
        background_binding['fixation'] = stimulus.binding
    binding = _binding(meta, result, geometry, background_binding, timeline)
    existing = visual_products(folder, binding=binding, verify_hashes=True)
    if existing is not None:
        emit(100, '散点图和注视回放已保存')
        return existing
    colors = _time_colors(len(samples))
    for name in FILES.values():
        target = folder / name
        if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
            raise ValueError('图像或视频保存路径包含链接')
    with tempfile.TemporaryDirectory(prefix='.experiment_visuals_', dir=folder) as temporary:
        pending = Path(temporary)
        emit(10, '生成预测散点图')
        with _plot_lock:
            _distribution(pending / FILES['distribution_path'], samples, geometry, background, colors)
        video = pending / FILES['video_path']
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 20.,
                                 (background.shape[1], background.shape[0]))
        if not writer.isOpened():
            writer.release()
            raise RuntimeError('无法创建注视回放；数据 TXT 和原始 RGB 已保留')
        try:
            for index, slot in enumerate(frame_slots):
                sample_index = slots.get(slot)
                elapsed_s = slot / 20 if timeline is not None else samples[index, 0]
                screen = stimulus.at(elapsed_s)
                frame = (screen if sample_index is None
                         else _draw_frame(screen, samples, sample_index, geometry, colors[sample_index]))
                writer.write(frame)
                if index % 20 == 0 or index + 1 == frame_count:
                    emit(20 + round(70 * (index + 1) / frame_count),
                         f'生成注视回放 {index + 1:,} / {frame_count:,}')
        finally:
            writer.release()
        # Decode the result to detect truncated or failed encoders before publication.
        emit(92, '校验注视回放')
        reader = cv2.VideoCapture(str(video))
        decoded = 0
        try:
            if not reader.isOpened() or not math.isclose(reader.get(cv2.CAP_PROP_FPS), 20., abs_tol=.01):
                raise RuntimeError('注视回放校验失败；原始数据已保留')
            while True:
                ok, frame = reader.read()
                if not ok:
                    break
                if frame.shape[:2] != background.shape[:2]:
                    raise RuntimeError('注视回放尺寸校验失败')
                decoded += 1
        finally:
            reader.release()
        if decoded != frame_count:
            raise RuntimeError('注视回放帧数不完整；数据 TXT 和原始 RGB 已保留')
        manifest = dict(version=VERSION, binding=binding, rows=len(samples), frames=frame_count, sample_hz=20,
                        txt_sha256=result['coordinate_export']['sha256'], files={})
        if timeline is not None:
            manifest['timeline'] = timeline
        for name in FILES.values():
            manifest['files'][name] = dict(bytes=(pending / name).stat().st_size, sha256=_sha(pending / name))
        # The manifest is published last; incomplete groups never count as complete.
        for name in FILES.values():
            os.replace(pending / name, folder / name)
        from record_layout import save_visual_manifest
        save_visual_manifest(folder, manifest)
    emit(100, '散点图和注视回放已保存')
    return visual_products(folder, binding=binding, verify_hashes=True) | {'visuals_created': True}


def ensure_session_visuals(folder, on_progress=None):
    """Generate missing experiment visuals from complete TXT, without rerunning inference."""
    from processor_service import _record_folder, completed_result
    folder = _record_folder(folder)
    result = completed_result(folder)
    if result is None:
        raise ValueError('请先生成并校验本次实验的数据 TXT 和人员信息 TXT')
    meta = json.loads((folder / 'session.json').read_text(encoding='utf-8-sig'))
    from coordinates import session_mode
    if session_mode(meta) != 'experiment':
        raise ValueError('此功能仅用于实验记录')
    return generate_experiment_visuals(folder, meta, result, on_progress=on_progress)
