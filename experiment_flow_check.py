"""Synthetic validation of the paired capture and LSL event sequence."""
import time


def check_two_part_experiment(app, cfg):
    """Run the full acquisition timeline without waiting or opening a camera."""
    from types import SimpleNamespace
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from main_window import Experiment
    from scheme_store import default_scheme

    from app_paths import DATA_ROOT

    root = DATA_ROOT / 'packaged_two_part'
    root.mkdir(parents=True, exist_ok=True)
    events, starts, finishes, targets, markers = [], [], [], [], []

    def begin_record(folder, metadata):
        assert not worker.record
        worker.record = (folder, metadata)
        starts.append((folder, metadata.copy()))
        events.append('record')
        return time.perf_counter()

    def end_record(reason):
        assert reason == 'completed'
        folder, _ = worker.record
        worker.record = None
        worker.last_record_folder = folder
        events.append('end')
        return folder

    def prepare_post(folder):
        assert not worker.record and folder == worker.last_record_folder
        events.append('post')

    def prepare_pre(folder, metadata):
        assert not worker.record and metadata['segment_index'] == len(starts) + 1
        events.append('pre')

    def bad(message):
        raise AssertionError(message)

    def emit(code, *, session_id, segment_index):
        markers.append((code, session_id, segment_index))
        events.append(code)
        return True

    worker = SimpleNamespace(
        defer_calibration=True, tracker=None, model_hash=None, runtime_hash=None,
        record=None, last_record_folder=None, begin_record=begin_record, end_record=end_record,
        prepare_calibration_storage=prepare_pre, prepare_post_calibration_storage=prepare_post,
        begin_calibration=lambda: events.append('calibrate'),
        set_target=lambda index, point: targets.append(index) if surface.calibrating else None,
        request_calibration_fit=lambda: events.append('fit'), stop=lambda: events.append('stop'),
    )
    main = SimpleNamespace(
        cfg=cfg, calibration=None, worker=worker, status=SimpleNamespace(setText=lambda _: None),
        _active_participant=dict(name='双段自检'),
        _active_processing=dict(mode='deferred', output_dir=str(root), retain_raw_rgb=True),
        lsl=SimpleNamespace(emit=emit),
        show_error=bad, show=lambda: None, raise_=lambda: None, activateWindow=lambda: None,
        experiment_finished=lambda folder, reason, **kwargs: finishes.append((folder, reason, kwargs)),
    )
    surface = Experiment(main, default_scheme())
    main.experiment = surface

    def finish_calibration():
        if surface.counting_down:
            surface.countdown_started -= surface.calibration_countdown_s + .1
            surface.tick()
        assert surface.calibrating and not worker.record
        for _ in range(9):
            surface.cal_start -= surface.calibration_dwell_s + .1
            surface.tick()
        assert surface.fitting
        surface.ready_now(dict(target_count=9))

    def key_s():
        QTest.keyClick(surface, Qt.Key.Key_S)

    def finish_record():
        assert surface.recording and surface.duration == 480
        surface.started -= surface.duration + .1
        surface.tick()
        assert not surface.recording and not worker.record

    try:
        surface.open_flow()
        surface.timer.stop()
        app.processEvents()
        assert surface.counting_down and not starts
        key_s()
        assert not markers  # S is not actionable during calibration countdown.
        finish_calibration()
        assert surface.ready and surface.message == '按 S 开始'
        key_s()
        finish_record()
        finish_calibration()
        assert 898 < surface.rest_deadline - time.perf_counter() <= 900
        key_s()
        assert len(starts) == 1 and not surface.counting_down and len(markers) == 6
        surface.rest_deadline = time.perf_counter() - 1
        surface.tick()
        assert surface.waiting_for_next_calibration and not surface.counting_down
        key_s()
        assert surface.counting_down and len(starts) == 1
        finish_calibration()
        assert surface.ready and surface.message == '按 S 开始'
        key_s()
        finish_record()
        finish_calibration()
        assert len(finishes) == 1 and finishes[0][1] == 'completed'
        assert len(starts) == 2 and starts[0][0] != starts[1][0]
        assert finishes[0][0] == starts[1][0]
        assert finishes[0][2]['segments'] == tuple(path for path, _ in starts)
        one, two = (record[1] for record in starts)
        assert [one['segment_index'], two['segment_index']] == [1, 2]
        assert one['segment_count'] == two['segment_count'] == 2
        assert one['experiment_session_id'] == two['experiment_session_id'] != ''
        assert all(row['processing']['retain_raw_rgb'] and not row['delete_raw_after_analysis']
                   for row in (one, two))
        assert targets == list(range(9)) * 4, targets
        assert events == [
            'pre', 'calibrate', 'CALIBRATION_1_START', 'CALIBRATION_1_END', 'fit',
            'record', 'S_1', 'FIXATION_1_END', 'end', 'post',
            'calibrate', 'CALIBRATION_2_START', 'CALIBRATION_2_END', 'fit', 'S_2',
            'pre', 'calibrate', 'CALIBRATION_3_START', 'CALIBRATION_3_END', 'fit',
            'record', 'S_3', 'FIXATION_2_END', 'end', 'post',
            'calibrate', 'CALIBRATION_4_START', 'CALIBRATION_4_END', 'fit', 'stop',
        ], events
        assert len(markers) == 13
        assert {entry[1] for entry in markers} == {one['experiment_session_id']}
        assert [entry[2] for entry in markers] == [1] * 6 + [2] * 7
    finally:
        if surface._awake_guard is not None:
            restore_execution_state, previous = surface._awake_guard
            restore_execution_state(previous)
        surface._finishing = True
        surface.timer.stop()
        surface.close()

