"""Installed processor checks use encoded fixtures and never open a physical camera."""
import csv
import json
import os
from pathlib import Path
import tempfile
import time
import traceback

from app_paths import DATA_ROOT, RESOURCE_ROOT


def make_fixture(folder, *, mode='experiment', model_hash='processor-fixture', duration=1., fps=20):
    import cv2
    import numpy as np
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cfg = dict(physical_width=1200, physical_height=800, logical_width=800, logical_height=534,
               screen_width_mm=260.3, screen_height_mm=173.6, dpr=1.5,
               camera_x_mm=0, camera_y_mm=10, fov_degrees=60, camera_index=0)
    metadata = dict(mode=mode, protocol_version=3, recording_protocol_version=3,
                    model_id='affnet_mixed', config=cfg, origin={'x': .5, 'y': .5},
                    actual_duration_s=duration, planned_duration_s=duration, assume_fixation=False,
                    participant=dict(name='处理验证人员', participant_id='001', age=23, sex='女'),
                    scheme=dict(name='实验验证', mode=mode),
                    calibration=dict(target_count=0, model_sha256=model_hash),
                    processing=dict(mode='immediate', retain_raw_rgb=False),
                    created_local='2026-11-10T10:00:00+08:00')
    (folder / 'session.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf8')
    writer = cv2.VideoWriter(str(folder / 'camera.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), fps, (96, 64))
    if not writer.isOpened():
        raise RuntimeError('无法创建诊断视频')
    try:
        for index in range(round(duration * fps)):
            writer.write(np.full((64, 96, 3), index % 255, np.uint8))
    finally:
        writer.release()
    with (folder / 'capture.csv').open('w', encoding='utf-8-sig', newline='') as file:
        out = csv.writer(file)
        out.writerow(['capture_id', 'timestamp_unix_ns', 'monotonic_ns', 'elapsed_s', 'video_index',
                      'target_id', 'origin_x_physical', 'origin_y_physical', 'label_valid'])
        for index in range(round(duration * fps)):
            elapsed_ns = round(index * 1_000_000_000 / fps)
            out.writerow([index, 1700000000000000000 + elapsed_ns, 1000000000 + elapsed_ns,
                          index / fps, index, 0, 600, 400, 0])
    return metadata


class FixtureTracker:
    def __init__(self, model_id, config):
        self.model_hash = 'processor-fixture'
        self.affine = None
        self.head = None
        self.cfg = config

    def infer(self, frame):
        import numpy as np
        return dict(x=610., y=420., distance_mm=550., reprojection_px=1., origin=np.array([0., 0., 550.]))

    def close(self):
        pass


class PairedFixtureTracker(FixtureTracker):
    def infer(self, frame):
        sample = super().infer(frame)
        if self.affine is not None:
            sample['x'], sample['y'] = self.affine[-1]
        return sample

    def calibrate(self, samples, use_maml, *, on_progress):
        import numpy as np
        target = samples[0]['target']
        self.affine = np.asarray([[0., 0.], [0., 0.], target])
        return dict(target_count=9, affine=self.affine.tolist(), model_sha256=self.model_hash)


def _save_calibration_fixture(folder, meta, target_x):
    import cv2
    import numpy as np
    from deferred_calibration import save_material

    ok, image = cv2.imencode('.jpg', np.zeros((64, 96, 3), np.uint8))
    assert ok
    targets = []
    clips = []
    for index in range(9):
        target = [target_x + index, 420.]
        started = float(index + 1)
        snapshot = dict(target_id=index, target=target, target_since=started)
        targets.append(dict(target_id=index, target=target, started_monotonic_s=started))
        for offset in range(50, 300, 50):
            clips.append((image.tobytes(), int(started * 1e9) + offset * 1_000_000, snapshot))
    return save_material(folder, meta, clips, targets, model_hash='processor-fixture')


def check_paired_records(root, window, patch, offline):
    from deferred_calibration import digest_file
    from processor_service import export_mapping, process_batch, scan_sessions
    from record_layout import PAIRED_EXPERIMENT_LAYOUT, _atomic_json, compact_pending

    pair = root / 'pair'
    originals = {}
    for index, (target_x, fps) in enumerate(((610., 25), (710., 20)), 1):
        folder = pair / f'segment-{index}'
        meta = make_fixture(folder, fps=fps)
        meta.update(experiment_layout=PAIRED_EXPERIMENT_LAYOUT, experiment_session_id='fixture-pair',
                    segment_index=index, segment_count=2, rest_duration_s=900,
                    calibration_id=f'pre-{index}', created_local=f'2026-11-12T1{index}:00:00+08:00')
        meta['config'].update(calibration_points=9, calibration_dwell_s=.4, calibration_exclude_start_ms=20)
        meta['processing']['mode'] = 'deferred'
        _save_calibration_fixture(folder, meta, target_x)
        post = folder / 'post_calibration'
        post_meta = dict(config=meta['config'], participant=meta['participant'], model_id=meta['model_id'],
                         calibration_id=f'post-{index}', mode='calibration', calibration_role='post')
        post_summary = _save_calibration_fixture(post, post_meta, target_x + 150.)
        meta.update(acquisition_state='recorded', post_calibration=post_summary | {'material_folder': post.name})
        _atomic_json(folder / 'session.json', meta)
        compact_pending(folder)
        for material in (folder, post):
            archive = material / 'calibration_raw/frames.zip'
            originals[archive] = digest_file(archive)
    rows = scan_sessions(pair)
    assert [row['segment_index'] for row in rows] == [1, 2]
    with patch.object(offline, 'Tracker', PairedFixtureTracker):
        processed = process_batch([row['path'] for row in rows], retain_raw_rgb=False, device='cpu')
    assert len(processed) == 2 and all(row['complete'] for row in processed)
    x_cm = []
    for row in processed:
        with Path(row['txt_path']).open(encoding='utf8', newline='') as file:
            x_cm.append(float(next(csv.DictReader(file, delimiter='\t'))['x_cm']))
    assert x_cm[1] > x_cm[0]
    saved = [json.loads((Path(row['path']) / 'session.json').read_text(encoding='utf8')) for row in rows]
    assert [meta['calibration']['affine'][-1][0] for meta in saved] == [610., 710.]
    assert all(meta['post_calibration']['status'] == 'pending' for meta in saved)
    assert all(digest_file(path) == digest for path, digest in originals.items())
    assert all(not (Path(row['path']) / 'camera.mp4').exists() for row in rows)
    window.show_records(scan_sessions(pair))
    for table, column in ((window.record_table, 8), (window.mapping_table, 7)):
        assert [table.item(index, column).text() for index in range(2)] == ['第 1 / 2 段', '第 2 / 2 段']
        assert all(table.item(index, column).toolTip() == 'fixture-pair' for index in range(2))
    path = export_mapping(window.mapping_records, root / 'pair.csv')
    with path.open(encoding='utf-8-sig', newline='') as file:
        mapping = list(csv.DictReader(file))
    assert [(row['实验编号'], row['段号']) for row in mapping] == [('fixture-pair', '1'), ('fixture-pair', '2')]


def run(output):
    output = Path(output)
    result = dict(ok=False, edition='processor', data_root=str(DATA_ROOT), resources=str(RESOURCE_ROOT),
                  installer_mode=(RESOURCE_ROOT / 'installer_mode.json').is_file(),
                  portable_mode=(RESOURCE_ROOT / 'portable_mode.json').is_file(), checks=[])
    app = None
    window = None
    try:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from unittest.mock import patch
        import torch
        import offline
        from PyQt6.QtWidgets import QApplication, QLabel
        from PyQt6.QtGui import QFontMetrics
        from processor_ui import ProcessorWindow
        from processor_service import scan_sessions, process_record, export_mapping
        from model_runtime import get_tracker
        from processing_hardware import plan_execution, probe_hardware
        hardware = probe_hardware()
        result['hardware'] = hardware
        assert plan_execution('auto')['device'].startswith('cuda:') == hardware['cuda_available']
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = ProcessorWindow()
        window.resize(1280, 860)
        window.show()
        app.processEvents()
        assert window.retain_raw.isChecked()
        assert not window.start_button.isEnabled()
        assert QFontMetrics(app.font()).inFontUcs4(ord('眼'))
        assert window.sidebar.isVisible() and window.stats_panel.isVisible()
        assert window.settings_button.isEnabled() and window.about_button.isEnabled()
        assert window.pending_value.text() == '0' and window.completed_value.text() == '0'
        window.resize(800, 560)
        app.processEvents()
        for control in (window.choose_button, window.start_button, window.view_button, window.tabs,
                        window.device_choice, window.cpu_jobs, window.cpu_threads):
            assert control.isVisibleTo(window) and control.visibleRegion().contains(control.rect())
        if window.hardware_status.isVisibleTo(window):
            device_bottom = window.device_choice.mapToGlobal(window.device_choice.rect().bottomLeft()).y()
            status_top = window.hardware_status.mapToGlobal(window.hardware_status.rect().topLeft()).y()
            assert device_bottom < status_top, (device_bottom, status_top)
        window.resize(1280, 860)
        app.processEvents()
        result['checks'].append('Lavender three-column dashboard and compact layout')
        result['checks'].append('Experiment-only processor window, Chinese font, RGB retained by default')
        DATA_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='processor_check_', dir=DATA_ROOT) as temporary:
            root = Path(temporary)
            experiment = root / 'experiment'
            test = root / 'test'
            metadata = make_fixture(experiment, fps=25)
            import cv2
            import numpy as np
            from experiment_visuals import _write_png
            stimulus = np.full((64, 64, 3), (60, 125, 210), dtype=np.uint8)
            _write_png(experiment / 'stimulus.png', stimulus)
            metadata.update(background_mode='image', image_path='stimulus.png', fixation_enabled=True)
            metadata['scheme'].update(fixation_style='cross', fixation_color='#000000', fixation_diameter_px=34)
            (experiment / 'session.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf8')
            from record_layout import compact_pending
            compact_pending(experiment)
            assert {p.name for p in experiment.iterdir()} == {'camera.mp4', 'session.json'}
            make_fixture(test, mode='test')
            test_before = {path.name: path.read_bytes() for path in test.iterdir()}
            rows = scan_sessions(root)
            assert len(rows) == 1 and rows[0]['path'] == str(experiment)
            progress = []
            requested_threads = min(3, hardware['cpu_count'])
            with patch.object(offline, 'Tracker', FixtureTracker):
                completed = process_record(experiment, cpu_threads=requested_threads,
                                           on_progress=lambda percent, message: progress.append(percent))
            assert completed['status'] == '已完成'
            assert completed['export_rows'] == 20
            assert (experiment / 'camera.mp4').is_file()
            with Path(completed['txt_path']).open(encoding='utf8', newline='') as file:
                values = list(csv.reader(file, delimiter='\t'))
            assert values[0] == ['time_s', 'timestamp_utc', 'x_cm', 'y_cm', 'distance_cm']
            assert [float(row[0]) for row in values[1:]] == [index / 20 for index in range(20)]
            captured_utc = {f'2023-11-14T22:13:20.{index * 40_000_000:09d}Z' for index in range(25)}
            selected_utc = [row[1] for row in values[1:]]
            assert len(set(selected_utc)) == 20 and set(selected_utc) <= captured_utc
            assert selected_utc == sorted(selected_utc)
            saved = json.loads((experiment / 'session.json').read_text(encoding='utf8'))
            assert saved['inference']['inference_cpu_threads'] == requested_threads
            assert saved['inference']['inference_device'] == 'cpu'
            assert saved['participant']['participant_id'] == '001'
            assert saved['files']['data'] == Path(completed['txt_path']).name
            assert saved['config'] == metadata['config']
            assert {p.name for p in experiment.iterdir()} == {
                'session.json', Path(completed['txt_path']).name, 'gaze_replay.mp4', 'distribution.png', 'camera.mp4'}
            assert progress == sorted(progress) and progress[-1] == 100
            assert {path.name: path.read_bytes() for path in test.iterdir()} == test_before
            result['checks'].append('Compact raw JSON and RGB to 20 Hz TXT with original frame UTC, person/device JSON, plot and replay')
            reader = cv2.VideoCapture(str(experiment / 'gaze_replay.mp4'))
            try:
                ok, frame = reader.read()
            finally:
                reader.release()
            assert ok and np.allclose(frame[100, 100], (21, 21, 21), atol=10)
            assert np.allclose(frame[100, 600], (60, 125, 210), atol=10)
            assert np.max(frame[400, 600]) < 55
            result['checks'].append('Replay restores the saved stimulus image and fixation target')
            assert np.allclose(frame[420, 610], (114, 146, 228), atol=14), frame[420, 610]
            result['checks'].append('Replay composites gaze points at half opacity')
            with patch.object(offline, 'infer_session', side_effect=AssertionError('Completed records must not rerun')):
                process_record(experiment, retain_raw_rgb=False)
            assert (experiment / 'camera.mp4').is_file()
            result['checks'].append('Existing complete records skipped without retrospective RGB deletion')
            minimal = root / 'minimal'
            second_metadata = make_fixture(minimal, fps=16)
            second_metadata['participant'].update(name='第二位验证人员', participant_id='0007')
            second_metadata['created_local'] = '2026-11-10T11:02:03+08:00'
            (minimal / 'session.json').write_text(json.dumps(second_metadata, ensure_ascii=False), encoding='utf8')
            compact_pending(minimal)
            with patch.object(offline, 'Tracker', FixtureTracker):
                clean = process_record(minimal, retain_raw_rgb=False)
            with Path(clean['txt_path']).open(encoding='utf8', newline='') as file:
                sparse_utc = [row['timestamp_utc'] for row in csv.DictReader(file, delimiter='\t')]
            assert sparse_utc == [f'2023-11-14T22:13:20.{index * 62_500_000:09d}Z' for index in range(16)]
            assert {p.name for p in minimal.iterdir()} == {
                'session.json', Path(clean['txt_path']).name, 'gaze_replay.mp4', 'distribution.png'}
            result['checks'].append('Below 20 captured frames exports only those frames with real UTC')
            result['checks'].append('Successful no-RGB experiment contains exactly four result files')
            mapping = export_mapping([completed], root / 'mapping.csv')
            with mapping.open(encoding='utf-8-sig', newline='') as file:
                mapped = list(csv.DictReader(file))
            assert mapped[0]['编号'] == '001' and mapped[0]['数据TXT'] == completed['txt_path']
            window.show_records(scan_sessions(root))
            window.tabs.setCurrentIndex(1)
            app.processEvents()
            assert window.pending_value.text() == '0' and window.completed_value.text() == '2'
            output.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(output.with_suffix('.png')))
            result['checks'].append('Participant mapping CSV and output-file table')
            window.mapping_table.selectAll()
            window.view_button.click()
            comparison = window.visual_dialog
            try:
                deadline = time.monotonic() + 30
                while not all(panel.paths for panel in comparison.panels) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(.01)
                assert len(comparison.panels) == 2
                assert {Path(folder) for folder in comparison.folders} == {experiment, minimal}
                assert all(panel.paths and not panel.distribution.original.isNull() for panel in comparison.panels)
                headers = {experiment: ('处理验证人员　编号 001', '2026-11-10 10:00:00'),
                           minimal: ('第二位验证人员　编号 0007', '2026-11-10 11:02:03')}
                for folder, panel in zip(comparison.folders, comparison.panels):
                    for page in (panel.plot, panel.replay):
                        assert set(headers[folder]) <= {label.text() for label in page.findChildren(QLabel)}
                comparison.grab().save(str(output.with_name(output.stem + '_comparison.png')))
            finally:
                comparison.close()
                app.processEvents()
            result['checks'].append('Multi-record comparison shows two processed plots and replays')
            result['checks'].append('Comparison labels use saved participant name, identifier and recording time')
            check_paired_records(root, window, patch, offline)
            result['checks'].append('Paired segments use independent calibration and preserve all four JPEG archives')
            tracker = get_tracker('affnet_mixed', metadata['config'])
            try:
                with torch.inference_mode():
                    prediction = tracker.net(torch.zeros(1, 3, 112, 112), torch.zeros(1, 3, 112, 112),
                                             torch.zeros(1, 3, 224, 224), torch.zeros(1, 12))
                assert tuple(prediction.shape) == (1, 2) and torch.isfinite(prediction).all()
                assert len(tracker.model_hash) == len(tracker.runtime_hash) == 64
            finally:
                tracker.close()
            result['checks'].append('Real AFF-Net weights and runtime hash verification, CPU forward')
            if torch.cuda.is_available():
                gpu_tracker = get_tracker('affnet_mixed', dict(metadata['config'], inference_device='cuda:0'))
                try:
                    device = gpu_tracker.device
                    with torch.inference_mode():
                        gpu_prediction = gpu_tracker.net(torch.zeros(4, 3, 112, 112, device=device),
                                                         torch.zeros(4, 3, 112, 112, device=device),
                                                         torch.zeros(4, 3, 224, 224, device=device),
                                                         torch.zeros(4, 12, device=device))
                    assert gpu_prediction.is_cuda and tuple(gpu_prediction.shape) == (4, 2)
                    assert torch.isfinite(gpu_prediction).all().item()
                finally:
                    gpu_tracker.close()
                result['checks'].append('Real CUDA AFF-Net batched forward')
        result['ok'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if app is not None and window is not None:
            from PyQt6.QtCore import QCoreApplication, QEvent
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if result['ok'] else 1
