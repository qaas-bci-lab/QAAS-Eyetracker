"""Persistent schemes and compatible geometry storage (UI lengths use mm).

This module does not initialize Qt, load a model, or access a camera.  Existing
annotation files and session directories are always treated as read-only inputs.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
from app_paths import APP_EDITION, DATA_ROOT, RESOURCE_ROOT, ensure_user_data

ROOT = DATA_ROOT
SETTINGS_VERSION = 2
DEFAULT_PROFILE_VERSION = 1
DEFAULT_GEOMETRY = {
    "screen_width_cm": 26.03,
    "screen_height_cm": 17.36,
    "camera_top_center": True,
    "camera_x_cm": 0.0,
    "camera_gap_cm": 1.0,
    "fov_degrees": 60.0,
    "camera_index": 0,
    "distance_mode": "estimated",
    "view_distance_cm": 55.0,
    "intrinsics": {},
}
DEFAULT_MODEL_ID = "affnet_mixed"
MODEL_PREFERENCE = (DEFAULT_MODEL_ID,)
DEFAULT_FIXATION = {
    'fixation_enabled': True,
    'fixation_style': 'cross',
    'fixation_color': '#000000',
    'fixation_diameter_px': 20.0,
}


DEFAULT_CALIBRATION_DWELL_S = 2.0
DEFAULT_TEST_POINT_SECONDS = 2.0
DEFAULT_REST_MINUTES = 15.0


def preset_points(layout='5', total_seconds=None):
    """Create two-second preset points, or evenly divide an explicit duration."""
    positions = ([(.5,.5),(.15,.15),(.85,.15),(.15,.85),(.85,.85)] if str(layout)=='5'
                 else [(x,y) for y in (.15,.5,.85) for x in (.15,.5,.85)])
    seconds = (DEFAULT_TEST_POINT_SECONDS if total_seconds is None
               else float(total_seconds)/len(positions))
    return [dict(x=x,y=y,seconds=seconds) for x,y in positions]


def _models_root(root: Path) -> Path:
    """Production weights are bundled read-only; explicit test roots stay local."""
    root = Path(root).resolve()
    return RESOURCE_ROOT / 'models' if root == DATA_ROOT.resolve() else root / 'models'


def preferred_model(root: Path = ROOT) -> str:
    """Pick a complete installed model for new drafts and retired-model fallback."""
    if APP_EDITION == 'capture_raw':
        # The processor uses this identity when fitting the saved calibration frames.
        return DEFAULT_MODEL_ID
    models = _models_root(root)
    for model_id in MODEL_PREFERENCE:
        directory = models / model_id
        if ((directory / 'backbone.pt').is_file()
                and (not model_id.startswith('affnet_') or (directory / 'model_cpu.ts').is_file())):
            return model_id
    raise ValueError('未找到完整的模型文件，请先将模型导入模型文件夹')


def default_scheme(name: str = "默认方案", *, mode: str = 'experiment') -> dict:
    """Return the built-in experiment or a draft for the selected mode."""
    if mode not in ('experiment', 'test'):
        raise ValueError('模式只能选择实验模式或测试模式')
    is_test = mode == 'test'
    two_part = not is_test and name == '默认方案'
    scheme = {
        "id": uuid.uuid4().hex,
        "name": name,
        "mode": mode,
        "default_profile_version": DEFAULT_PROFILE_VERSION,
        "test_layout": "5",
        "calibration_points": 9 if is_test or two_part else 0,
        "calibration_countdown_s": 3,
        "calibration_dwell_s": DEFAULT_CALIBRATION_DWELL_S,
        "calibration_exclude_start_ms": 500.0,
        "calibration_exclude_end_ms": 0.0,
        "transition_exclusion_s": 1.0,
        "direction_task_enabled": False,
        "direction_countdown_s": 3,
        "direction_visible_s": 0.3,
        "direction_timing_version": 1,
        "target_style": "dot" if is_test else "cross",
        "target_color": "#35452e" if is_test else "#000000",
        "duration_minutes": 5 * DEFAULT_TEST_POINT_SECONDS / 60 if is_test else 8.0,
        "model_id": DEFAULT_MODEL_ID,
        "target_diameter_px": 20.0,
        "countdown_font_px": 14.0,
        "hint_font_px": 22.0,
        "display_scale": 1.0,
        "background_mode": "solid",
        "background_color": "#f0f0f0",
        "image": "",
        "origin": {"x": .5, "y": .5},
        "points": preset_points('5') if is_test else [{"x": .5, "y": .5, "seconds": 480.0}],
        "assume_fixation": is_test,
        "max_fps": True,
        "delete_raw_after_analysis": True,
        **DEFAULT_FIXATION,
        "fixation_enabled": not is_test,
    }
    if not is_test:
        scheme.update(experiment_protocol='two_part' if two_part else 'single', rest_minutes=DEFAULT_REST_MINUTES)
    return scheme


def _number(value, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}必须是数值")
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{label}应在 {minimum:g}～{maximum:g} 之间")
    return value


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label}必须为勾选状态（true / false）")
    return value


def _text(value, label: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文本")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"{label}不能为空")
    return value


def _background_mode(scheme: dict, root: Path) -> str:
    """Retire the shipped grid while keeping custom legacy image backgrounds."""
    if 'background_mode' in scheme:
        mode = scheme['background_mode']
        if mode not in ('solid', 'image'):
            raise ValueError('背景类型只能选择纯色背景或图片背景')
        return mode
    value = scheme.get('image', '')
    if not value:
        return 'solid'
    if not isinstance(value, str):
        raise ValueError('实验图片路径必须是文本')
    path = Path(value).expanduser()
    path = path.resolve() if path.is_absolute() else (Path(root) / path).resolve()
    return 'solid' if path == (Path(root) / 'schemes/example.png').resolve() else 'image'


def resolve_image(scheme: dict, root: Path = ROOT) -> Path | None:
    """Resolve the active image; solid backgrounds leave the stored image unused."""
    if _background_mode(scheme, root) == 'solid':
        return None
    value = scheme.get("image", "")
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("实验图片路径必须是文本")
    p = Path(value).expanduser()
    return p.resolve() if p.is_absolute() else (Path(root) / p).resolve()


def _verify_image(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"实验图片不存在：{path}")
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(f"实验图片无法读取，请重新导入：{path.name}") from exc


def validate_scheme(scheme: dict, root: Path = ROOT, require_image: bool = True) -> dict:
    """Validate and return a normalized copy without modifying the caller.

    ``require_image=False`` allows an empty image so the settings editor can save
    a newly created draft. Starting a run validates the selected image background;
    a solid background needs no image. Drafts can retain moved image paths for repair.
    """
    if not isinstance(scheme, dict):
        raise ValueError("实验方案必须是JSON对象")
    out = copy.deepcopy(scheme)
    out["id"] = _text(out.get("id"), "方案ID")
    out["name"] = _text(out.get("name"), "方案名称")
    mode=out.get('mode')
    if mode is None:
        mode='test' if ('测试' in out['name'] or len(out.get('points',[]))>1) else 'experiment'
    if mode not in ('experiment','test'):raise ValueError('模式只能选择实验模式或测试模式')
    out['mode']=mode
    count = out.get("calibration_points")
    if isinstance(count, bool) or count not in (0, 5, 9) or not isinstance(count, int):
        raise ValueError("校准点数只能选择0、5或9")
    countdown = _number(out.get('calibration_countdown_s', 3), '校准前倒计时（秒）', 0, 60)
    if not countdown.is_integer():
        raise ValueError('校准前倒计时必须为整数秒')
    out['calibration_countdown_s'] = int(countdown)
    direction_enabled = _boolean(out.get('direction_task_enabled', False), '启用方向反应任务')
    out['direction_task_enabled'] = direction_enabled and mode == 'test'
    direction_countdown = _number(out.get('direction_countdown_s', 3), '答后下一点倒计时（秒）', 0, 60)
    if not direction_countdown.is_integer():
        raise ValueError('答后下一点倒计时必须为整数秒')
    out['direction_countdown_s'] = int(direction_countdown)
    out['direction_visible_s'] = _number(out.get('direction_visible_s', .3),
                                         '箭头显示时长（秒）', .05, 5)
    out['direction_timing_version'] = 1
    for key, title, default, low, high in (
        ('calibration_dwell_s', '每点校准时长（秒）', DEFAULT_CALIBRATION_DWELL_S, .1, 60),
        ('calibration_exclude_start_ms', '校准开始排除时间（ms）', 500.0, 0, 60000),
        ('calibration_exclude_end_ms', '校准结束排除时间（ms）', 0.0, 0, 60000),
        ('transition_exclusion_s', '注视稳定等待时间（秒）', 1.0, 0, 60),
    ):
        out[key] = _number(out.get(key, default), title, low, high)
    excluded_ms = out['calibration_exclude_start_ms'] + out['calibration_exclude_end_ms']
    if excluded_ms >= out['calibration_dwell_s'] * 1000:
        raise ValueError('校准开始与结束排除时间之和必须小于每点校准时长')
    style = out.get('target_style', 'dot' if mode == 'test' else 'cross')
    if not isinstance(style, str) or style not in ('dot', 'cross', 'countdown'):
        raise ValueError('标记样式只能选择圆点、十字或秒数倒计时')
    color = out.get('target_color', '#35452e' if mode == 'test' else '#000000')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        raise ValueError('标记颜色必须为#RRGGBB格式')
    out['target_style'] = style
    out['target_color'] = color.lower()
    for key,title,default,low,high in [('target_diameter_px','标记直径',20.,4.,200.),('countdown_font_px','倒计时字号',14.,6.,96.),('hint_font_px','提示字号',22.,8.,96.),('display_scale','显示缩放',1.,.25,4.)]:
        out[key]=_number(out.get(key,default),title,low,high)
    # Old small prompt sizes are upgraded; they remain stored as a base size.
    if out['hint_font_px'] < 22:out['hint_font_px']=22.
    if mode == 'experiment':
        protocol = out.get('experiment_protocol', 'single')
        if protocol not in ('single', 'two_part'):
            raise ValueError('实验流程只能选择单段或双段')
        if protocol == 'two_part' and count == 0:
            raise ValueError('双段实验需要在每段注视前后校准，请选择 5 点或 9 点校准')
        out['experiment_protocol'] = protocol
        out['rest_minutes'] = _number(out.get('rest_minutes', DEFAULT_REST_MINUTES), '休息时长（分钟）', 0, 1440)
        out['fixation_enabled'] = _boolean(out.get('fixation_enabled', DEFAULT_FIXATION['fixation_enabled']), '启用单点注视')
        fixation_style = out.get('fixation_style', DEFAULT_FIXATION['fixation_style'])
        if fixation_style not in ('dot', 'cross', 'countdown'):
            raise ValueError('注视点样式只能选择圆点、十字或秒数倒计时')
        out['fixation_style'] = fixation_style
        fixation_color = out.get('fixation_color', DEFAULT_FIXATION['fixation_color'])
        if not isinstance(fixation_color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', fixation_color):
            raise ValueError('注视点颜色必须为#RRGGBB格式')
        out['fixation_color'] = fixation_color.lower()
        out['fixation_diameter_px'] = _number(
            out.get('fixation_diameter_px', DEFAULT_FIXATION['fixation_diameter_px']), '注视点直径', 4, 200)
    else:
        out.pop('experiment_protocol', None)
        out.pop('rest_minutes', None)
    layout=out.get('test_layout','custom' if len(out.get('points',[]))>1 else '5')
    if str(layout) not in ('5','9','custom'):raise ValueError('测试点布局只能选择5点、9点或自定义')
    out['test_layout']=str(layout)
    out["duration_minutes"] = _number(out.get("duration_minutes"), "实验时长（分钟）", 0.01 if mode=='test' else 0.05, 1440)
    if APP_EDITION == 'capture_raw':
        model_id = DEFAULT_MODEL_ID
    else:
        model_id = _text(out['model_id'] if 'model_id' in out else preferred_model(root), "模型")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", model_id):
            raise ValueError("模型ID格式无效")
        if model_id in ('official', 'gc_full', 'mixed', 'affnet_natural', 'tg_full'):
            # Historical session snapshots are read by offline.py without normalization.
            out['model_migrated_from'] = model_id
            model_id = preferred_model(root)
        elif model_id not in MODEL_PREFERENCE:
            raise ValueError(f'未找到模型权重：{model_id}/backbone.pt（本版本仅支持 AFF-Net 均衡混合）')
        if not (_models_root(root) / model_id / "backbone.pt").is_file():
            raise ValueError(f"未找到模型权重：{model_id}/backbone.pt")
        if model_id.startswith('affnet_') and not (_models_root(root)/model_id/'model_cpu.ts').is_file():
            raise ValueError('AFF-Net推理文件尚未导入：model_cpu.ts')
    out["model_id"] = model_id
    out["image"] = _text(out.get("image", ""), "实验图片路径", allow_empty=True)
    out['background_mode'] = _background_mode(out, root)
    background_color = out.get('background_color', '#f0f0f0')
    if not isinstance(background_color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', background_color):
        raise ValueError('背景颜色必须为#RRGGBB格式')
    out['background_color'] = background_color.lower()
    for key, label in (("assume_fixation", "参考点误差分析"), ("max_fps", "最大速度采集")):
        out[key] = _boolean(out.get(key, True), label)
    points = out.get("points", [])
    if not isinstance(points, list):
        raise ValueError("标记点必须是列表")
    normalized_points = []
    max_seconds = 3600 if mode == 'test' else 1440 * 60
    for i, point in enumerate(points, 1):
        if not isinstance(point, dict):
            raise ValueError(f"第{i}个标记点格式无效")
        p = copy.deepcopy(point)
        p["x"] = _number(p.get("x"), f"第{i}点X坐标", 0, 1)
        p["y"] = _number(p.get("y"), f"第{i}点Y坐标", 0, 1)
        p["seconds"] = _number(p.get("seconds", DEFAULT_TEST_POINT_SECONDS if mode == 'test' else 10), f"第{i}点呈现时间（秒）", 0.1, max_seconds)
        normalized_points.append(p)
    # Session files are never migrated; only editable scheme drafts are normalized.
    origin=out.get('origin')
    if origin is None:
        origin=({"x":normalized_points[0]['x'],"y":normalized_points[0]['y']}
                if len(normalized_points)==1 else {"x":.5,"y":.5})
    if not isinstance(origin,dict):raise ValueError('原点必须包含屏幕归一化坐标x和y')
    origin={key:_number(origin.get(key),f'原点{key.upper()}坐标',0,1) for key in ['x','y']}
    out['origin']=origin
    if mode=='test':
        if layout!='custom':
            defaults=preset_points(layout,out['duration_minutes']*60)
            if len(normalized_points)==len(defaults):
                for point,previous in zip(defaults,normalized_points):point['seconds']=previous['seconds']
            normalized_points=defaults
        if not normalized_points:raise ValueError('测试模式请至少设置一个点')
        for i,point in enumerate(normalized_points,1):
            point['seconds']=_number(point['seconds'],f'第{i}点呈现时间（秒）',.1,3600)
            if point['seconds'] <= out['transition_exclusion_s']:
                raise ValueError(f'第{i}点呈现时间必须大于注视稳定等待时间')
            if out['direction_task_enabled'] and point['seconds'] < out['direction_visible_s']:
                raise ValueError(f'第{i}点呈现时间不能小于箭头显示时长（{out["direction_visible_s"]:g} 秒）')
        out['points']=normalized_points
        out['duration_minutes']=sum(p['seconds'] for p in normalized_points)/60
    else:out['points']=[dict(origin,seconds=out['duration_minutes']*60)]
    out['assume_fixation']=mode=='test'
    out['coordinate_frame']='screen_normalized'
    out['delete_raw_after_analysis']=_boolean(out.get('delete_raw_after_analysis',False),'分析后删除原始影像')
    if require_image and out['background_mode'] == 'image':
        image = resolve_image(out, root)
        if image is None:
            raise ValueError("请先选择实验图片，或切换为纯色背景")
        _verify_image(image)
    return out


def _validate_geometry(geometry: dict) -> dict:
    if not isinstance(geometry, dict):
        raise ValueError("屏幕和相机几何设置格式无效")
    out = copy.deepcopy(DEFAULT_GEOMETRY)
    out.update(copy.deepcopy(geometry))
    out["screen_width_cm"] = _number(out["screen_width_cm"], "屏幕宽度（cm）", 1, 300)
    out["screen_height_cm"] = _number(out["screen_height_cm"], "屏幕高度（cm）", 1, 300)
    out["camera_top_center"] = _boolean(out["camera_top_center"], "摄像头位于屏幕正上方中间")
    out["camera_x_cm"] = _number(out["camera_x_cm"], "摄像头水平偏移（cm）", -100, 100)
    out["camera_gap_cm"] = _number(out["camera_gap_cm"], "摄像头与屏幕上沿距离（cm）", -100, 100)
    out["fov_degrees"] = _number(out["fov_degrees"], "水平视场角（度）", 20, 130)
    if out['distance_mode'] not in ('fixed','estimated'):raise ValueError('距离模式必须为fixed或estimated')
    out['view_distance_cm']=_number(out['view_distance_cm'],'眼睛到屏幕距离（cm）',15,200)
    index = out["camera_index"]
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= 20:
        raise ValueError("摄像头编号应为0～20的整数")
    for key in ['camera_width','camera_height']:
        if key in out and (isinstance(out[key],bool) or not isinstance(out[key],int) or not 1<=out[key]<=8192):
            raise ValueError('相机图像宽高必须为1～8192的整数')
    intrinsics = out["intrinsics"]
    if not isinstance(intrinsics, dict):
        raise ValueError("相机内参必须是JSON对象")
    if intrinsics:
        matrix = intrinsics.get("camera_matrix")
        if not isinstance(matrix, list) or len(matrix) != 3 or any(not isinstance(r, list) or len(r) != 3 for r in matrix):
            raise ValueError("相机内参camera_matrix必须为3×3矩阵")
        for row in matrix:
            for value in row:
                _number(value, "相机内参矩阵元素", -1e9, 1e9)
        if matrix[0][0] <= 0 or matrix[1][1] <= 0:
            raise ValueError("相机内参焦距必须大于0")
        resolution = intrinsics.get("intrinsics_resolution")
        if not isinstance(resolution, list) or len(resolution) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in resolution):
            raise ValueError("相机内参必须包含intrinsics_resolution：[图像宽度, 图像高度]")
        distortion = intrinsics.get("distortion", [0, 0, 0, 0, 0])
        if not isinstance(distortion, list) or len(distortion) not in (4, 5, 8, 12, 14):
            raise ValueError("相机畸变参数长度应为4、5、8、12或14")
        for value in distortion:
            _number(value, "相机畸变参数", -1e6, 1e6)
    return out


def validate_processing(value: dict) -> dict:
    """Validate stored choices without requiring the destination to be mounted."""
    if not isinstance(value, dict):
        raise ValueError('数据处理设置必须是对象')
    mode = value.get('mode', 'immediate')
    if mode not in ('immediate', 'deferred'):
        raise ValueError('请选择实时处理或后台处理')
    retain_raw_rgb = _boolean(value.get('retain_raw_rgb', True), '保留原始 RGB 数据')
    output_dir = _text(value.get('output_dir', ''), '原始 RGB 保存文件夹', allow_empty=True)
    if mode == 'deferred' and not output_dir:
        raise ValueError('请选择后台处理的保存文件夹')
    if output_dir and ('\0' in output_dir or not Path(output_dir).is_absolute()):
        raise ValueError('原始 RGB 保存文件夹必须是绝对路径')
    result = dict(mode=mode, retain_raw_rgb=retain_raw_rgb, output_dir=output_dir)
    if 'device' in value:
        device = value['device']
        if not isinstance(device, str) or (device not in ('auto', 'cpu', 'cuda')
                                          and re.fullmatch(r'cuda:\d+', device) is None):
            raise ValueError('处理设备应为 auto、cpu、cuda 或 cuda:0 等编号')
        result['device'] = device
    if 'cpu_threads' in value:
        threads = value['cpu_threads']
        if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
            raise ValueError('CPU 线程数必须为正整数')
        result['cpu_threads'] = threads
    return result


def processing_for_mode(settings: dict, mode: str) -> dict | None:
    """Return a detached per-mode policy, retaining legacy global fallback."""
    if mode not in ('experiment', 'test'):
        raise ValueError('模式只能选择实验模式或测试模式')
    policy = settings.get('processing_by_mode', {}).get(mode)
    if policy is None:
        policy = settings.get('processing')
    return copy.deepcopy(policy)


def _migrate_default_profile(scheme: dict, mode: str) -> None:
    """Apply the requested built-in upgrade once, never to named custom drafts."""
    version = scheme.get('default_profile_version', 0)
    if isinstance(version, int) and not isinstance(version, bool) and version >= DEFAULT_PROFILE_VERSION:
        return
    expected_name = '默认方案' if mode == 'experiment' else '短时测试'
    if scheme.get('name') != expected_name:
        return
    scheme['calibration_dwell_s'] = DEFAULT_CALIBRATION_DWELL_S
    excluded = [scheme.get('calibration_exclude_start_ms', 500.),
                scheme.get('calibration_exclude_end_ms', 0.)]
    if (all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in excluded)
            and sum(excluded) >= DEFAULT_CALIBRATION_DWELL_S * 1000):
        scheme.update(calibration_exclude_start_ms=500., calibration_exclude_end_ms=0.)
    if mode == 'experiment':
        scheme.update(fixation_enabled=True, fixation_style='cross', target_style='cross',
                      background_mode='solid')
    else:
        points = scheme.get('points')
        if isinstance(points, list):
            for point in points:
                if isinstance(point, dict):
                    point['seconds'] = DEFAULT_TEST_POINT_SECONDS
        layout = str(scheme.get('test_layout', 'custom' if isinstance(points, list) and len(points) > 1 else '5'))
        count = (int(layout) if layout in ('5', '9') else len(points or []))
        if count:
            scheme['duration_minutes'] = count * DEFAULT_TEST_POINT_SECONDS / 60
        cache = scheme.get('test_points_by_layout')
        if isinstance(cache, dict):
            for saved in cache.values():
                if isinstance(saved, list):
                    for point in saved:
                        if isinstance(point, dict):
                            point['seconds'] = DEFAULT_TEST_POINT_SECONDS
        settling = scheme.get('transition_exclusion_s', 1.)
        if isinstance(settling, (int, float)) and not isinstance(settling, bool) and settling >= DEFAULT_TEST_POINT_SECONDS:
            scheme['transition_exclusion_s'] = 1.
        arrow = scheme.get('direction_visible_s', .3)
        if (scheme.get('direction_task_enabled') is True and isinstance(arrow, (int, float))
                and not isinstance(arrow, bool) and arrow > DEFAULT_TEST_POINT_SECONDS):
            scheme['direction_visible_s'] = .3
    scheme['default_profile_version'] = DEFAULT_PROFILE_VERSION


def _validated_bank(schemes, root: Path, title: str, mode: str) -> list[dict]:
    """A mode bank owns the mode; the scheme still owns its layout and timing."""
    if not isinstance(schemes, list) or not schemes:
        raise ValueError(f"请至少保留一个{title}方案")
    normalized = []
    for raw in schemes:
        if not isinstance(raw, dict):
            raise ValueError("实验方案必须是JSON对象")
        scheme = copy.deepcopy(raw)
        scheme['mode'] = mode
        profile_version = raw.get('default_profile_version', 0)
        current_profile = (isinstance(profile_version, int) and not isinstance(profile_version, bool)
                           and profile_version >= DEFAULT_PROFILE_VERSION)
        if not current_profile and raw.get('name') != ('默认方案' if mode == 'experiment' else '短时测试'):
            # Unversioned custom schemes keep their former implicit behavior.
            # Fresh custom drafts carry the version and receive the new defaults.
            scheme.setdefault('calibration_dwell_s', 3.)
            scheme.setdefault('target_style', 'dot')
            if mode == 'experiment':
                scheme.setdefault('fixation_enabled', False)
            if isinstance(scheme.get('points'), list):
                for point in scheme['points']:
                    if isinstance(point, dict):
                        point.setdefault('seconds', 10.)
        # Legacy editors always wrote .5. Only editable settings adopt the new
        # default, while historical session parameters and new choices remain intact.
        if 'direction_timing_version' not in raw and raw.get('direction_visible_s') == .5:
            scheme['direction_visible_s'] = .3
        # Earlier editors forcibly wrote .5 and did not expose a timing control.
        # Upgrade only editable legacy drafts, never saved session metadata.
        legacy_timing = not any(key in raw for key in (
            'calibration_dwell_s', 'calibration_exclude_start_ms', 'calibration_exclude_end_ms'))
        if legacy_timing and raw.get('transition_exclusion_s') == .5:
            scheme['transition_exclusion_s'] = 1.
        if legacy_timing and mode == 'experiment' and raw.get('fixation_color') == '#202020':
            scheme['fixation_color'] = '#000000'
        _migrate_default_profile(scheme, mode)
        if mode == 'experiment' and scheme.get('name') == '默认方案' and 'experiment_protocol' not in scheme:
            scheme.update(experiment_protocol='two_part', calibration_points=9)
        normalized.append(validate_scheme(scheme, root, require_image=False))
    ids = [scheme['id'] for scheme in normalized]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{title}方案ID重复")
    return normalized


def _legacy_mode_banks(schemes, selected, root: Path):
    """Split only explicit old test modes and the built-in short test.

    Do not infer a mode from old annotation point counts: old example images can
    contain many annotations even when their scheme was a normal experiment.
    """
    if not isinstance(schemes, list) or not schemes:
        raise ValueError("请至少保留一个实验方案")
    experiments, tests = [], []
    for scheme in schemes:
        if not isinstance(scheme, dict):
            raise ValueError("实验方案必须是JSON对象")
        old_mode = scheme.get('mode')
        if old_mode is not None and old_mode not in ('experiment', 'test'):
            raise ValueError('模式只能选择实验模式或测试模式')
        is_test = old_mode == 'test' or scheme.get('name') == '短时测试'
        (tests if is_test else experiments).append(copy.deepcopy(scheme))
    experiments = _validated_bank(experiments, root, '实验', 'experiment') if experiments else []
    tests = _validated_bank(tests, root, '测试', 'test') if tests else []
    if not experiments:
        current = next((scheme for scheme in tests if scheme['id'] == selected), tests[0])
        experiment = copy.deepcopy(current)
        experiment.update(id=uuid.uuid4().hex, name='实验方案', mode='experiment',
                          duration_minutes=max(.05, current['duration_minutes']), calibration_points=0)
        experiments = _validated_bank([experiment], root, '实验', 'experiment')
    experiment_ids = [scheme['id'] for scheme in experiments]
    selected_experiment = selected if selected in experiment_ids else experiment_ids[0]
    if not tests:
        current = next(scheme for scheme in experiments if scheme['id'] == selected_experiment)
        short = copy.deepcopy(current)
        short.update(id=uuid.uuid4().hex, name='测试方案', mode='test', test_layout='5',
                     duration_minutes=5 * DEFAULT_TEST_POINT_SECONDS / 60,
                     points=preset_points('5'), assume_fixation=True, calibration_points=9,
                     calibration_dwell_s=DEFAULT_CALIBRATION_DWELL_S,
                     calibration_exclude_start_ms=500., calibration_exclude_end_ms=0.,
                     transition_exclusion_s=1., direction_task_enabled=False)
        tests = _validated_bank([short], root, '测试', 'test')
    test_ids = [scheme['id'] for scheme in tests]
    builtin = next((scheme['id'] for scheme in tests if scheme['name'] == '短时测试'), test_ids[0])
    selected_test = selected if selected in test_ids else builtin
    return experiments, selected_experiment, tests, selected_test


def _validated_settings(data: dict, root: Path) -> dict:
    if not isinstance(data, dict):
        raise ValueError("设置文件必须是JSON对象")
    out = copy.deepcopy(data)
    if out.get("version", SETTINGS_VERSION) != SETTINGS_VERSION:
        raise ValueError("设置文件版本不受支持，请保留原文件并重新导入方案")
    out["version"] = SETTINGS_VERSION
    out["geometry"] = _validate_geometry(out.get("geometry", {}))
    defaults_version = out.get('defaults_version', 0)
    if (not isinstance(defaults_version, int) or isinstance(defaults_version, bool)
            or defaults_version < DEFAULT_PROFILE_VERSION):
        out['geometry']['distance_mode'] = 'estimated'
        out['defaults_version'] = DEFAULT_PROFILE_VERSION
    if 'processing' in out:
        out['processing'] = validate_processing(out['processing'])
    if 'processing_by_mode' in out:
        policies = out['processing_by_mode']
        if not isinstance(policies, dict) or set(policies) - {'experiment', 'test'}:
            raise ValueError('数据处理设置只能包含实验与测试')
        out['processing_by_mode'] = {mode: validate_processing(policy)
                                     for mode, policy in policies.items()}
    if 'test_schemes' not in out:
        (out['schemes'], out['selected_scheme'], out['test_schemes'],
         out['selected_test_scheme']) = _legacy_mode_banks(out.get('schemes'), out.get('selected_scheme'), root)
    else:
        out['schemes'] = _validated_bank(out.get('schemes'), root, '实验', 'experiment')
        out['test_schemes'] = _validated_bank(out['test_schemes'], root, '测试', 'test')
    ids = [scheme['id'] for scheme in out['schemes']]
    test_ids = [scheme['id'] for scheme in out['test_schemes']]
    if set(ids).intersection(test_ids):
        raise ValueError('实验与测试方案ID不能重复')
    selected = out.get('selected_scheme', ids[0])
    out['selected_scheme'] = selected if selected in ids else ids[0]
    selected_test = out.get('selected_test_scheme', test_ids[0])
    out['selected_test_scheme'] = selected_test if selected_test in test_ids else test_ids[0]
    return out


def normalize_settings(data: dict, root: Path = ROOT) -> dict:
    """Validate both independent banks without editing the caller or disk."""
    return _validated_settings(data, Path(root).resolve())


def save_settings(data: dict, root: Path = ROOT) -> dict:
    """Validate and atomically save a settings copy; return the saved copy."""
    root = Path(root).resolve()
    normalized = _validated_settings(data, root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, prefix=".settings_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(normalized, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "settings.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return copy.deepcopy(normalized)


def _initial_settings(root: Path) -> dict:
    model_id = preferred_model(root)
    primary = default_scheme()
    primary["model_id"] = model_id
    short = default_scheme('短时测试', mode='test')
    short["model_id"] = primary["model_id"]
    return {"version": SETTINGS_VERSION, "defaults_version": DEFAULT_PROFILE_VERSION,
            "selected_scheme": primary["id"],
            "selected_test_scheme": short['id'], "geometry": copy.deepcopy(DEFAULT_GEOMETRY),
            "schemes": [primary], 'test_schemes': [short]}


def load_settings(root: Path = ROOT) -> dict:
    """Load saved schemes; first use creates defaults without editing old data."""
    root = Path(root).resolve()
    if root == DATA_ROOT.resolve():
        ensure_user_data()
    path = root / "settings.json"
    if not path.exists():
        return save_settings(_initial_settings(root), root)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"设置文件无法读取，原文件已保留：{path}") from exc
    return _validated_settings(data, root)


def import_image(path: str | Path, root: Path = ROOT) -> str:
    """Copy a readable image into the project and return its portable relative path."""
    source = Path(path).expanduser().resolve()
    _verify_image(source)
    root = Path(root).resolve()
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    suffix = source.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".img"
    relative = Path("schemes") / "assets" / (digest.hexdigest() + suffix)
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest.hexdigest():
            raise ValueError("已导入图片的内容校验失败，请先保留文件并重新导入")
        return relative.as_posix()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, prefix=".image_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as image:
                for chunk in iter(lambda: image.read(1024 * 1024), b""):
                    stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return relative.as_posix()
