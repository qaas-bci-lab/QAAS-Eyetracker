"""Verify the installed raw-only recorder with synthetic camera frames."""
import json
import os
from pathlib import Path
import sys
import time
import traceback


def run(output):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from app_paths import DATA_ROOT, RESOURCE_ROOT, RAW_CAPTURE, configure_cache_environment
    configure_cache_environment()
    result = dict(ok=False, edition='capture_raw', data_root=str(DATA_ROOT),
                  installer_mode=(RESOURCE_ROOT / 'installer_mode.json').is_file(),
                  portable_mode=(RESOURCE_ROOT / 'portable_mode.json').is_file(), checks=[])
    app = worker = window = schemes = settings = None
    try:
        import cv2
        import numpy as np
        from unittest.mock import patch
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QPushButton
        import capture
        from deferred_calibration import read_material
        from main_window import Main
        from record_layout import PAIRED_EXPERIMENT_LAYOUT, read_capture_rows
        from scheme_store import default_scheme
        from ui_components import SchemeDialog, SettingsDialog

        assert RAW_CAPTURE
        if result['installer_mode']:
            assert not any((RESOURCE_ROOT / name).exists() for name in ('models', 'torch', 'mediapipe'))
        app = QApplication([])
        window = Main()
        window.show()
        app.processEvents()
        from lsl_check import check_lsl_roundtrip
        check_lsl_roundtrip()
        result['checks'].append('Real LSL marker outlet and inlet round trip')
        visible = {b.text() for b in window.findChildren(QPushButton) if b.isVisibleTo(window)}
        assert {'开始实验', '历史记录', '设置'} <= visible and not {'测试', '数据分析'} & visible
        schemes = SchemeDialog(window.settings)
        settings = SettingsDialog(window.settings)
        assert schemes.mode_tabs.count() == 1 and schemes.model.isHidden()
        assert [b.text() for b in settings.settings_nav_buttons] == ['保存位置', '相机与屏幕']
        assert window.selected_scheme()['experiment_protocol'] == 'two_part'
        result['checks'].append('Raw recorder UI has experiment and storage controls only')
        from experiment_flow_check import check_two_part_experiment
        check_two_part_experiment(app, window.build_config(default_scheme()))
        result['checks'].append('Exact 13 LSL triggers for the paired capture timeline')

        class Camera:
            def __init__(self, *args): self.closed = False
            def isOpened(self): return not self.closed
            def set(self, *args): return True
            def getBackendName(self): return 'synthetic'
            def read(self):
                time.sleep(.009)
                return True, np.full((64, 96, 3), 90, np.uint8)
            def release(self): self.closed = True

        errors, calibrated = [], []

        def wait_for(predicate, seconds=8):
            deadline = time.perf_counter() + seconds
            while not predicate():
                if errors:
                    raise AssertionError(errors)
                if time.perf_counter() >= deadline:
                    raise TimeoutError('Synthetic raw capture timed out')
                time.sleep(.007)

        scheme = default_scheme()
        cfg = window.build_config(scheme)
        cfg.update(calibration_dwell_s=.45, calibration_exclude_start_ms=10,
                   calibration_exclude_end_ms=10, max_fps=True)
        root = DATA_ROOT / 'raw_check_recordings'
        root.mkdir(exist_ok=True)
        with patch.object(capture.cv2, 'VideoCapture', Camera), patch.object(
                capture, 'Tracker', side_effect=AssertionError('Raw edition initialized a model')):
            worker = capture.Worker('affnet_mixed', cfg)
            assert worker.defer_calibration
            worker.failed.connect(errors.append, Qt.ConnectionType.DirectConnection)
            worker.calibrated.connect(calibrated.append, Qt.ConnectionType.DirectConnection)
            worker.start()
            wait_for(lambda: worker.is_camera_ready)

            def collect_points():
                worker.begin_calibration()
                for index in range(9):
                    worker.set_target(index, [100. + index % 3 * 100, 100. + index // 3 * 100])
                    wait_for(lambda: time.perf_counter() - worker.target_since >= .43)
                old_count = len(calibrated)
                worker.request_calibration_fit()
                wait_for(lambda: len(calibrated) > old_count)
                assert calibrated[-1]['status'] == 'pending' and worker.tracker is None
                return calibrated[-1]

            folders = []
            for index in (1, 2):
                folder = root / f'segment-{index}'
                folder.mkdir(exist_ok=True)
                metadata = dict(
                    mode='experiment', config=cfg, model_id='affnet_mixed', scheme=scheme,
                    model_sha256=worker.model_hash, calibration_id=f'raw-check-pre-{index}',
                    experiment_layout=PAIRED_EXPERIMENT_LAYOUT, experiment_session_id='raw-check-pair',
                    segment_count=2, segment_index=index, rest_duration_s=900,
                    participant={'name': '采集自检', 'participant_id': '01'},
                    planned_duration_s=.7, storage_layout='compact_v1',
                    recording_protocol_version=3, points=[dict(x=.5, y=.5, seconds=.7)],
                    processing={'mode': 'deferred', 'retain_raw_rgb': True})
                worker.prepare_calibration_storage(folder, metadata)
                metadata['calibration'] = collect_points()
                assert (folder / 'calibration_raw/frames.zip').is_file()
                worker.begin_record(folder, metadata)
                wait_for(lambda: worker.record['video_index'] >= 5)
                assert worker.end_record('completed') == folder
                worker.prepare_post_calibration_storage(folder)
                collect_points()
                folders.append(folder)
            worker.stop()
            assert worker.wait(6000) and not errors, errors

        for index, folder in enumerate(folders, 1):
            meta = json.loads((folder / 'session.json').read_text(encoding='utf8'))
            assert meta['segment_index'] == index and meta['experiment_session_id'] == 'raw-check-pair'
            manifest, clips = read_material(folder, meta)
            assert manifest['target_count'] == 9 and len(clips) >= 45
            post = folder / 'post_calibration'
            post_meta = json.loads((post / 'session.json').read_text(encoding='utf8'))
            manifest, clips = read_material(post, post_meta)
            assert manifest['target_count'] == 9 and len(clips) >= 45
            assert len(read_capture_rows(folder)) >= 5
            video = cv2.VideoCapture(str(folder / 'camera.mp4'))
            try:
                assert video.read()[0]
            finally:
                video.release()
        assert not {'torch', 'mediapipe', 'affnet_tracker'} & sys.modules.keys()
        result['checks'].append('Two decodable RGB segments and four nine-point JPEG archives without model inference')
        result['ok'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(6000)
        for widget in (settings, schemes, window):
            if widget is not None:
                widget.close()
                widget.deleteLater()
        if app is not None:
            from PyQt6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if result['ok'] else 1
