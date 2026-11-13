"""Session/range reporting with real progress, raw coordinates and separate 95% intervals."""
from pathlib import Path
from collections import Counter
import csv
import json
import html
import math
import re
import os
import tempfile
from datetime import datetime,timezone

import numpy as np
import cv2
from coordinates import (ANGULAR_METHOD, TXT_FIELDS, EXPERIMENT_TXT_FIELDS, enrich_row, enrich_session_row, export_txt,
                         export_session_txt, validate_txt, sha256, session_mode, rows_for_protocol,
                         experiment_duration_s, experiment_sample_rows, experiment_txt_paths,
                         export_experiment_txt)
from record_layout import read_capture_rows, has_capture_data, read_result, save_result, finalize_experiment, experiment_segment


def _error_metrics(rows,cfg):
    """Frame errors always use the captured current target, never TXT origin."""
    if not rows:return dict(n=0,mean_error_cm=None,median_error_cm=None,rmse_cm=None,p95_cm=None,
                            signed_bias_x_cm=None,signed_bias_y_cm=None,mean_angular_error_deg=None,
                            median_angular_error_deg=None,p95_angular_error_deg=None)
    dx=np.array([_number(row,'dx_physical') for row in rows])/cfg['physical_width']*cfg['screen_width_mm']/10
    dy=-np.array([_number(row,'dy_physical') for row in rows])/cfg['physical_height']*cfg['screen_height_mm']/10
    errors=np.hypot(dx,dy)
    angles=np.array([_number(row,'angular_error_deg') for row in rows]);angles=angles[np.isfinite(angles)]
    return dict(n=len(rows),mean_error_cm=float(errors.mean()),median_error_cm=float(np.median(errors)),
                rmse_cm=float(np.sqrt(np.mean(errors**2))),p95_cm=float(np.quantile(errors,.95)),
                signed_bias_x_cm=float(dx.mean()),signed_bias_y_cm=float(dy.mean()),
                mean_angular_error_deg=float(angles.mean()) if len(angles) else None,
                median_angular_error_deg=float(np.median(angles)) if len(angles) else None,
                p95_angular_error_deg=float(np.quantile(angles,.95)) if len(angles) else None)


def _recordable_duration(meta,start,end):
    if meta.get('direction_task_enabled') is True:
        points=meta.get('points',(meta.get('scheme') or {}).get('points',[]))
        settling=float(meta.get('transition_exclusion_s',0.))
        total=0.
        for response in (meta.get('direction_task') or {}).get('responses',[]):
            if response.get('status')!='correct':
                continue
            index=int(response['target_id'])
            point_start=float(response['point_started_s'])
            point_end=point_start+float(points[index]['seconds'])
            total+=max(0.,min(end,point_end)-max(start,point_start+settling))
        return total
    if not (session_mode(meta)=='test' and meta.get('transition_exclusion_s') and
            max(meta.get('protocol_version',0),meta.get('recording_protocol_version',0))>=3):return end-start
    cursor=0.;total=0.;settling=float(meta['transition_exclusion_s'])
    for point in meta.get('points',(meta.get('scheme') or {}).get('points',[])):
        seconds=float(point['seconds']);total+=max(0.,min(end,cursor+seconds)-max(start,cursor+settling));cursor+=seconds
    return total


def canvas(meta, width=1280):
    cfg = meta['config']
    height = max(2, round(width * cfg['physical_height'] / cfg['physical_width']))
    height -= height % 2  # mp4 encoders need even dimensions.
    mode = meta.get('background_mode', (meta.get('scheme') or {}).get('background_mode'))
    if mode == 'solid':
        color = meta.get('background_color', (meta.get('scheme') or {}).get('background_color', '#f0f0f0'))
        if not isinstance(color, str) or re.fullmatch(r'#[0-9a-fA-F]{6}', color) is None:
            color = '#f0f0f0'
        rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
        return np.full((height, width, 3), rgb[::-1], np.uint8)
    bg = np.full((height, width, 3), 24, np.uint8)
    path = Path(meta.get('image_path', ''))
    if path.is_file():
        image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            scale = min(width / image.shape[1], height / image.shape[0])
            image = cv2.resize(image, None, fx=scale, fy=scale)
            x = (width - image.shape[1]) // 2
            y = (height - image.shape[0]) // 2
            bg[y:y + image.shape[0], x:x + image.shape[1]] = image
    return bg


def mean_ci_block(errors, times, seed=20261104):
    """Descriptive session-level CI using one-second time-block resampling."""
    errors, times = np.asarray(errors), np.asarray(times)
    if not len(errors):
        return None
    ids = np.floor(times - times.min()).astype(int)
    blocks = [errors[ids == index] for index in np.unique(ids)]
    if len(blocks) < 5:
        return None
    sums = np.array([block.sum() for block in blocks])
    counts = np.array([len(block) for block in blocks])
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(2000):
        draw = rng.integers(0, len(blocks), len(blocks))
        means.append(sums[draw].sum() / counts[draw].sum())
    return np.quantile(means, [.025, .975]).tolist()


def _number(row, key, fallback=float('nan')):
    try:
        return float(row.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _valid(row):
    return row.get('valid') == '1' and np.isfinite([_number(row, 'x_physical'),
                                                   _number(row, 'y_physical')]).all()


def _tag(value):
    return f'{value:.3f}'.rstrip('0').rstrip('.').replace('.', 'p')


def _format(value, suffix='', digits=2):
    if value is None:
        return '—'
    if isinstance(value, (list, tuple)):
        return ' – '.join(f'{float(number):.{digits}f}' for number in value) + suffix
    return f'{float(value):.{digits}f}{suffix}'


def _html_report(stats, has_errors):
    error = _format(stats.get('mean_error_cm'), ' cm')
    confidence = _format(stats.get('mean_error_95ci_cm'), ' cm')
    interval = _format(stats.get('error_central_95_interval_cm'), ' cm')
    angle = _format(stats.get('mean_angular_error_deg'), '°')
    annotation = ('按未校准的 '+stats.get('model_family','FAZE')+' 预测计算。' if stats['uncalibrated']
                  else f"使用 {stats['calibration_points']} 点个人校准。")
    if stats['status'] == 'no_valid_predictions':
        note = '该时段没有有效注视预测，无法计算误差。请检查人脸是否清晰可见、摄像头方向及几何设置。'
    elif stats['status'] == 'no_reference_labels':
        note = '该时段没有可用于误差计算的参考标签；仍可查看有效预测的分布和视频。'
    else:
        note = '误差基于“持续注视当前标记点”的实验假设，不能证明真实注视始终位于标记点。'
    image_html = '<img src="errors.png" alt="误差明细图">' if has_errors else ''
    video_html = ('<video controls preload="metadata" src="gaze_replay.mp4"></video>'
                  if stats['video_created'] else '<p class="muted">此记录没有可生成回放的帧。</p>')
    extra=''.join(f'<div class="metric"><b>{html.escape(value)}</b><span>{title}</span></div>' for title,value in [
        ('中位误差',_format(stats.get('median_error_cm'),' cm')),('RMSE',_format(stats.get('rmse_cm'),' cm')),
        ('误差 P95',_format(stats.get('p95_cm'),' cm')),('X偏差',_format(stats.get('signed_bias_x_cm'),' cm')),
        ('Y偏差',_format(stats.get('signed_bias_y_cm'),' cm')),('有效率',_format((stats.get('valid_fraction') or 0)*100,'%'))])
    point_rows=''.join('<tr>'+''.join(f'<td>{html.escape(str(v))}</td>' for v in [
        int(p['target_id'])+1 if str(p['target_id']).lstrip('-').isdigit() else p['target_id'],
        p['capture_frames'],p['valid_predictions'],_format((p.get('valid_fraction') or 0)*100,'%'),
        _format(p.get('mean_error_cm'),' cm'),_format(p.get('rmse_cm'),' cm'),_format(p.get('mean_angular_error_deg'),'°')])+'</tr>'
        for p in stats.get('per_point',[]))
    direction=stats.get('direction_task')
    direction_html=''
    if direction:
        values=[('正确点',str(direction.get('correct_points',0))),
                ('错误点',str(direction.get('incorrect_points',0))),
                ('未作答点',str(direction.get('unanswered_points',0))),
                ('作答准确率',_format((direction.get('accuracy') or 0)*100,'%')),
                ('平均反应时',_format(direction.get('mean_reaction_time_s'),' 秒',3))]
        cards=''.join(f'<div class="metric"><b>{html.escape(value)}</b><span>{title}</span></div>' for title,value in values)
        direction_html=f'<section class="section"><h2>方向反应任务</h2><div class="metrics">{cards}</div><p class="muted">错误及未作答点不参与以上眼动误差统计；逐点方向和反应时保存在 direction_responses.json。</p></section>'
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>眼动实验报告</title>
<style>body{margin:0;background:#f7f7f4;color:#1d1d1f;font-family:-apple-system,BlinkMacSystemFont,"Microsoft YaHei UI","Microsoft YaHei","PingFang SC",sans-serif;font-size:16px;line-height:1.65}.wrap{max-width:1120px;margin:auto;padding:44px 24px}h1{font-size:34px;letter-spacing:-1px;margin:8px 0}h2{font-size:22px;margin:0 0 20px}.eyebrow{color:#344542;font-size:13px;font-weight:700;letter-spacing:2px}.muted{color:#626268}.section{background:white;border:1px solid #e6e6eb;border-radius:6px;padding:28px;margin-top:24px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:18px}.metric{background:#f7f7f4;border-radius:4px;padding:20px}.metric b{font-size:26px;display:block;line-height:1.3}.metric span{display:block;font-size:13px;color:#626268;margin-top:8px}.subtle{font-size:14px;color:#626268}img,video{max-width:100%;display:block;border-radius:12px}video{width:100%;background:#111}.badge{display:inline-block;padding:5px 12px;background:#eef0eb;color:#344542;border-radius:4px;font-size:13px}</style>
<main class="wrap"><div class="eyebrow">EYE TRACKING</div><h1>实验数据分析</h1>'''+f'''
<p>模型：{html.escape(str(stats.get('model_id','')))}　校准：{stats['calibration_points']}点　时段：{stats['start_s']:.2f} – {stats['end_s']:.2f} 秒</p>
<section class="section"><h2>误差结果</h2>
<div class="metrics" style="margin-top:20px"><div class="metric"><b>{error}</b><span>平均欧氏距离误差</span></div><div class="metric"><b>{angle}</b><span>平均角度误差</span></div><div class="metric"><b>{confidence}</b><span>平均距离误差的 95% 置信区间</span></div><div class="metric"><b>{interval}</b><span>单帧距离误差中间 95% 范围</span></div></div>
<div class="metrics" style="margin-top:18px">{extra}</div>{image_html}</section>
<section class="section"><h2>各测试点</h2><table style="width:100%;border-spacing:0 14px;text-align:left"><thead><tr><th>点</th><th>采集帧</th><th>有效帧</th><th>有效率</th><th>平均误差</th><th>RMSE</th><th>角度误差</th></tr></thead><tbody>{point_rows}</tbody></table></section>
{direction_html}
<section class="section"><h2>坐标</h2><a href="gaze_coordinates.txt">打开TXT</a></section>
<section class="section"><h2>预测点分布</h2><img src="distribution.png" alt="预测散点图"></section>
<section class="section"><h2>注视点视频</h2>{video_html}</section></main></html>'''


def _write_report_json(path, stats):
    path = Path(path)
    pending = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.result-', suffix='.partial',
                                         encoding='utf8', delete=False) as stream:
            pending = Path(stream.name)
            json.dump(stats, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if pending is not None and pending.exists():
            pending.unlink()


def _record_cleanup_result(path, stats):
    # The verified export exists before cleanup. A bookkeeping failure afterwards
    # must not reclassify a successful export as failed inference.
    try:
        _write_report_json(path, stats)
    except OSError as error:
        stats['post_export_warning'] = '数据已保存，清理状态更新失败：' + str(error)


def cleanup_raw_data(session,meta,stats,all_rows,capture_rows,*,retain_raw_rgb=None):
    """Delete only this session's raw copies after a verified complete export.

    Never follows a symlink/junction to remove an external target. All files are
    preflighted before any unlink. Analysis records and derived plots remain.
    """
    processing = meta.get('processing')
    explicit = retain_raw_rgb is not None or (isinstance(processing, dict) and processing.get('mode') in ('immediate', 'deferred'))
    if retain_raw_rgb is not None:
        if not isinstance(retain_raw_rgb, bool):
            raise ValueError('是否保留原始 RGB 必须为勾选状态')
        requested = not retain_raw_rgb
    elif explicit:
        requested = processing['mode'] == 'immediate' and processing.get('retain_raw_rgb') is False
    else:
        requested = (meta.get('delete_raw_after_analysis') is True
                     or (meta.get('scheme') or {}).get('delete_raw_after_analysis') is True)
    result=dict(requested=requested,status='disabled',deleted=[])
    if not requested:return result
    session=Path(session).resolve()
    try:
        if explicit and not stats.get('is_full_session'):
            raise ValueError('局部时段导出保留原始数据')
        completion=json.loads((session/'inference_complete.json').read_text(encoding='utf8'))
        exported=json.loads((session/'coordinate_export.json').read_text(encoding='utf8'))
        if completion.get('mode')!='offline' or completion.get('frames')!=len(all_rows) or len(all_rows)!=len(capture_rows):
            raise ValueError('整次推理帧数尚未完整核对')
        if stats['valid_predictions']<1 or not any(_valid(row) for row in all_rows):raise ValueError('没有有效预测，保留原始数据')
        experiment = session_mode(meta) == 'experiment'
        if experiment:
            if not stats.get('is_full_session'):raise ValueError('局部时段导出保留原始数据')
            if not read_result(Path(stats['output_dir'])):raise ValueError('坐标结果尚未保存')
            if stats['export_rows'] <= stats['missing_samples']:raise ValueError('20 Hz TXT没有有效坐标，保留原始数据')
            data_path, participant_path = experiment_txt_paths(session, session)
            expected_rows = experiment_sample_rows(all_rows, 0., stats['end_s'])
            if sha256(participant_path) != exported.get('participant_sha256'):
                raise ValueError('人员信息TXT校验未通过')
            with participant_path.open(encoding='utf8', newline='') as file:
                if next(csv.reader(file, delimiter='\t'), []) != ['数据文件', data_path.name]:
                    raise ValueError('人员信息TXT与数据文件名不一致')
        else:
            if not stats.get('video_created') or not (Path(stats['output_dir'])/'gaze_replay.mp4').is_file():raise ValueError('回放视频尚未成功生成')
            if not (Path(stats['output_dir'])/'distribution.png').is_file():raise ValueError('散点图尚未成功生成')
            if not (Path(stats['output_dir'])/'report.json').is_file() or not (Path(stats['output_dir'])/'report.html').is_file():raise ValueError('报告尚未保存')
            data_path, expected_rows = session/'gaze_coordinates.txt', all_rows
        if not exported.get('verified') or exported.get('csv_sha256')!=sha256(session/'gaze.csv'):raise ValueError('完整TXT与当前CSV不一致')
        checked=validate_txt(data_path,expected_rows,
                             fields=EXPERIMENT_TXT_FIELDS if experiment else TXT_FIELDS)
        if checked['sha256']!=exported.get('sha256') or checked['rows']!=exported.get('rows'):raise ValueError('完整TXT回读校验未通过')
        candidates = [session / 'camera.mp4']
        calibration_dir = session / 'calibration_raw'
        if not experiment_segment(meta) and (calibration_dir.exists() or calibration_dir.is_symlink()):
            if (calibration_dir.is_symlink() or (hasattr(calibration_dir, 'is_junction') and calibration_dir.is_junction())
                    or calibration_dir.resolve().parent != session or not calibration_dir.is_dir()):
                raise ValueError('校准素材路径不安全，未执行清理')
            calibration = meta.get('calibration') or {}
            if (calibration.get('status') in ('pending', 'interrupted', 'incomplete')
                    or (calibration.get('protocol') == 'deferred_rgb_v1' and calibration.get('status') != 'complete')):
                raise ValueError('个人校准尚未完成，保留原始数据')
            # Keep the manifest and fitted parameters for provenance; only the
            # fixed ZIP of RGB frames is raw image data. Never recurse-delete.
            candidates.append(calibration_dir / 'frames.zip')
        if not explicit:
            image_suffixes = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}
            candidates.extend(sorted(p for p in session.glob('stimulus.*') if p.suffix.lower() in image_suffixes))
        paths=[]
        for path in candidates:
            if not path.exists() and not path.is_symlink():continue
            expected_parent = calibration_dir.resolve() if path.parent == calibration_dir else session
            if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()) or path.resolve().parent!=expected_parent or not path.is_file():
                raise ValueError('原始文件路径不安全，未执行清理')
            paths.append(path)
        result.update(status='planned',planned=[path.relative_to(session).as_posix() for path in paths],timestamp_utc=datetime.now(timezone.utc).isoformat())
        manifest=session/'raw_cleanup.json'
        manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        for path in paths:
            path.unlink();result['deleted'].append(path.relative_to(session).as_posix())
        result['status']='deleted' if paths else 'already_removed'
        manifest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    except (OSError,ValueError,TypeError,KeyError) as error:
        result.update(status='retained' if not result['deleted'] else 'partial',reason=str(error))
        try:(session/'raw_cleanup.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        except OSError:pass
    return result


def report(session, make_video=True, *, on_progress=None, start_s=None, end_s=None, retain_raw_rgb=None,
           compact_experiment=False):
    """Generate report for a full session or a half-open [start_s, end_s) range.

    Old calls ``report(session, make_video)`` remain compatible. Full reports keep
    their old filenames. Range reports are isolated in ``analysis_<start>_<end>``.
    The returned stats always include the absolute ``output_dir`` for the UI.
    """
    if retain_raw_rgb is not None and not isinstance(retain_raw_rgb, bool):
        raise ValueError('是否保留原始 RGB 必须为勾选状态')
    previous = -1

    def emit(percent, message):
        nonlocal previous
        percent = max(previous, min(100, max(0, int(percent))))
        previous = percent
        if on_progress is not None:
            on_progress(percent, message)

    session = Path(session).resolve()
    emit(0, '读取注视坐标与实验时间…')
    if not (session / 'session.json').is_file():
        raise ValueError('缺少实验信息或注视坐标，请先完成该记录的离线推理。')
    meta = json.loads((session / 'session.json').read_text(encoding='utf8'))
    if not (session / 'gaze.csv').is_file():
        from processor_service import completed_result
        result = completed_result(session) if session_mode(meta) == 'experiment' else None
        if result is None:
            raise ValueError('缺少实验信息或注视坐标，请先完成该记录的离线推理。')
        if ((start_s is not None and float(start_s) != 0.)
                or (end_s is not None and not math.isclose(float(end_s), result['end_s'], abs_tol=.0005))):
            raise ValueError('本次实验已保存为完整 20 Hz 数据，请查看整次实验结果')
        if make_video:
            from experiment_visuals import ensure_session_visuals
            result.update(ensure_session_visuals(session, on_progress=emit))
        if compact_experiment:
            result = finalize_experiment(session, result)
        emit(100, '已读取保存的实验结果')
        return result
    image_path = Path(meta.get('image_path') or '')
    if meta.get('image_path') and not image_path.is_absolute():
        image_path = (session / image_path).resolve()
        if not image_path.is_relative_to(session):
            raise ValueError('记录中的背景图片路径超出本次实验目录')
        meta['image_path'] = str(image_path)
    with (session / 'gaze.csv').open(encoding='utf-8-sig', newline='') as file:
        all_rows = list(csv.DictReader(file))
    if not has_capture_data(session, meta):
        raise ValueError('记录缺少 capture.csv，无法核对采集时间。')
    all_capture = read_capture_rows(session, meta)
    all_rows=rows_for_protocol(meta,all_rows)
    all_capture=rows_for_protocol(meta,all_capture)
    all_rows = sorted([row for row in all_rows if np.isfinite(_number(row, 'elapsed_s'))],
                      key=lambda row: _number(row, 'elapsed_s'))
    all_capture = sorted([row for row in all_capture if np.isfinite(_number(row, 'elapsed_s'))],
                         key=lambda row: _number(row, 'elapsed_s'))
    if not all_rows:raise ValueError('没有已完成的预测记录，请先运行离线推理。')
    experiment = session_mode(meta) == 'experiment'
    duration = (experiment_duration_s(meta, all_capture or all_rows) if experiment
                else float(meta.get('actual_duration_s') or 0))
    if duration <= 0 and not experiment:
        times = np.array([_number(row, 'elapsed_s') for row in all_capture or all_rows])
        if len(times):
            step = float(np.median(np.diff(times))) if len(times) > 1 else .05
            duration = float(times.max() + max(step, .001))
    if not np.isfinite(duration) or duration <= 0:
        raise ValueError('本次记录没有可分析的采集时段。')
    start = 0. if start_s is None else float(start_s)
    end = duration if end_s is None else float(end_s)
    if not np.isfinite([start, end]).all() or start < 0 or end > duration + .001 or end <= start:
        raise ValueError(f'分析时段无效，请选择 0 至 {duration:.2f} 秒内的起止时间。')
    end = min(end, duration)
    is_full = (start == 0. and end == duration if experiment else
               math.isclose(start, 0, abs_tol=.0005) and math.isclose(end, duration, abs_tol=.0005))
    output = session if is_full else session / f'analysis_{_tag(start)}_{_tag(end)}'
    output.mkdir(parents=True, exist_ok=True)
    rows = [row for row in all_rows if start <= _number(row, 'elapsed_s') < end]
    capture = [row for row in all_capture if start <= _number(row, 'elapsed_s') < end]
    valid = [row for row in rows if _valid(row)]
    labeled = [row for row in valid if bool(meta.get('assume_fixation', False)) and row.get('label_valid') == '1' and
               np.isfinite([_number(row, 'dx_physical'), _number(row, 'dy_physical')]).all()]
    cfg = meta['config']
    all_rows=[enrich_session_row(row,meta) for row in all_rows]
    rows=[row for row in all_rows if start<=_number(row,'elapsed_s')<end]
    valid=[row for row in rows if _valid(row)]
    labeled=[row for row in valid if bool(meta.get('assume_fixation',False)) and str(row.get('label_valid'))=='1'
             and np.isfinite([_number(row,'x_cm'),_number(row,'y_cm')]).all()]
    selected_duration = end - start
    calibration = meta.get('calibration') or {}
    calibration_points = int(calibration.get('target_count',
                                            5 if calibration.get('affine') is not None else 0))
    if experiment:
        emit(10, '正在生成 20 Hz 数据和人员信息…')
        if is_full:
            selected_export = export_session_txt(session, all_rows, cfg, meta=meta, duration_s=duration)
        else:
            selected_export = export_experiment_txt(session, output, rows, meta, start, end)
        stats=dict(mode='experiment',status='coordinates_saved',output_dir=str(output),source_session=str(session),
                   start_s=start,end_s=end,duration_s=selected_duration,session_duration_s=duration,is_full_session=is_full,
                   capture_frames=len(capture),prediction_rows=len(rows),valid_predictions=len(valid),
                   model_id=meta.get('model_id'),model_sha256=calibration.get('model_sha256',meta.get('model_sha256')),
                   model_family='AFF-Net' if str(meta.get('model_id','')).startswith('affnet_') else 'FAZE',
                   calibration_points=calibration_points,calibration_method=calibration.get('calibration_method'),
                   txt_path=selected_export['path'],participant_txt_path=selected_export['participant_path'],
                   sample_hz=selected_export['sample_hz'],export_rows=selected_export['rows'],
                   missing_samples=selected_export['missing_samples'],coordinate_export=selected_export,
                   invalid_reasons=dict(Counter(row.get('reason') or '无效预测' for row in rows if not _valid(row))),
                   txt_columns=list(selected_export['columns']))
        result_file=output/'coordinate_result.json'
        if compact_experiment and is_full:
            save_result(output, stats)
        else:
            _write_report_json(result_file, stats)
        if make_video:
            from experiment_visuals import generate_experiment_visuals
            stats.update(generate_experiment_visuals(output, meta, stats, session=session,
                         on_progress=lambda percent, message: emit(15 + round(percent * .8), message)))
            if compact_experiment and is_full:
                save_result(output, stats)
            else:
                _write_report_json(result_file, stats)
        stats['raw_cleanup']=cleanup_raw_data(session,meta,stats,all_rows,all_capture,retain_raw_rgb=retain_raw_rgb)
        if compact_experiment and is_full:
            save_result(output, stats)
            stats=finalize_experiment(output, stats)
        else:
            _record_cleanup_result(result_file, stats)
        emit(100,'数据、人员信息和注视图像已保存' if make_video else '数据和人员信息 TXT 已保存')
        return stats
    export_session_txt(session,all_rows,cfg)
    txt_path=output/'gaze_coordinates.txt'
    selected_export=export_txt(txt_path,rows)
    width, height = cfg['physical_width'], cfg['physical_height']
    stats = dict(capture_frames=len(capture), prediction_rows=len(rows),
                 valid_predictions=len(valid), labeled_frames=len(labeled),
                 capture_hz=len(capture) / selected_duration,
                 valid_samples_per_recorded_second=len(valid) / selected_duration,
                 duration_s=selected_duration, session_duration_s=duration,
                 start_s=start, end_s=end, is_full_session=is_full,
                 output_dir=str(output), source_session=str(session),
                 calibration_points=calibration_points, uncalibrated=calibration_points == 0,
                 model_family='AFF-Net' if str(meta.get('model_id','')).startswith('affnet_') else 'FAZE',
                 calibration_method=calibration.get('calibration_method','personal_screen_affine' if str(meta.get('model_id','')).startswith('affnet_') else 'FAZE_MAML_and_screen'),
                 inference_mode='offline after acquisition; not real-time throughput',
                 reference_assumption=bool(meta.get('assume_fixation', False)),
                 ci_method='2000 bootstrap replicates of 1-second time blocks; descriptive single-session interval, not population inference',
                 mean_error_physical_px=None, mean_error_logical_px=None, mean_error_mm=None,
                 mean_error_cm=None, mean_error_95ci_px=None, mean_error_95ci_cm=None,
                 error_central_95_interval_px=None, error_central_95_interval_cm=None,
                 error_p95_px=None, signed_bias_x_px=None, signed_bias_y_px=None,
                 mean_absolute_x_px=None, mean_absolute_y_px=None,
                 outside_screen_fraction=None,
                 invalid_reasons=dict(Counter(row.get('reason') or '无效预测' for row in rows if not _valid(row))))
    stats.update(mode='test',model_id=meta.get('model_id'),model_sha256=calibration.get('model_sha256',meta.get('model_sha256')),
                 valid_fraction=len(valid)/len(capture) if capture else None,
                 recordable_duration_s=_recordable_duration(meta,start,end),
                 transition_exclusion_s=meta.get('transition_exclusion_s',0),
                 direction_task=(meta.get('direction_task') or {}).get('summary'))
    stats['capture_hz_during_recordable_time']=len(capture)/stats['recordable_duration_s'] if stats['recordable_duration_s'] else None
    stats.update(_error_metrics(labeled,cfg))
    points=sorted(set(str(row.get('target_id','-1')) for row in capture),key=lambda x:int(x) if x.lstrip('-').isdigit() else 0)
    stats['per_point']=[]
    for point in points:
        point_capture=[row for row in capture if str(row.get('target_id','-1'))==point]
        point_valid=[row for row in valid if str(row.get('target_id','-1'))==point]
        point_labeled=[row for row in labeled if str(row.get('target_id','-1'))==point]
        stats['per_point'].append(dict(target_id=point,capture_frames=len(point_capture),valid_predictions=len(point_valid),
            valid_fraction=len(point_valid)/len(point_capture) if point_capture else None,**_error_metrics(point_labeled,cfg)))
    stats.update(txt_path=str(txt_path),coordinate_export=selected_export,
                 mean_angular_error_deg=None,mean_angular_error_95ci_deg=None,angular_valid_frames=0,
                 angular_error_method=ANGULAR_METHOD,angular_error_note='',error_p95_cm=None,
                 raw_cleanup={'status':'pending'})
    stats['status'] = ('no_valid_predictions' if not valid else
                       'no_reference_labels' if not labeled else 'ok')
    emit(10, '计算参考误差与统计区间…')
    if labeled:
        dx = np.array([_number(row, 'dx_physical') for row in labeled])
        dy = np.array([_number(row, 'dy_physical') for row in labeled])
        error = np.hypot(dx, dy)
        labeled_times = np.array([_number(row, 'elapsed_s') for row in labeled])
        dpr = np.array([_number(row, 'dpr', cfg['dpr']) for row in labeled])
        error_mm = np.hypot(dx / width * cfg['screen_width_mm'],
                            dy / height * cfg['screen_height_mm'])
        stats.update(mean_error_physical_px=float(error.mean()),
                     mean_error_logical_px=float(np.mean(error / dpr)),
                     mean_error_mm=float(error_mm.mean()), mean_error_cm=float(error_mm.mean() / 10),
                     signed_bias_x_px=float(dx.mean()), signed_bias_y_px=float(dy.mean()),
                     mean_absolute_x_px=float(abs(dx).mean()), mean_absolute_y_px=float(abs(dy).mean()),
                     mean_error_95ci_px=mean_ci_block(error, labeled_times),
                     mean_error_95ci_cm=mean_ci_block(error_mm / 10, labeled_times),
                     error_central_95_interval_px=np.quantile(error, [.025, .975]).tolist(),
                     error_central_95_interval_cm=np.quantile(error_mm / 10, [.025, .975]).tolist(),
                     error_p95_px=float(np.quantile(error, .95)))
        stats['error_p95_cm']=float(np.quantile(error_mm/10,.95))
        angular_rows=[row for row in labeled if np.isfinite(_number(row,'angular_error_deg'))]
        if angular_rows:
            angles=np.array([_number(row,'angular_error_deg') for row in angular_rows])
            angle_times=np.array([_number(row,'elapsed_s') for row in angular_rows])
            stats.update(mean_angular_error_deg=float(angles.mean()),
                         mean_angular_error_95ci_deg=mean_ci_block(angles,angle_times),
                         angular_valid_frames=len(angles))
    if not stats['angular_valid_frames']:
        stats['angular_error_note']='记录缺少可核查的眼睛三维位置或有效参考标签，未补造角度。'
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    emit(25, '生成整个所选时段的预测散点图…')
    if (meta.get('background_mode', (meta.get('scheme') or {}).get('background_mode')) != 'solid'
            and not Path(meta.get('image_path', '')).is_file()):
        images = list(session.glob('stimulus.*'))
        if images:
            meta['image_path'] = str(images[0])
    bg = canvas(meta)
    with plt.rc_context({'font.size': 12, 'axes.spines.top': False,
                         'axes.spines.right': False, 'figure.facecolor': 'white'}):
        fig, ax = plt.subplots(figsize=(12, 7))
        ax.set_facecolor('#fafaf8')
        if valid:
            x = np.array([_number(row, 'x_physical') for row in valid])
            y = np.array([_number(row, 'y_physical') for row in valid])
            times = np.array([_number(row, 'elapsed_s') for row in valid])
            xc=np.array([_number(row,'x_cm') for row in valid]);yc=np.array([_number(row,'y_cm') for row in valid])
            scatter = ax.scatter(xc, yc, c=times, s=9, alpha=.4, cmap='cividis',
                                 vmin=start, vmax=end, linewidths=0)
            fig.colorbar(scatter, ax=ax, label='Experiment time (s)')
            stats['outside_screen_fraction'] = float(np.mean((x < 0) | (x > width) | (y < 0) | (y > height)))
        else:
            ax.text(.5, .5, 'No valid gaze predictions in this time range',
                    transform=ax.transAxes, ha='center', va='center',
                    color='white', fontsize=15, bbox=dict(facecolor='#252529', alpha=.85, pad=14))
        ox=_number(rows[0],'export_origin_x_physical',_number(rows[0],'origin_x_physical',width/2)) if rows else width/2
        oy=_number(rows[0],'export_origin_y_physical',_number(rows[0],'origin_y_physical',height/2)) if rows else height/2
        if not np.isfinite([ox,oy]).all():ox,oy=width/2,height/2
        sw,sh=cfg['screen_width_mm']/10,cfg['screen_height_mm']/10
        left,right=-ox/width*sw,(width-ox)/width*sw
        bottom,top=-(height-oy)/height*sh,oy/height*sh
        from matplotlib.patches import Rectangle
        ax.add_patch(Rectangle((left,bottom),sw,sh,fill=False,edgecolor='#a8a8a2',linewidth=1))
        ax.axhline(0,color='#c8c8c2',linewidth=.7);ax.axvline(0,color='#c8c8c2',linewidth=.7)
        ax.scatter([0],[0],marker='+',s=90,c='#222222',zorder=10)
        ax.set(xlim=(left,right),ylim=(bottom,top),xlabel='X from reference origin (cm)',
               ylabel='Y from reference origin (cm)', title=f'Gaze distribution  {start:.2f}–{end:.2f} s')
        ax.set_aspect('equal',adjustable='box')
        fig.tight_layout()
        fig.savefig(output / 'distribution.png', dpi=160)
        plt.close(fig)
        if labeled:
            fig, axes = plt.subplots(2, 1, figsize=(11, 7))
            axes[0].plot(labeled_times, dx/width*sw, label='X (right)', color='#303c45')
            axes[0].plot(labeled_times, -dy/height*sh, label='Y (up)', color='#89978b')
            axes[0].legend(); axes[0].set(xlabel='Experiment time (s)', ylabel='cm from origin')
            axes[1].hist(error_mm/10, bins=min(50, max(5, len(error) // 4)), color='#47585d', alpha=.8)
            axes[1].set(xlabel='Euclidean reference error (cm)', ylabel='Frames')
            fig.tight_layout(); fig.savefig(output / 'errors.png', dpi=160); plt.close(fig)
        elif (output / 'errors.png').exists():
            (output / 'errors.png').unlink()
    emit(45, '散点图与误差图已生成。')
    stats['video_created'] = False
    if make_video and rows and selected_duration > 0:
        fps = 20.
        frame_count = max(1, int(np.ceil(selected_duration * fps)))
        writer = cv2.VideoWriter(str(output / 'gaze_replay.mp4'),
                                 cv2.VideoWriter_fourcc(*'mp4v'), fps,
                                 (bg.shape[1], bg.shape[0]))
        if not writer.isOpened():
            raise RuntimeError('无法创建回放视频；原始视频、坐标和图表仍已保留。')
        gaze_index, capture_index = 0, 0
        scale_x, scale_y = bg.shape[1] / width, bg.shape[0] / height
        protocol_target=None
        if meta.get('mode')=='test' and max(meta.get('protocol_version',0),meta.get('recording_protocol_version',0))>=3:
            from capture import recording_target_state
            protocol_target=lambda now:recording_target_state(meta,cfg,now)
        try:
            for frame_index in range(frame_count):
                now = start + frame_index / fps
                while gaze_index + 1 < len(all_rows) and _number(all_rows[gaze_index + 1], 'elapsed_s') <= now:
                    gaze_index += 1
                while capture_index + 1 < len(all_capture) and _number(all_capture[capture_index + 1], 'elapsed_s') <= now:
                    capture_index += 1
                row = all_rows[gaze_index] if all_rows else None
                sample_age = now - _number(row, 'elapsed_s') if row else float('inf')
                current = row is not None and 0 <= sample_age <= .15 and _valid(row)
                state=protocol_target(now) if protocol_target else None
                if state is not None and not state['eligible']:current=False
                frame = bg.copy()
                if current:
                    x, y = _number(row, 'x_physical'), _number(row, 'y_physical')
                    if 0 <= x < width and 0 <= y < height:
                        cv2.circle(frame, (round(x * scale_x), round(y * scale_y)), 8, (50, 65, 245), -1)
                target = all_capture[capture_index] if all_capture else row
                if state is not None:
                    # The protocol has no target after its planned endpoint.
                    # Actual recording duration can be slightly longer because
                    # the GUI timer stops on its next tick. Keep that tail blank
                    # instead of unpacking None or reviving the previous point.
                    current_target=state.get('target')
                    if current_target is not None:
                        ox,oy=current_target
                        if np.isfinite([ox,oy]).all():cv2.drawMarker(frame,(round(ox*scale_x),round(oy*scale_y)),(65,220,65),cv2.MARKER_CROSS,22,2)
                elif target is not None and _number(target, 'elapsed_s') <= now:
                    ox, oy = _number(target, 'origin_x_physical'), _number(target, 'origin_y_physical')
                    if np.isfinite([ox, oy]).all():
                        cv2.drawMarker(frame, (round(ox * scale_x), round(oy * scale_y)),
                                       (65, 220, 65), cv2.MARKER_CROSS, 22, 2)
                cv2.putText(frame, f'{now:.2f}s  ' + ('VALID' if current else 'NO CURRENT PREDICTION'),
                            (18, 30), cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2)
                writer.write(frame)
                if frame_index % 20 == 0 or frame_index + 1 == frame_count:
                    emit(45 + 50 * (frame_index + 1) / frame_count,
                         f'生成回放视频 {frame_index + 1:,} / {frame_count:,} 帧')
        finally:
            writer.release()
        stats['video_created'] = True
    emit(97, '保存报告与统计结果…')
    _write_report_json(output / 'report.json', stats)
    (output / 'report.html').write_text(_html_report(stats, bool(labeled)), encoding='utf8')
    stats['raw_cleanup']=cleanup_raw_data(session,meta,stats,all_rows,all_capture,retain_raw_rgb=retain_raw_rgb)
    _record_cleanup_result(output / 'report.json', stats)
    emit(100, '报告生成完成。')
    return stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('session')
    parser.add_argument('--no-video', action='store_true')
    parser.add_argument('--start', type=float)
    parser.add_argument('--end', type=float)
    args = parser.parse_args()
    print(json.dumps(report(args.session, not args.no_video,
                            start_s=args.start, end_s=args.end), ensure_ascii=False, indent=2))
