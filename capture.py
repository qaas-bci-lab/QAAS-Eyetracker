"""Camera acquisition and calibration for the PyQt6 UI.

Formal recordings contain camera frames and host-arrival timestamps only.  The
inference thread is gated by ``phase == 'calibration'`` in both its producer and
consumer paths.  Zero-point plans never instantiate the FAZE/MediaPipe tracker.
"""
from __future__ import annotations

import csv
import copy
import math
import hashlib
import json
import queue
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path

import cv2
from PyQt6.QtCore import QThread, pyqtSignal
from app_paths import APP_EDITION, RESOURCE_ROOT

ROOT = RESOURCE_ROOT
# Lazy dependency and injectable seam for synthetic camera tests.  Importing this
# acquisition module does not import torch or initialize MediaPipe.
Tracker = None


def open_working_camera(cfg):
    """Select a backend only after it returns an actual image."""
    index = int(cfg.get('camera_index', 0))
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == 'win32' else [cv2.CAP_ANY]
    failures = []
    for backend in backends:
        camera = cv2.VideoCapture(index, backend)
        working = False
        try:
            if not camera.isOpened():
                failures.append('无法打开')
                continue
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg.get('camera_width', 1280)))
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg.get('camera_height', 720)))
            camera.set(cv2.CAP_PROP_FPS, float(cfg.get('camera_fps', 30)))
            for _ in range(10):
                ok, frame = camera.read()
                if ok and frame is not None and getattr(frame, 'size', 0) and len(frame.shape) == 3:
                    working = True
                    return camera, frame
                time.sleep(.05)
            failures.append('已打开但无法读取画面')
        except Exception as error:
            failures.append(str(error))
        finally:
            if not working:
                camera.release()
    raise RuntimeError('摄像头无法读取画面（' + '；'.join(failures) + '）。请检查相机编号、Windows桌面应用相机权限及其他程序占用。')

GAZE_FIELDS = [
    'capture_id', 'timestamp_unix_ns', 'timestamp_utc', 'monotonic_ns', 'elapsed_s',
    'valid', 'reason', 'target_id', 'label_valid', 'x_physical', 'y_physical',
    'x_logical', 'y_logical', 'origin_x_physical', 'origin_y_physical',
    'origin_x_logical', 'origin_y_logical', 'dx_physical', 'dy_physical',
    'abs_dx_physical', 'abs_dy_physical', 'dx_logical', 'dy_logical',
    'distance_mm_est', 'pose_reprojection_px', 'dpr', 'latency_ms',
]
CAPTURE_FIELDS = [
    'capture_id', 'timestamp_unix_ns', 'monotonic_ns', 'elapsed_s', 'video_index',
    'target_id', 'origin_x_physical', 'origin_y_physical', 'label_valid',
]

# Unversioned snapshots and recording metadata retain their historical gates.
CALIBRATION_DWELL_S=3.0
CALIBRATION_WINDOW_START_S=1.0
CALIBRATION_WINDOW_END_S=2.5
TRANSITION_EXCLUSION_S=.5
DEFAULT_CALIBRATION_DWELL_S=2.0
DEFAULT_CALIBRATION_EXCLUDE_START_MS=500.0
DEFAULT_CALIBRATION_EXCLUDE_END_MS=0.0
CALIBRATION_FRAME_INTERVAL_NS=50_000_000


def calibration_timing(cfg):
    """Resolve one point's dwell and half-open capture window in seconds."""
    values=[]
    for key, default in [('calibration_dwell_s',DEFAULT_CALIBRATION_DWELL_S),
                         ('calibration_exclude_start_ms',DEFAULT_CALIBRATION_EXCLUDE_START_MS),
                         ('calibration_exclude_end_ms',DEFAULT_CALIBRATION_EXCLUDE_END_MS)]:
        value=cfg.get(key,default)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('校准计时参数必须为有限数值')
        values.append(float(value))
    dwell,start_ms,end_ms=values
    start,end=start_ms/1000.,dwell-end_ms/1000.
    if dwell<=0 or start_ms<0 or end_ms<0 or start>=end:
        raise ValueError('校准点时长必须大于前后不计入时间之和')
    return dwell,start,end


def recording_target_state(meta, cfg, elapsed):
    """Resolve the reference and settling gate from the capture clock.

    Protocol-3 test dwell times include the settling interval. The first point
    settles too. Missing mode means the old external-target recording interface,
    whose unchanged behaviour is retained for historical integrations.
    """
    mode=meta.get('mode',meta.get('scheme',{}).get('mode'))
    if mode not in ('experiment','test'):
        return None
    elapsed=float(elapsed)
    points=meta.get('points',meta.get('scheme',{}).get('points',[]))
    if mode=='experiment':
        point=meta.get('origin',meta.get('scheme',{}).get('origin',dict(x=.5,y=.5)))
        duration=float(meta.get('planned_duration_s',float(meta.get('scheme',{}).get('duration_minutes',8))*60))
        return dict(target_id=0,target=[float(point['x'])*float(cfg['physical_width']),float(point['y'])*float(cfg['physical_height'])],
                    target_since_s=0.,point_elapsed_s=elapsed,eligible=0<=elapsed<duration,
                    exclusion_reason='' if 0<=elapsed<duration else 'outside_protocol')
    if not points:
        raise ValueError('测试模式至少需要一个测试点')
    start=0.
    settling=float(meta.get('transition_exclusion_s',meta.get('scheme',{}).get('transition_exclusion_s',TRANSITION_EXCLUSION_S)))
    if not math.isfinite(settling) or settling<0:
        raise ValueError('注视稳定等待时间必须为非负有限数值')
    for index,point in enumerate(points):
        duration=float(point['seconds'])
        if not math.isfinite(duration) or duration<=settling:
            raise ValueError('每个测试点的时间必须大于切点稳定时间')
        end=start+duration
        if 0<=elapsed<end:
            age=elapsed-start
            eligible=age+1e-12>=settling
            return dict(target_id=index,target=[float(point['x'])*float(cfg['physical_width']),float(point['y'])*float(cfg['physical_height'])],
                        target_since_s=start,point_elapsed_s=age,eligible=eligible,
                        exclusion_reason='' if eligible else 'transition')
        start=end
    return dict(target_id=-1,target=None,target_since_s=start,point_elapsed_s=elapsed-start,
                eligible=False,exclusion_reason='outside_protocol')


def direction_recording_target_state(meta, cfg, snapshot, now):
    """Use UI-set targets while a direction task waits for input or counts down."""
    points=meta.get('points',meta.get('scheme',{}).get('points',[]))
    settling=float(meta.get('transition_exclusion_s',meta.get('scheme',{}).get('transition_exclusion_s',TRANSITION_EXCLUSION_S)))
    if not math.isfinite(settling) or settling<0:
        raise ValueError('注视稳定等待时间必须为非负有限数值')
    index=snapshot.get('target_id',-1)
    target=snapshot.get('target')
    target_since=float(snapshot.get('target_since',now))
    age=max(0.,float(now)-target_since)
    if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<len(points) or target is None:
        return dict(target_id=-1,target=None,target_since_s=target_since-snapshot['record']['start'],
                    point_elapsed_s=age,eligible=False,exclusion_reason='outside_protocol')
    duration=float(points[index]['seconds'])
    if not math.isfinite(duration) or duration<=settling:
        raise ValueError('每个测试点的时间必须大于切点稳定时间')
    eligible=age+1e-12>=settling and age<duration
    reason='transition' if age+1e-12<settling else ('outside_protocol' if age>=duration else '')
    return dict(target_id=index,target=target,target_since_s=target_since-snapshot['record']['start'],
                point_elapsed_s=age,eligible=eligible,exclusion_reason=reason)


def calibration_sample_eligible(capture_ns,snapshot,current_target_id):
    """Eligibility follows acquisition time and the same target, never inference finish time."""
    age=capture_ns/1e9-snapshot['target_since']
    if snapshot['target_id']!=current_target_id:
        return False
    if 'calibration_window_s' in snapshot:
        start,end=snapshot['calibration_window_s']
        return start<=age<end
    # Existing integrations can still pass the old, unversioned snapshot shape.
    return CALIBRATION_WINDOW_START_S<=age<=CALIBRATION_WINDOW_END_S


def _save_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.partial')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


def _raw_model_identity(model_id):
    path = ROOT / 'capture_model_identity.json'
    identity = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(identity, dict) or identity.get('model_id') != model_id:
        raise ValueError('采集程序的模型标识与实验方案不一致')
    for key in ('model_sha256', 'runtime_sha256'):
        digest = identity.get(key)
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('采集程序的模型校验信息无效')
    return identity


def _model_digest(model_id):
    if APP_EDITION == 'capture_raw':
        return _raw_model_identity(model_id)['model_sha256']
    model_root = ROOT / 'models'
    model_path = (model_root / str(model_id) / 'backbone.pt').resolve()
    if not model_path.is_relative_to(model_root.resolve()):
        raise ValueError('无效的模型位置')
    digest = hashlib.sha256()
    with model_path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _runtime_digest(model_id):
    if APP_EDITION == 'capture_raw':
        return _raw_model_identity(model_id)['runtime_sha256']
    from deferred_calibration import digest_file
    root = (ROOT / 'models').resolve()
    path = (root / str(model_id) / 'model_cpu.ts').resolve()
    if not path.is_relative_to(root):
        raise ValueError('无效的模型位置')
    return digest_file(path) if path.is_file() else None


class Worker(QThread):
    preview = pyqtSignal(object)
    status = pyqtSignal(str)
    calibrated = pyqtSignal(object)
    failed = pyqtSignal(str)
    sample = pyqtSignal(object)  # Retained for existing integrations.
    ready = pyqtSignal()
    calibration_progress = pyqtSignal(int, str)

    def __init__(self, model_id, cfg):
        super().__init__()
        self.model_id = model_id
        self.cfg = dict(cfg)
        self.calibration_points = int(cfg.get('calibration_points', 9))
        if self.calibration_points not in (0, 5, 9):
            raise ValueError('校准点数只能为0、5或9')
        self.calibration_dwell_s,self.calibration_window_start_s,self.calibration_window_end_s=calibration_timing(cfg)
        self.running = True
        self.phase = 'preview'
        self.tracker = None
        self.model_hash = None
        self.runtime_hash = None
        self.defer_calibration = APP_EDITION == 'capture_raw' or cfg.get('defer_calibration', False) is True
        self.calibration_folder = None
        self.calibration_metadata = None
        self.post_calibration_folder = None
        self.pre_calibration_material = None
        self.post_calibration_material = None
        self.calibration_targets = {}
        self.calibration_saved = False
        self.calibration_stop_reason = 'worker_stopped'
        self.deferred_summary = None
        self.calibration_frame_bytes = 0
        self.camera_environment = {}
        self.camera = None
        self.capture_thread = None
        self.is_camera_ready = False
        self.total_capture = 0
        self.target = None
        self.target_id = -1
        self.target_since = 0.0
        self.cal_samples = []
        self.calibration_frames=[]
        self._last_calibration_capture={}
        self.record = None
        self.last_record_folder = None
        self._formal_record_started = False
        self.q = queue.Queue(maxsize=2)
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self._fit_event = threading.Event()
        self._calibration_generation = 0

    @property
    def fit_requested(self):
        return self._fit_event.is_set()

    @fit_requested.setter
    def fit_requested(self, value):
        if value:
            self.request_calibration_fit()
        else:
            self._fit_event.clear()

    def set_target(self, target_id, xy):
        with self.lock:
            self.target_id = int(target_id)
            self.target = list(xy) if xy is not None else None
            self.target_since = time.perf_counter()
            if self.calibration_folder is not None and self.phase == 'calibration' and xy is not None:
                self.calibration_targets[self.target_id] = dict(target_id=self.target_id,
                    target=list(xy), started_monotonic_s=self.target_since)

    def prepare_calibration_storage(self, folder, metadata):
        from record_layout import _folder

        folder = Path(folder) if self.defer_calibration else _folder(folder)
        with self.lock:
            if self.phase in ('calibration', 'fitting', 'recording') or self.post_calibration_folder is not None:
                raise RuntimeError('当前采集已开始，不能更换校准存储位置')
            self.calibration_folder = folder
            self.calibration_metadata = copy.deepcopy(metadata)
            self.pre_calibration_material = None
            self.calibration_saved = False
            self._formal_record_started = False
            self.last_record_folder = folder

    def _prepare_raw_calibration(self, folder, metadata, role):
        from record_layout import _local

        self.calibration_folder = _local(folder, f'{role}_calibration')
        self.calibration_metadata = copy.deepcopy({
            key: metadata.get(key) for key in ('model_id', 'config', 'participant',
                                              'experiment_session_id', 'segment_index', 'segment_count')
        })
        self.calibration_metadata.update(mode='calibration', calibration_id=uuid.uuid4().hex,
                                         calibration_role=role, segment_folder=folder.name)

    def prepare_post_calibration_storage(self, folder):
        """Keep a segment's final calibration separate from its fitted input."""
        from record_layout import _folder, _json, _local

        folder = _folder(folder)
        with self.lock:
            if self.phase != 'preview' or self.record is not None or self.post_calibration_folder is not None:
                raise RuntimeError('请在本段录制保存后开始末次校准')
            metadata = _json(_local(folder, 'session.json'))
            if metadata.get('acquisition_state') != 'recorded':
                raise ValueError('本段正式录制尚未保存')
            self._prepare_raw_calibration(folder, metadata, 'post')
            # A stop during countdown precedes the reset in begin_calibration().
            self.calibration_targets = {}
            self.calibration_frames = []
            self.calibration_frame_bytes = 0
            self.calibration_saved = False
            self.post_calibration_material = None
            self.post_calibration_folder = folder

    def _commit_post_calibration(self, result, stop_reason=None):
        from record_layout import _merge

        summary = (self.post_calibration_material or {}) | result
        if stop_reason:
            summary['stop_reason'] = stop_reason
        _merge(self.post_calibration_folder, {'post_calibration': summary})
        self.post_calibration_folder = None
        self.post_calibration_material = None
        self.calibration_folder = None
        self.calibration_metadata = None

    def _persist_calibration(self, completed, stop_reason=None):
        from deferred_calibration import save_material
        from record_layout import _merge
        if self.calibration_folder is None or self.calibration_metadata is None:
            raise RuntimeError('尚未设置原始校准素材的保存位置')
        with self.lock:
            clips = list(self.calibration_frames)
            targets = [self.calibration_targets[index] for index in sorted(self.calibration_targets)]
            self.calibration_metadata.setdefault('acquisition_environment', {}).setdefault('camera', {}).update(self.camera_environment)
        immediate_pre = not self.defer_calibration and self.post_calibration_folder is None
        result = save_material(self.calibration_folder, self.calibration_metadata, clips, targets,
                               model_hash=self.model_hash, runtime_hash=self.runtime_hash,
                               completed=completed, stop_reason=stop_reason,
                               summary_key='pre_calibration_material' if immediate_pre else 'calibration',
                               update_session_state=not immediate_pre)
        if self.post_calibration_folder is not None:
            self.post_calibration_material = result | {'material_folder': self.calibration_folder.name}
            if self.defer_calibration:
                self._commit_post_calibration(result, stop_reason)
            else:
                _merge(self.post_calibration_folder, {'post_calibration': self.post_calibration_material})
        elif self.defer_calibration:
            self.deferred_summary = result
        else:
            self.pre_calibration_material = result
        self.calibration_saved = True
        self.calibration_frames = []
        self.calibration_frame_bytes = 0
        return result

    def begin_calibration(self):
        with self.inference_lock, self.lock:
            if not self.calibration_points or (self.tracker is None and not self.defer_calibration):
                raise ValueError('0点方案不运行个人校准，请直接开始采集')
            if self.defer_calibration and self.calibration_folder is None:
                raise RuntimeError('原始校准素材保存位置尚未准备好')
            if self.record is not None:
                raise RuntimeError('录制期间不能开始校准')
            self._fit_event.clear()
            self._calibration_generation += 1
            self.cal_samples = []
            self.calibration_frames=[]
            self._last_calibration_capture={}
            self.calibration_frame_bytes = 0
            self.calibration_targets = {}
            self.calibration_saved = False
            self.deferred_summary = None
            if self.tracker is not None:
                self.tracker.head = None
                self.tracker.affine = None
            self.phase = 'calibration'
            self._drain_queue()

    def request_calibration_fit(self):
        with self.lock:
            if (self.tracker is None and not self.defer_calibration) or self.phase != 'calibration':
                raise RuntimeError('当前没有可拟合的校准流程')
            self._fit_event.set()

    def begin_record(self, folder, meta):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        with self.inference_lock, self.lock:
            if not self.is_camera_ready or not self.running:
                raise RuntimeError('摄像头尚未就绪')
            if self.record is not None:
                raise RuntimeError('已有录制正在进行')
            if self._fit_event.is_set() or self.phase in ('calibration', 'fitting') or self.post_calibration_folder is not None:
                raise RuntimeError('请等待校准完成后再开始采集')
            if not self.cfg.get('record_video', True):
                raise ValueError('离线推理必须保存摄像头视频')
            if self.defer_calibration and self.calibration_points:
                from deferred_calibration import binding
                if not self.deferred_summary or self.deferred_summary['status'] != 'pending':
                    raise ValueError('请先完成原始校准素材采集')
                if folder.resolve() != self.calibration_folder.resolve() or binding(meta) != binding(self.calibration_metadata):
                    raise ValueError('校准素材与正式记录的人员或配置不一致')
                meta['calibration'] = copy.deepcopy(self.deferred_summary)
            if self.pre_calibration_material is not None:
                meta['pre_calibration_material'] = copy.deepcopy(self.pre_calibration_material)
            # Reject an invalid new protocol before opening the recording files.
            recording_target_state(meta,self.cfg,0.)
            f = cf = None
            try:
                f = (folder / 'gaze.csv').open('w', newline='', encoding='utf-8-sig')
                writer = csv.DictWriter(f, GAZE_FIELDS)
                writer.writeheader()
                cf = (folder / 'capture.csv').open('w', newline='', encoding='utf-8-sig')
                capture_writer = csv.writer(cf)
                capture_writer.writerow(CAPTURE_FIELDS)
                start_ns = time.perf_counter_ns()
                self.record = dict(
                    folder=folder, meta=meta, start=start_ns / 1e9, start_ns=start_ns,
                    file=f, writer=writer, cfile=cf, cwriter=capture_writer,
                    video=None, video_index=0, closing=False,
                    transition_skipped_frames=0,outside_protocol_skipped_frames=0,
                    source_count=0, source_first_ns=None, source_last_ns=None,
                    video_first_ns=None, video_last_ns=None,
                )
                meta['acquisition_state'] = 'recording'
                _save_json(folder / 'session.json', meta)
                self._formal_record_started = True
                self.phase = 'recording'
                self._drain_queue()
            except Exception:
                if f is not None:
                    f.close()
                if cf is not None:
                    cf.close()
                raise
            return self.record['start']

    def end_record(self, reason):
        with self.lock:
            r = self.record
            if r is None:
                return None
            self.phase = 'preview'
            r['closing'] = True
            end = time.perf_counter()
            try:
                if r['video'] is not None:
                    r['video'].release()
            finally:
                r['file'].close()
                r['cfile'].close()
                self.record = None
            r['meta'].update(
                actual_duration_s=max(0.0, end - r['start']),
                stop_reason=reason,
                recorded_frames=r['video_index'],
                transition_skipped_frames=r['transition_skipped_frames'],
                outside_protocol_skipped_frames=r['outside_protocol_skipped_frames'],
                acquisition_state='recorded',
            )
            def measured(count, first, last):
                return (count-1)*1e9/(last-first) if count>1 and last and first is not None and last>first else None
            camera = r['meta'].setdefault('acquisition_environment', {}).setdefault('camera', {})
            camera.update(self.camera_environment)
            camera['timing'] = dict(source_frame_count=r['source_count'], retained_frame_count=r['video_index'],
                source_first_monotonic_ns=r['source_first_ns'], source_last_monotonic_ns=r['source_last_ns'],
                retained_first_monotonic_ns=r['video_first_ns'], retained_last_monotonic_ns=r['video_last_ns'],
                source_arrival_fps=measured(r['source_count'],r['source_first_ns'],r['source_last_ns']),
                retained_arrival_fps=measured(r['video_index'],r['video_first_ns'],r['video_last_ns']),
                timestamp_domain='host arrival: Unix UTC nanoseconds and perf_counter monotonic nanoseconds',
                authoritative_timestamps='capture.csv')
            _save_json(r['folder'] / 'session.json', r['meta'])
            self.last_record_folder = r['folder']
            if r['meta'].get('storage_layout') == 'compact_v1':
                from record_layout import compact_pending
                compact_pending(r['folder'])
            return r['folder']

    def snapshot(self, capture_ns=None):
        with self.lock:
            now_ns = capture_ns if capture_ns is not None else time.perf_counter_ns()
            return dict(
                phase=self.phase, target_id=self.target_id,
                target=list(self.target) if self.target is not None else None,
                target_since=self.target_since, record=self.record,
                generation=self._calibration_generation,
                calibration_window_s=[self.calibration_window_start_s,self.calibration_window_end_s],
                elapsed=(now_ns - self.record['start_ns']) / 1e9 if self.record else 0.0,
            )

    def stop(self):
        self.running = False
        self.requestInterruption()

    def _drain_queue(self):
        while True:
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def _open_camera(self):
        camera, _ = open_working_camera(self.cfg)
        return camera

    def capture(self):
        cid = 0
        last_preview = 0.0
        next_selected = 0.0
        try:
            self.camera = self._open_camera()
            try:
                backend = self.camera.getBackendName()
            except (AttributeError, cv2.error):
                backend = 'unknown'
            self.camera_environment = dict(index=int(self.cfg.get('camera_index',0)),
                name=self.cfg.get('camera_name') or None, backend=backend)
            self.is_camera_ready = True
            self.status.emit('摄像头已就绪')
            self.ready.emit()
            misses = 0
            while self.running and not self.isInterruptionRequested():
                ok, frame = self.camera.read()
                unix = time.time_ns()
                mono = time.perf_counter_ns()
                now = mono / 1e9
                if not ok or frame is None or not getattr(frame, 'size', 0):
                    misses += 1
                    if misses > 30:
                        raise RuntimeError('摄像头持续读取失败，已停止当前采集')
                    time.sleep(.02)
                    continue
                misses = 0
                self.camera_environment.update(actual_width=int(frame.shape[1]), actual_height=int(frame.shape[0]))
                with self.lock:
                    source_record = self.record
                    if source_record is not None and not source_record['closing']:
                        source_record['source_count'] += 1
                        source_record['source_first_ns'] = source_record['source_first_ns'] if source_record['source_first_ns'] is not None else mono
                        source_record['source_last_ns'] = mono
                if not self.cfg.get('max_fps', True):
                    if now < next_selected:
                        continue
                    next_selected = max(next_selected + .05, now - .05)
                cid += 1
                self.total_capture = cid
                with self.lock:
                    st = self.snapshot(mono)
                    rec = st['record']
                    if rec is not None and not rec['closing']:
                        if rec['meta'].get('direction_task_enabled') is True:
                            protocol=direction_recording_target_state(rec['meta'],self.cfg,st,now)
                        else:
                            protocol=recording_target_state(rec['meta'],self.cfg,st['elapsed'])
                        if protocol is not None:
                            if not protocol['eligible']:
                                counter='transition_skipped_frames' if protocol['exclusion_reason']=='transition' else 'outside_protocol_skipped_frames'
                                rec[counter]+=1
                                continue
                            st['target_id']=protocol['target_id']
                            st['target']=protocol['target']
                            st['target_since']=rec['start']+protocol['target_since_s']
                        if rec['video'] is None:
                            h, w = frame.shape[:2]
                            fps = 30 if self.cfg.get('max_fps', True) else 20
                            rec['video'] = cv2.VideoWriter(
                                str(rec['folder'] / 'camera.mp4'),
                                cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h),
                            )
                            if not rec['video'].isOpened():
                                raise RuntimeError('无法创建摄像头视频，请检查存储空间和写入权限')
                            rec['meta']['camera_recording'] = dict(
                                width=w, height=h, container_fps=fps,
                                timing='capture.csv host-arrival timestamps are authoritative',
                            )
                            self.camera_environment.update(container_fps=fps,
                                container_fps_is_measured=False)
                        rec['video'].write(frame)
                        video_index = rec['video_index']
                        rec['video_index'] += 1
                        rec['video_first_ns'] = rec['video_first_ns'] if rec['video_first_ns'] is not None else mono
                        rec['video_last_ns'] = mono
                        target = st['target'] if st['target'] is not None else [float('nan')] * 2
                        label = int(
                            rec['meta'].get('assume_fixation', False)
                            and st['target'] is not None
                            and (protocol['eligible'] if protocol is not None
                                 else now - st['target_since'] >= TRANSITION_EXCLUSION_S)
                        )
                        rec['cwriter'].writerow([
                            cid, unix, mono, st['elapsed'], video_index,
                            st['target_id'], *target, label,
                        ])
                if st['phase'] == 'recording':
                    # No model, landmark detector, normalized crop, or preview
                    # conversion belongs on the formal acquisition path.
                    continue
                if st['phase'] == 'calibration':
                    if st['target'] is not None and calibration_sample_eligible(mono,st,st['target_id']):
                        key=(st['generation'],st['target_id'])
                        # Bounded compressed clips preserve capture-time selection
                        # even when FAZE is slower than the camera.
                        if mono-self._last_calibration_capture.get(key,0)>=CALIBRATION_FRAME_INTERVAL_NS:
                            ok,encoded=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,95])
                            if ok:
                                with self.lock:
                                    if self.phase=='calibration' and st['generation']==self._calibration_generation:
                                        if self.calibration_folder is not None:
                                            from deferred_calibration import MAX_TOTAL_BYTES, MAX_FRAMES
                                            if self.calibration_frame_bytes + len(encoded) > MAX_TOTAL_BYTES or len(self.calibration_frames) >= MAX_FRAMES:
                                                raise RuntimeError('校准素材达到容量限制，请缩短每点时长')
                                        st['timestamp_unix_ns'] = unix
                                        self.calibration_frames.append((encoded.tobytes(),mono,st))
                                        self.calibration_frame_bytes += len(encoded)
                                        self._last_calibration_capture[key]=mono
                if now - last_preview > .15:
                    self.preview.emit(frame.copy())
                    last_preview = now
        except Exception as error:
            self.running = False
            self.failed.emit(str(error))
        finally:
            self.is_camera_ready = False
            if self.camera is not None:
                self.camera.release()

    def run(self):
        try:
            if self.calibration_points and not self.defer_calibration:
                tracker_class = Tracker
                if tracker_class is None:
                    from model_runtime import get_tracker as tracker_class
                self.status.emit('正在准备个人校准模型…')
                self.tracker = tracker_class(self.model_id, self.cfg)
                self.model_hash = self.tracker.model_hash
                self.runtime_hash = getattr(self.tracker, 'runtime_hash', None)
            else:
                self.status.emit('正在准备摄像头')
                self.model_hash = _model_digest(self.model_id)
                self.runtime_hash = _runtime_digest(self.model_id)
            if not self.running:
                return
            self.capture_thread = threading.Thread(target=self.capture, daemon=True)
            self.capture_thread.start()
            while self.running and not self.isInterruptionRequested():
                if self._fit_event.is_set():
                    with self.inference_lock:
                        self._fit_event.clear()
                        with self.lock:
                            self.phase = 'fitting'
                            clips=list(self.calibration_frames)
                        if self.defer_calibration:
                            self.calibration_progress.emit(0, '正在保存校准素材')
                            result = self._persist_calibration(True)
                            if result['status'] != 'pending':
                                raise ValueError('校准素材不足，每点至少需要5帧原始图片，请重新采集')
                            with self.lock:
                                self.phase = 'preview'
                            self.calibration_progress.emit(100, '校准素材已保存')
                            self.calibrated.emit(result)
                            continue
                        if self.calibration_folder is not None:
                            self.calibration_progress.emit(0, '正在保存校准素材')
                            self._persist_calibration(True)
                        self.status.emit('正在完成个人校准…')
                        samples=[]
                        import numpy as np
                        for i,(encoded,mono,st) in enumerate(clips):
                            if not self.running:break
                            frame=cv2.imdecode(np.frombuffer(encoded,np.uint8),cv2.IMREAD_COLOR)
                            if frame is not None:
                                try:
                                    sample=self.tracker.infer(frame)
                                    samples.append(sample | {'target_id':st['target_id'],'target':st['target'],
                                                            'captured_offset_s':mono/1e9-st['target_since']})
                                except ValueError:pass
                            if i%5==0 or i+1==len(clips):
                                self.calibration_progress.emit(round(65*(i+1)/max(1,len(clips))),
                                    f'处理校准画面 {i+1}/{len(clips)}')
                        if not self.running:break
                        self.cal_samples=samples
                        self.calibration_frames=[]
                        result = self.tracker.calibrate(
                            samples, self.cfg.get('use_maml', True),
                            on_progress=lambda p,m:self.calibration_progress.emit(65+round(p*.35),m.replace(chr(183),' ')),
                        )
                        result['capture_window_s']=[self.calibration_window_start_s,self.calibration_window_end_s]
                        result['target_dwell_s']=self.calibration_dwell_s
                        result['exclude_start_ms']=self.calibration_window_start_s*1000.
                        result['exclude_end_ms']=(self.calibration_dwell_s-self.calibration_window_end_s)*1000.
                        if self.post_calibration_folder is not None:
                            self._commit_post_calibration({'status': 'complete', **result})
                        with self.lock:
                            self.phase = 'preview'
                        self.calibrated.emit(result)
                self.msleep(20)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            self.running = False
            self._fit_event.clear()
            try:
                self.end_record('worker_stopped')
            except Exception as error:
                self.failed.emit('保存采集数据时出现错误：' + str(error))
            if self.capture_thread is not None:
                self.capture_thread.join(timeout=5)
                if self.capture_thread.is_alive():
                    self.status.emit('正在等待摄像头驱动释放，请稍候…')
                    # Keep the QThread/QObject alive while the capture thread
                    # still owns it; the UI can remain responsive while waiting.
                    while self.capture_thread.is_alive():
                        self.capture_thread.join(timeout=.2)
            if self.post_calibration_folder is not None:
                try:
                    if not self.calibration_saved:
                        self._persist_calibration(False, self.calibration_stop_reason)
                    if self.post_calibration_folder is not None:
                        self._commit_post_calibration({'status': 'incomplete', 'target_count': self.calibration_points},
                                                      self.calibration_stop_reason)
                except Exception as error:
                    self.failed.emit('保存末次校准失败：' + str(error))
            elif self.calibration_folder is not None and not self.calibration_saved:
                try:
                    self._persist_calibration(False, self.calibration_stop_reason)
                except Exception as error:
                    self.failed.emit('保存原始校准素材失败：' + str(error))
            elif self.defer_calibration and self.calibration_saved and not self._formal_record_started:
                try:
                    self.calibration_metadata.update(acquisition_state='calibration_only',
                        actual_duration_s=0.,stop_reason=self.calibration_stop_reason)
                    _save_json(self.calibration_folder/'session.json',self.calibration_metadata)
                except Exception as error:
                    self.failed.emit('保存中止状态失败：' + str(error))
            if (self.defer_calibration and self.calibration_folder is not None
                    and self.calibration_metadata.get('storage_layout') == 'compact_v1'):
                try:
                    from record_layout import compact_pending
                    compact_pending(self.calibration_folder)
                except Exception as error:
                    self.failed.emit('整理校准素材时出现错误：' + str(error))
            self.calibration_frames=[]
            if self.tracker is not None:
                try:
                    self.tracker.close()
                except Exception as error:
                    self.failed.emit('释放校准模型时出现错误：' + str(error))
