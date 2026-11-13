"""Post-acquisition FAZE inference, with timestamp-preserving CSV and UI progress."""
from pathlib import Path
import csv
import copy
import json
import re
import threading
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import torch

from model_runtime import get_tracker as Tracker,model_family
from progress_feedback import timing_feedback
from coordinates import (CSV_COORDINATE_FIELDS, ANGULAR_METHOD, enrich_session_row,
                         export_session_txt, session_mode, rows_for_protocol)

FIELDS = ['capture_id', 'timestamp_unix_ns', 'timestamp_utc', 'monotonic_ns',
          'elapsed_s', 'valid', 'reason', 'target_id', 'label_valid',
          'x_physical', 'y_physical', 'x_logical', 'y_logical',
          'origin_x_physical', 'origin_y_physical', 'origin_x_logical',
          'origin_y_logical', 'dx_physical', 'dy_physical',
          'abs_dx_physical', 'abs_dy_physical', 'dx_logical', 'dy_logical',
          'distance_mm_est', 'pose_reprojection_px', 'dpr', 'latency_ms']
FIELDS += CSV_COORDINATE_FIELDS
_EXPLICIT_THREAD_LOCK=threading.RLock()


def _prediction_rows(cap, rows, tracker, batch_size):
    """Decode bounded batches and yield every capture row in its original order."""
    last_index=-1
    for offset in range(0,len(rows),batch_size):
        selected=rows[offset:offset+batch_size]
        frames=[];started=[]
        for row in selected:
            started.append(time.perf_counter())
            video_index=int(row['video_index'])
            if video_index>=0:
                if video_index!=last_index+1:cap.set(cv2.CAP_PROP_POS_FRAMES,video_index)
                ok,frame=cap.read();last_index=video_index
            else:ok,frame=False,None
            frames.append(frame if ok else None)
        if batch_size>1 and callable(getattr(tracker,'infer_batch',None)):
            results=tracker.infer_batch(frames)
            if len(results)!=len(selected):raise RuntimeError('批量推理结果数量与采集帧不一致')
        else:
            results=[]
            for frame in frames:
                try:
                    if frame is None:raise ValueError('对应视频帧缺失或读取失败')
                    results.append(tracker.infer(frame))
                except ValueError as error:results.append(error)
        for relative,(row,result) in enumerate(zip(selected,results)):
            failed=isinstance(result,ValueError)
            reason=str(result) if failed else ''
            prediction=None if failed else result
            if prediction is not None and not np.isfinite([prediction['x'],prediction['y']]).all():
                prediction=None;reason='预测坐标不是有限值'
            yield offset+relative,row,prediction,reason,(time.perf_counter()-started[relative])*1000


def infer_session(folder, progress=print, force=False, *, on_progress=None, device='cpu', batch_size=None,
                  cpu_threads=None):
    """Run one record; an explicit CPU budget is restored after this analysis."""
    options=dict(progress=progress,force=force,on_progress=on_progress,device=device,batch_size=batch_size)
    if cpu_threads is None:return _infer_session(folder,**options)
    if isinstance(cpu_threads,bool) or not isinstance(cpu_threads,int) or not 1<=cpu_threads<=256:
        raise ValueError('CPU线程数必须为1至256的整数')
    from affnet_tracker import _ensure_default_threads,configure_torch_threads
    with _EXPLICIT_THREAD_LOCK:
        _ensure_default_threads()
        previous=torch.get_num_threads()
        configure_torch_threads(cpu_threads)
        try:return _infer_session(folder,**options)
        finally:configure_torch_threads(previous)


def _infer_session(folder, progress=print, force=False, *, on_progress=None, device='cpu', batch_size=None):
    """Infer all acquired frames. ``on_progress(int, str)`` reports 0–100.

    The legacy one-argument text callback is retained. No model code is run by
    acquisition; this function is invoked only after the camera recording ends.
    An uncalibrated session uses the original DTED head and geometric projection.
    """
    from record_layout import _folder, _local, read_capture_rows, verified_result
    device=str(device).strip().lower()
    if re.fullmatch(r'cpu|cuda(?::[0-9]+)?',device) is None:
        raise ValueError('推理设备应为 cpu、cuda 或 cuda:编号')
    batch_size=(16 if device.startswith('cuda') else 1) if batch_size is None else batch_size
    if isinstance(batch_size,bool) or not isinstance(batch_size,int) or not 1<=batch_size<=64:
        raise ValueError('批量大小必须为1至64的整数')
    folder = _folder(folder)
    previous = -1

    def emit(percent, message):
        nonlocal previous
        percent = max(previous, min(100, max(0, int(percent))))
        previous = percent
        if progress is not None:
            progress(message)
        if on_progress is not None:
            on_progress(percent, message)

    emit(0, '读取实验记录…')
    if not (folder / 'session.json').is_file():
        raise ValueError('所选文件夹缺少 session.json，请选择一次完整的实验记录。')
    meta = json.loads(_local(folder, 'session.json').read_text(encoding='utf8'))
    from deferred_calibration import PROTOCOL, fit_material
    deferred = (meta.get('calibration') or {}).get('protocol') == PROTOCOL
    calibration_pending = deferred and (meta.get('calibration') or {}).get('status') != 'complete'
    if meta.get('storage_layout') == 'compact_v1' and not force and not calibration_pending:
        cached = verified_result(folder)
        if cached is not None:
            emit(100, '已找到完成的 20 Hz 数据。')
            return cached
    complete = folder / 'inference_complete.json'
    if complete.is_file() and (folder / 'gaze.csv').is_file() and not force and not calibration_pending:
        result = json.loads(complete.read_text(encoding='utf8'))
        with (folder/'gaze.csv').open(encoding='utf-8-sig',newline='') as file:cached=list(csv.DictReader(file))
        cached=[enrich_session_row(row,meta) for row in rows_for_protocol(meta,cached)]
        result['coordinate_export']=export_session_txt(folder,cached,meta['config'],meta=meta)
        emit(100, '已找到完成的推理结果。')
        return result
    if not (folder / 'camera.mp4').is_file():
        # A finished, imported CSV can be analysed without keeping camera video.
        if (folder / 'gaze.csv').is_file() and not force and not calibration_pending:
            with (folder / 'gaze.csv').open(encoding='utf-8-sig') as file:
                if sum(1 for _ in file) > 1:
                    with (folder/'gaze.csv').open(encoding='utf-8-sig',newline='') as source:cached=list(csv.DictReader(source))
                    cached=[enrich_session_row(row,meta) for row in rows_for_protocol(meta,cached)]
                    exported=export_session_txt(folder,cached,meta['config'],meta=meta)
                    emit(100, '使用已有注视坐标数据。')
                    return {'mode': 'imported_csv','coordinate_export':exported}
        raise ValueError('记录中缺少 camera.mp4，不能重新进行离线推理。')
    rows = read_capture_rows(folder, meta)
    source_frames=len(rows)
    rows=rows_for_protocol(meta,rows)
    if not rows:
        if meta.get('direction_task_enabled') is True:
            raise ValueError('本次记录没有可分析的数据：所有测试点均因方向错误或未完成作答而被排除。')
        raise ValueError('本次记录没有采集到视频帧，请先完成一次实验。')

    cfg = meta['config']
    calibration = meta.get('calibration') or {}
    uncalibrated = int(calibration.get('target_count',
                                     5 if calibration.get('affine') is not None else 0)) == 0
    emit(2, '加载 '+model_family(meta['model_id'])+'…')
    runtime_cfg=copy.deepcopy(cfg)
    runtime_cfg['inference_device']=device
    tracker = Tracker(meta['model_id'], runtime_cfg)
    cap = None
    valid = 0
    started = time.perf_counter()
    temp = folder / 'gaze.partial.csv'
    try:
        expected_hash = calibration.get('model_sha256') or meta.get('model_sha256')
        if not expected_hash:
            raise ValueError('实验记录缺少模型校验值，无法确认本次使用的权重。')
        if tracker.model_hash != expected_hash:
            raise ValueError('当前模型与采集时的模型不同，请恢复相应权重后再分析。')
        if calibration.get('runtime_sha256') and calibration['runtime_sha256']!=getattr(tracker,'runtime_hash',None):
            raise ValueError('AFF-Net推理文件与本次个人校准不匹配。')
        tracker.affine = None
        tracker.head = None
        if deferred and calibration_pending:
            emit(3, '开始处理原始校准素材')
            calibration = fit_material(folder, meta, tracker,
                on_progress=lambda percent,text:emit(3+int(percent*.27),text))
        elif not uncalibrated:
            if calibration.get('affine') is not None:
                tracker.affine = np.asarray(calibration['affine'], dtype=float)
            if (folder / 'personal_maml.npz').is_file():
                with np.load(folder / 'personal_maml.npz') as file:
                    tracker.head = {key: torch.from_numpy(file[key].copy()) for key in file.files}
            elif calibration.get('head_adapted'):
                raise ValueError('记录中缺少个人校准参数 personal_maml.npz。')
        cap = cv2.VideoCapture(str(folder / 'camera.mp4'))
        if not cap.isOpened():
            raise ValueError('无法打开本次摄像头视频，请检查文件是否完整。')
        formal_start = 32 if deferred else 5
        emit(formal_start, '开始逐帧推理…')
        with temp.open('w', encoding='utf-8-sig', newline='') as file:
            writer = csv.DictWriter(file, FIELDS)
            writer.writeheader()
            inference_started = time.perf_counter()
            for index,row,prediction,reason,latency_ms in _prediction_rows(cap,rows,tracker,batch_size):
                if prediction is not None:valid+=1
                x = prediction['x'] if prediction else float('nan')
                y = prediction['y'] if prediction else float('nan')
                ox = float(row.get('origin_x_physical') or 'nan')
                oy = float(row.get('origin_y_physical') or 'nan')
                dx, dy = x - ox, y - oy
                dpr = float(cfg['dpr'])
                unix_ns = int(row['timestamp_unix_ns'])
                output = dict(
                    capture_id=row['capture_id'], timestamp_unix_ns=unix_ns,
                    timestamp_utc=datetime.fromtimestamp(unix_ns / 1e9, timezone.utc).isoformat(),
                    monotonic_ns=row['monotonic_ns'], elapsed_s=row['elapsed_s'],
                    valid=int(prediction is not None), reason=reason,
                    target_id=row.get('target_id', -1), label_valid=row.get('label_valid', '0'),
                    x_physical=x, y_physical=y, x_logical=x / dpr, y_logical=y / dpr,
                    origin_x_physical=ox, origin_y_physical=oy,
                    origin_x_logical=ox / dpr, origin_y_logical=oy / dpr,
                    dx_physical=dx, dy_physical=dy, abs_dx_physical=abs(dx), abs_dy_physical=abs(dy),
                    dx_logical=dx / dpr, dy_logical=dy / dpr,
                    distance_mm_est=prediction['distance_mm'] if prediction else '',
                    pose_reprojection_px=prediction['reprojection_px'] if prediction else '',
                    dpr=dpr, latency_ms=latency_ms)
                output=enrich_session_row(output,meta,prediction.get('origin') if prediction else None)
                writer.writerow(output)
                if index % 20 == 0 or index + 1 == len(rows):
                    emit(formal_start + (99-formal_start) * (index + 1) / len(rows),
                         f'离线推理 {index + 1:,} / {len(rows):,} 帧，有效 {valid:,} 帧  '
                         + timing_feedback(index + 1, len(rows), time.perf_counter() - inference_started))
        temp.replace(folder / 'gaze.csv')
        with (folder/'gaze.csv').open(encoding='utf-8-sig',newline='') as file:inferred_rows=list(csv.DictReader(file))
        exported=export_session_txt(folder,inferred_rows,cfg,meta=meta)
        result = dict(frames=len(rows), source_frames=source_frames, transition_frames_excluded=source_frames-len(rows),valid=valid,
                      processing_seconds=time.perf_counter() - started,
                      mode='offline', uncalibrated=uncalibrated,
                      inference_device=str(getattr(tracker,'device',device)), inference_batch_size=batch_size,
                      inference_cpu_threads=torch.get_num_threads(), inference_precision='float32',
                      model_sha256=tracker.model_hash,coordinate_export=exported,
                      session_mode=session_mode(meta),model_id=meta['model_id'],export_complete=True)
        if session_mode(meta)=='test':result['angular_error_method']=ANGULAR_METHOD
        complete.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    finally:
        if cap is not None:
            cap.release()
        tracker.close()
    emit(100, '离线推理完成。')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('session')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--batch-size', type=int)
    parser.add_argument('--cpu-threads', type=int)
    args = parser.parse_args()
    infer_session(args.session, force=args.force, device=args.device, batch_size=args.batch_size,
                  cpu_threads=args.cpu_threads)
