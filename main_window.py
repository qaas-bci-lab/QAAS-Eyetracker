"""Independent experiment/test workflows and full-screen acquisition."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import math
import random
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

import numpy as np
from PyQt6.QtCore import Qt, QTimer, QPointF, QRectF, QUrl
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar, QScrollArea,
    QVBoxLayout, QWidget,
)

from capture import Worker, calibration_timing
from scheme_store import load_settings, normalize_settings, resolve_image, save_settings, validate_scheme, processing_for_mode
from ui_components import (
    HomeSurface, ImageBoard, SettingsDialog, SchemeDialog, TestStartDialog,
    StartEntry, button, configure_system_font, label,
)
from ui_symbols import brand_pixmap, icon
from PyQt6.QtGui import QIcon,QColor,QFont,QPainter,QDesktopServices
from target_visuals import DIRECTIONS, display_dimensions, draw_direction_arrow, draw_direction_keypad
from app_paths import DATA_ROOT, RAW_CAPTURE, ensure_user_data
from window_chrome import install_chrome
from participant_info import ParticipantInfo
from workspace_ui import ParticipantWorkspace

ROOT = DATA_ROOT


def write_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name + '.partial')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    temp.replace(path)


class Main(ParticipantWorkspace, QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        ensure_user_data()
        configure_system_font()
        self.setWindowTitle('QAAS-Eyetracker 纯采集' if RAW_CAPTURE else 'QAAS-Eyetracker 模型采集')
        self.setWindowIcon(QIcon(brand_pixmap(64)))
        self.resize(960, 700)
        self.setMinimumSize(720, 580)
        self.settings = normalize_settings(settings) if settings is not None else load_settings()
        self.worker = None
        self.experiment = None
        self.analysis_window = None
        self.calibration = None
        self.cfg = None
        self.last_folder = None
        previous = self.settings.get('last_session')
        if previous and (Path(previous) / 'session.json').is_file():
            self.last_folder = Path(previous)
        self._preparing = False
        self._prepare_dialog = None
        self._handled_worker_failure = False
        self._active_scheme = None
        self._active_processing = None
        self._active_participant = {}
        self._run_mode = 'experiment'
        self._pending_test_analysis = None
        self._queued_experiment_analysis = []
        self._analysis_starting = False
        self._closing = False

        self.build_workspace(ROOT)
        install_chrome(self)
        from lsl_markers import MarkerOutlet
        self.lsl = MarkerOutlet()
        if self.lsl.startup_error:
            self.status.setText('LSL 触发未启用')
            self.status.setToolTip(self.lsl.startup_error)

    @property
    def data(self):
        return self.settings

    def selected_scheme(self):
        selected = self.scheme_combo.currentData()
        bank = 'test_schemes' if self.workspace_mode == 'test' else 'schemes'
        return next(s for s in self.settings[bank] if s['id'] == selected)

    def refresh_schemes(self):
        self.scheme_combo.blockSignals(True)
        self.scheme_combo.clear()
        bank = 'test_schemes' if self.workspace_mode == 'test' else 'schemes'
        for scheme in self.settings[bank]:
            self.scheme_combo.addItem(scheme['name'], scheme['id'])
        index = self.scheme_combo.findData(self.settings.get('selected_test_scheme' if self.workspace_mode == 'test' else 'selected_scheme'))
        self.scheme_combo.setCurrentIndex(max(0, index))
        self.scheme_combo.blockSignals(False)
        self.refresh_summary()

    def refresh_summary(self):
        scheme = self.selected_scheme()
        points = scheme['calibration_points']
        calibration = f'{points} 点校准' if points else '无需校准'
        summary = f"{scheme['duration_minutes']:g} 分钟    {calibration}"
        if scheme.get('experiment_protocol') == 'two_part':
            summary = f"两段各 {scheme['duration_minutes']:g} 分钟    休息 {scheme['rest_minutes']:g} 分钟"
        self.scheme_combo.setToolTip(summary)
        self.scheme_summary.setText(summary)
        processing = processing_for_mode(self.settings, self.workspace_mode) or {}
        self.processing_badge.setText('保存素材' if RAW_CAPTURE else
                                      '后台处理' if processing.get('mode') == 'deferred' else '实时处理')

    def scheme_changed(self):
        if self.scheme_combo.currentIndex() < 0:
            return
        self.settings['selected_test_scheme' if self.workspace_mode == 'test' else 'selected_scheme'] = self.scheme_combo.currentData()
        try:
            self.settings = save_settings(self.settings)
        except Exception as error:
            self.show_error(str(error))
        self.refresh_summary()

    def _analysis_busy(self):
        if self._analysis_starting:
            return True
        window = self.analysis_window
        if window is None:
            return False
        for name in ['busy', 'is_busy']:
            flag = getattr(window, name, False)
            if (flag() if callable(flag) else flag):
                return True
        return False

    def open_settings(self):
        if self._preparing or self.experiment is not None:
            return
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = load_settings()
            self.refresh_schemes()
            self.refresh_history()
            self.status.setText('设置已保存')

    def open_scheme_settings(self):
        if self._preparing or self.experiment is not None:
            return
        dialog=SchemeDialog(self.settings,self,mode=self.workspace_mode)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.settings=load_settings();self.refresh_schemes();self.status.setText('方案已保存。')

    def start_test(self):
        if RAW_CAPTURE:
            return
        self.switch_workspace_mode('test')
        self.start_selected()

    def build_config(self, scheme):
        screen = QApplication.primaryScreen()
        area = screen.geometry()
        dpr = float(screen.devicePixelRatio())
        geometry = self.settings['geometry']
        config = dict(
            physical_width=round(area.width() * dpr), physical_height=round(area.height() * dpr),
            logical_width=area.width(), logical_height=area.height(), dpr=dpr,
            screen_name=screen.name(), screen_width_mm=geometry['screen_width_cm'] * 10,
            screen_height_mm=geometry['screen_height_cm'] * 10,
            camera_x_mm=0 if geometry['camera_top_center'] else geometry['camera_x_cm'] * 10,
            camera_y_mm=geometry['camera_gap_cm'] * 10, fov_degrees=geometry['fov_degrees'],
            camera_index=geometry['camera_index'], max_fps=scheme['max_fps'],
            record_video=True, use_maml=True, calibration_points=scheme['calibration_points'],
            calibration_dwell_s=scheme.get('calibration_dwell_s',2.),
            calibration_exclude_start_ms=scheme.get('calibration_exclude_start_ms',500.),
            calibration_exclude_end_ms=scheme.get('calibration_exclude_end_ms',0.),
            view_distance_cm=geometry.get('view_distance_cm',55),
            distance_mode=geometry.get('distance_mode','estimated'),
            camera_top_center=geometry['camera_top_center'],
            camera_name=geometry.get('camera_name'),
        )
        def screen_value(name):
            method = getattr(screen, name, None)
            return method() if callable(method) else None
        config['display_snapshot'] = dict(name=screen.name(), manufacturer=screen_value('manufacturer'),
            model=screen_value('model'), serial_number=screen_value('serialNumber'),
            physical_resolution_px=[config['physical_width'],config['physical_height']],
            logical_resolution_px=[area.width(),area.height()], device_pixel_ratio=dpr,
            logical_dpi_x=screen.logicalDotsPerInchX(), logical_dpi_y=screen.logicalDotsPerInchY(),
            configured_size_mm=[config['screen_width_mm'],config['screen_height_mm']])
        for key in ['camera_matrix', 'distortion', 'intrinsics_resolution']:
            if key in geometry.get('intrinsics', {}):
                config[key] = copy.deepcopy(geometry['intrinsics'][key])
        for key in ['camera_width','camera_height']:
            if key in geometry:config[key]=geometry[key]
        return config

    def start_experiment(self):
        self._start_capture(self.selected_scheme(),'experiment')

    def _start_capture(self, scheme, mode):
        if RAW_CAPTURE and mode != 'experiment':
            return
        if self._closing or self._preparing or self.experiment is not None or self._pending_test_analysis:
            return
        if self._analysis_busy():
            QMessageBox.information(self, '正在分析', '请等待当前数据分析完成后，再开始新的实验。')
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, '摄像头正在退出', '请稍候，上一份记录仍在保存或释放摄像头。')
            return
        try:
            self._active_scheme = validate_scheme(scheme)
            if RAW_CAPTURE and self._active_scheme['mode'] != 'experiment':
                return
            self._active_processing = copy.deepcopy(processing_for_mode(self.settings, mode))
            if RAW_CAPTURE:
                directory = (self._active_processing or {}).get('output_dir') or str(ROOT / 'sessions')
                self._active_processing = dict(mode='deferred', retain_raw_rgb=True, output_dir=directory)
            if mode == 'test' and self._active_processing:
                self._active_processing['mode'] = 'immediate'
            if self._active_processing and self._active_processing['mode'] == 'deferred':
                try:
                    destination = Path(self._active_processing['output_dir']).resolve(strict=True)
                    if not destination.is_dir():
                        raise NotADirectoryError(destination)
                    with tempfile.TemporaryFile(dir=destination):
                        pass
                except (OSError, ValueError) as error:
                    raise ValueError('无法写入实验保存位置，请在设置中重新选择。') from error
                self._active_processing['output_dir'] = str(destination)
            person = self.catalog.upsert_person(self.participant_info.values())
            self.people_panel.select_person(person['key'])
            self._active_participant = copy.deepcopy(self.participant_info.values())
            self._run_mode = mode
            self.cfg = self.build_config(self._active_scheme)
            self.cfg['defer_calibration'] = mode == 'experiment' and (self._active_processing or {}).get('mode') == 'deferred'
            self.calibration = None
            self._handled_worker_failure = False
            self.worker = Worker(self._active_scheme['model_id'], self.cfg)
        except Exception as error:
            self.show_error(str(error))
            return
        self._preparing = True
        self.history_dialog.hide()
        self.analysis_link.hide()
        self.record_folder_link.hide()
        self.set_controls(False)
        title = '准备测试' if mode == 'test' else '准备实验'
        self.status.setText(f'正在{title}…')
        self._prepare_dialog = QDialog(self)
        self._prepare_dialog.setWindowTitle(title)
        self._prepare_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._prepare_dialog.setFixedWidth(440)
        content = QVBoxLayout(self._prepare_dialog)
        content.setContentsMargins(28, 26, 28, 26)
        content.setSpacing(18)
        self._prepare_status = label('正在连接默认摄像头…', 'Muted', True)
        content.addWidget(self._prepare_status)
        progress = QProgressBar()
        progress.setRange(0, 0)
        content.addWidget(progress)
        content.addWidget(button('取消', self._prepare_dialog.reject))
        install_chrome(self._prepare_dialog, content, resizable=False)
        self._prepare_dialog.rejected.connect(self.cancel_prepare)
        self.worker.status.connect(self.worker_status)
        self.worker.ready.connect(self.camera_ready)
        self.worker.calibrated.connect(self.on_calibrated)
        self.worker.calibration_progress.connect(self.on_calibration_progress)
        self.worker.failed.connect(self.worker_failed)
        self.worker.finished.connect(self.worker_finished)
        self.worker.start()
        self._prepare_dialog.open()

    def worker_status(self, text):
        self.status.setText(text)
        if self._preparing and self._prepare_dialog is not None:
            self._prepare_status.setText(text)

    def cancel_prepare(self):
        if not self._preparing:
            return
        self._preparing = False
        if self._prepare_dialog is not None:
            self._prepare_dialog.deleteLater()
            self._prepare_dialog = None
        if self.worker is not None:
            self.worker.stop()
        self.status.setText('已取消，正在释放摄像头…')
        self.record_folder_link.setVisible(self.last_folder is not None)
        if self.worker is None or not self.worker.isRunning():
            self.set_controls(True)

    def camera_ready(self):
        if not self._preparing:
            return
        self._preparing = False
        if self._prepare_dialog is not None:
            self._prepare_dialog.accept()
            self._prepare_dialog.deleteLater()
            self._prepare_dialog = None
        try:
            self.experiment = Experiment(self, self._active_scheme)
            self.experiment.open_flow()
        except Exception as error:
            self.worker.stop()
            if self.experiment is not None:
                self.experiment.finish('capture_or_calibration_failed')
            self.show_error(str(error))

    def on_calibrated(self, data):
        if self.experiment is not None:
            self.experiment.ready_now(data)

    def on_calibration_progress(self, percent, message):
        self.status.setText(f'个人校准 {percent}%  {message.replace(chr(183), " ")}')
        if self.experiment is not None:
            self.experiment.show_fit_progress(percent, message)

    def worker_failed(self, text):
        if self._handled_worker_failure:
            return
        self._handled_worker_failure = True
        self._preparing = False
        if self._prepare_dialog is not None:
            self._prepare_dialog.accept()
            self._prepare_dialog.deleteLater()
            self._prepare_dialog = None
        if self.experiment is not None:
            self.experiment.finish('capture_or_calibration_failed')
        if self.worker is not None:
            self.worker.stop()
            if self.worker.last_record_folder is not None:
                self.last_folder = self.worker.last_record_folder
        self.show_error(text)

    def worker_finished(self):
        if self.experiment is None and not self._preparing:
            if self._pending_test_analysis:
                self._launch_pending_test_analysis();return
            self.set_controls(True)

    def set_controls(self, enabled):
        enabled=bool(enabled and not self._closing and not self._pending_test_analysis and not self._analysis_busy())
        self.people_panel.setEnabled(enabled)
        self.participant_combo.setEnabled(enabled)
        self.new_person_button.setEnabled(enabled)
        self.history_button.setEnabled(enabled or self._analysis_busy())
        self.history_dialog.experiment_button.setEnabled(enabled)
        self.history_dialog.test_button.setEnabled(enabled)
        self.history_dialog.select_button.setEnabled(enabled and self.current_person is not None)
        self.records_panel.setEnabled(enabled or self._analysis_busy())
        self.experiment_button.setEnabled(enabled)
        self.edit_person_button.setEnabled(enabled and self.current_person is not None)
        self.scheme_combo.setEnabled(enabled)
        self.participant_info.setEnabled(enabled)
        self.start_button.setEnabled(enabled)
        self.scheme_settings_button.setEnabled(enabled)
        self.test_button.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)
        if enabled:
            self.records_panel._selection_changed()
        self.record_folder_link.setEnabled(enabled)

    def experiment_finished(self, folder, reason, *, segments=()):
        self.experiment = None
        if folder is not None:
            self.last_folder = Path(folder)
            try:
                for segment in segments:
                    if segment != self.last_folder:
                        self.catalog.remember_record(segment)
                self.catalog.remember_record(self.last_folder)
                self.refresh_history()
            except (ValueError, OSError):
                pass
            self.settings['last_session'] = str(self.last_folder)
            self.settings['last_test_session' if self._run_mode=='test' else 'last_experiment_session']=str(self.last_folder)
            try:
                self.settings = save_settings(self.settings)
            except Exception:
                pass  # Source recording is already saved; settings are ancillary.
            ending = '采集完成' if reason == 'completed' else '本次采集已保存'
            self.record_folder_link.setToolTip(str(self.last_folder))
            self.record_folder_link.show()
            deferred = (self._active_processing or {}).get('mode') == 'deferred'
            if deferred and reason in ('completed', 'user_stopped', 'window_closed'):
                if (self.last_folder / 'camera.mp4').is_file():
                    self.status.setText('记录已保存，可在历史记录中打开文件夹。' if RAW_CAPTURE else
                                        '记录已保存，可在历史记录中分析。')
                else:
                    self.status.setText('记录已保存，本次没有可用的 RGB 画面。')
            elif reason in ('completed','user_stopped','window_closed'):
                folders = [segment for segment in segments if (segment / 'camera.mp4').is_file()]
                self._pending_test_analysis = folders[0] if folders else self.last_folder
                self._queued_experiment_analysis = folders[1:]
                self.status.setText('测试已保存，正在准备自动分析。' if self._run_mode=='test' else '实验已保存，正在准备推理并导出TXT。')
                QTimer.singleShot(0,self._launch_pending_test_analysis)
            else:
                self.status.setText(f'{ending}。')
                self.analysis_link.show()
        else:
            self.status.setText('已结束准备流程，未开始正式采集。')
            self.record_folder_link.setVisible(self.last_folder is not None)
        if self.worker is not None:
            self.worker.stop()
        if self.worker is None or not self.worker.isRunning():
            self.set_controls(True)

    def _launch_pending_test_analysis(self):
        folder=self._pending_test_analysis
        if folder is None or self._closing or self._preparing or self.experiment is not None:
            return
        if self.worker is not None and self.worker.isRunning():
            return
        self._pending_test_analysis=None
        self.analysis_link.show()
        self.open_analysis(folder=folder,auto_run=True)

    def _automatic_analysis_finished(self):
        if self._queued_experiment_analysis and not self._closing:
            self._pending_test_analysis = self._queued_experiment_analysis.pop(0)
            QTimer.singleShot(0, self._launch_pending_test_analysis)
        else:
            self.set_controls(True)

    def open_record_folder(self):
        if self.last_folder is not None and self.last_folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_folder)))
        else:
            self.show_error('记录文件夹不可用，请检查磁盘是否已连接。')

    def open_analysis(self,folder=None,auto_run=False):
        if RAW_CAPTURE or self._preparing or self.experiment is not None or (self.worker is not None and self.worker.isRunning()):
            return
        self.history_dialog.hide()
        if self.analysis_window is not None:
            if self._analysis_busy():
                self.analysis_window.show()
                self.analysis_window.raise_()
                self.analysis_window.activateWindow()
                return
            self.analysis_window.close()
            self.analysis_window.deleteLater()
        from analysis_ui import AnalysisWindow
        from coordinates import session_mode
        initial = Path(folder).resolve() if folder else None
        mode = self.workspace_mode
        person_key = self.current_person.get('key') if self.current_person else None
        if initial:
            try:
                entry = self.catalog.remember_record(initial)
                mode = entry['mode']
                person_key = entry['person_key']
            except (ValueError, OSError, KeyError) as error:
                self.show_error(str(error))
                if auto_run:
                    self._automatic_analysis_finished()
                return
        folders = [row['path'] for row in self.catalog.records(person_key, mode)] if person_key else []
        if initial is None:
            initial = folders[0] if folders else None
        self.analysis_window = AnalysisWindow(self, initial, record_roots=self.history_roots(),
                                             record_mode=mode, allowed_folders=folders)
        self.analysis_window.processing_config = processing_for_mode(self.settings, 'experiment') or {}
        self.analysis_window.analysis_finished.connect(self.refresh_history)
        self.analysis_window.show()
        if auto_run:
            window=self.analysis_window
            self._analysis_starting=True
            self.set_controls(False)
            def begin():
                if self._closing or self.analysis_window is not window or not window.isVisible():
                    self._analysis_starting=False
                    self._automatic_analysis_finished()
                    return
                try:window.run_analysis()
                finally:self._analysis_starting=False
                if window.task is not None and window.task.isRunning():
                    self.set_controls(False)
                    window.task.finished.connect(self._automatic_analysis_finished)
                else:self._automatic_analysis_finished()
            QTimer.singleShot(0,begin)

    def show_error(self, text):
        message = str(text)
        summary = message.strip().splitlines()[-1] if message.strip() else '操作未完成'
        self.status.setText(summary)
        if len(message.splitlines()) > 1:
            try:
                log = ROOT / '.cache' / 'last_error.log'
                log.parent.mkdir(exist_ok=True)
                log.write_text(message, encoding='utf8')
            except OSError:
                pass
        QMessageBox.warning(self, '操作未完成', summary)

    def closeEvent(self, event):
        if self._analysis_busy():
            QMessageBox.information(self, '正在生成报告', '请等待数据分析和视频导出完成后再退出。')
            event.ignore()
            return
        self._closing=True
        self._pending_test_analysis=None
        self._queued_experiment_analysis.clear()
        if self._preparing:
            self.cancel_prepare()
        if self.experiment is not None:
            self.experiment.finish('application_closed')
        if self.worker is not None and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(5000):
                self.status.setText('正在等待摄像头退出，请稍后再关闭。')
                event.ignore()
                self._closing=False
                return
        if self.analysis_window is not None:
            self.analysis_window.close()
        self.history_dialog.close()
        self.lsl.close()
        event.accept()


class Experiment(ImageBoard):
    def __init__(self, main, scheme):
        super().__init__()
        self.main = main
        self.scheme = copy.deepcopy(scheme)
        self.participant = copy.deepcopy(getattr(main, '_active_participant', {}))
        self.processing = copy.deepcopy(getattr(main, '_active_processing', None))
        self.cfg = main.cfg
        self.edit = False
        self.ready = False
        self.recording = False
        self._finishing = False
        self.folder = None
        origin=copy.deepcopy(scheme.get('origin',{'x':.5,'y':.5}))
        self.mode=scheme.get('mode','experiment')
        self.two_part = self.mode == 'experiment' and scheme.get('experiment_protocol') == 'two_part'
        self.segment_index = 1
        self.segment_folders = []
        self.experiment_session_id = uuid.uuid4().hex if self.two_part else None
        self.rest_duration_s = float(scheme.get('rest_minutes', 15.)) * 60 if self.two_part else 0.
        self.rest_deadline = None
        self.waiting_for_next_calibration = False
        self.post_calibrating = False
        self._awake_guard = None
        self.background_color = scheme.get('background_color', '#f0f0f0')
        self.fixation_enabled = self.mode == 'experiment' and scheme.get('fixation_enabled', False)
        self.saved_points = (copy.deepcopy(scheme['points']) if self.mode=='test'
                             else [dict(origin,seconds=float(scheme['duration_minutes'])*60)])
        self.duration=sum(float(point['seconds']) for point in self.saved_points)
        self.points = []
        self.active = None
        self.target_style = scheme.get('target_style', 'dot')
        self.target_color = scheme.get('target_color', '#35452e')
        self.target_remaining_s = None
        self.direction_enabled = self.mode == 'test' and scheme.get('direction_task_enabled', False) is True
        self.direction_countdown_s = int(scheme.get('direction_countdown_s', 3))
        self.direction_visible_s = float(scheme.get('direction_visible_s', .3))
        self.direction_phase = 'idle'
        self.direction_point_started = None
        self.direction_prompt_delay_s = 0.
        self.direction_prompt_shown = False
        self.direction_prompt_first_shown = None
        self.direction_prompt_actual_visible_s = None
        self.direction_expected = None
        self.direction_response_started = None
        self.direction_countdown_started = None
        self.direction_results = []
        self.direction_excluded_ids = []
        self.direction_feedback = ''
        self.target_diameter_px,self.countdown_font_px,self.hint_font_px=display_dimensions(scheme)
        self.message_anchor='bottom';self._hint_generation=0
        self.cal_index = 0
        self.calibrating = False
        self.counting_down = False
        self.fitting = False
        self.calibration_countdown_s = float(scheme.get('calibration_countdown_s',3.))
        self.calibration_dwell_s,self.calibration_window_start_s,self.calibration_window_end_s=calibration_timing(scheme)
        self.calpoints = [(.5, .5), (.15, .15), (.85, .15), (.15, .85), (.85, .85)]
        if scheme['calibration_points'] == 9:
            self.calpoints = [(x, y) for y in [.15, .5, .85] for x in [.15, .5, .85]]
        self.setWindowTitle('实验采集  Esc结束')
        self.setGeometry(QApplication.primaryScreen().geometry())
        image=resolve_image(scheme)
        if image is not None:self.load(image)
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)
        self.flow_progress = QProgressBar(self)
        self.flow_progress.setRange(0, 100)
        self.flow_progress.setValue(0)
        self.flow_progress.setTextVisible(False)
        self.flow_progress.setAccessibleName('个人校准进度')
        self.flow_progress.hide()
        self.flow_progress_label = QLabel(self)
        self.flow_progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.flow_progress_label.setStyleSheet(f'color:#1d1d1f;background:transparent;padding:3px;font-size:{self.hint_font_px:g}px;')
        self.flow_progress_label.hide()

    def open_flow(self):
        self.setCursor(Qt.CursorShape.BlankCursor)
        if self.two_part and sys.platform == 'win32':
            # Keep the countdown and the post-rest S prompt visible on battery.
            from ctypes import windll
            set_execution_state = windll.kernel32.SetThreadExecutionState
            previous = set_execution_state(0x80000003)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
            if previous:
                self._awake_guard = set_execution_state, previous
        if self.scheme['calibration_points']:
            self.start_calibration_countdown()
        else:
            self.ready_now(dict(target_count=0, model_sha256=self.main.worker.model_hash,
                                runtime_sha256=getattr(self.main.worker,'runtime_hash',None),
                                affine=None, head_adapted=False, valid_frames=0))
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()
        self.timer.start()

    def start_calibration_countdown(self):
        self.ready = False
        self.points = []
        self.active = None
        self.target_remaining_s = None
        self._hint_generation += 1
        self.message = ''
        if self.calibration_countdown_s > 0:
            self.counting_down = True
            self.countdown_started = time.perf_counter()
        else:
            self.start_calibration()
        self.update()

    def start_calibration(self):
        """Begin sampling only after the optional preparation countdown."""
        if self._finishing:
            return
        self.counting_down = False
        self.fitting = False
        self.calibrating = True
        self.cal_index = 0
        self.target_style = self.scheme.get('target_style', 'dot')
        self.target_color = self.scheme.get('target_color', '#35452e')
        self.target_diameter_px = display_dimensions(self.scheme)[0]
        self.flow_progress.hide()
        self.flow_progress_label.hide()
        self.message = ''
        if not self.post_calibrating and (self.two_part or getattr(self.main.worker, 'defer_calibration', False)):
            metadata = self.prepare_recording_folder()
            self.main.worker.prepare_calibration_storage(self.folder, metadata)
        self.main.worker.begin_calibration()
        self.cal_start = time.perf_counter()
        self.set_cal_target()
        self._emit_calibration_marker('START')

    def _emit_lsl(self, code):
        if self.two_part:
            outlet = getattr(self.main, 'lsl', None)
            if outlet is not None:
                outlet.emit(code, session_id=self.experiment_session_id, segment_index=self.segment_index)

    def _emit_calibration_marker(self, boundary):
        index = (self.segment_index - 1) * 2 + (2 if self.post_calibrating else 1)
        self._emit_lsl(f'CALIBRATION_{index}_{boundary}')

    def paintEvent(self,event):
        if self.recording and self.direction_enabled and self.direction_phase in ('response', 'countdown'):
            super().paintEvent(event)
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor('#242424'))
            font = QFont(self.font())
            scale = float(self.scheme.get('display_scale', 1.))
            if self.direction_phase == 'response':
                width,height=float(self.width()),float(self.height())
                key_size=max(44.,min(72.,64.*scale,min(width,height)*.11))
                step=key_size*1.16
                cluster_height=key_size*2.16
                title_height=max(48.,52.*scale)
                title_rect=QRectF(24.,height/2-cluster_height/2-title_height-30.,width-48.,title_height)
                font.setPixelSize(max(24,round(34*scale)))
                font.setWeight(QFont.Weight.Medium)
                painter.setFont(font)
                painter.setPen(QColor('#242424'))
                painter.drawText(title_rect,Qt.AlignmentFlag.AlignCenter,'刚才的方向？')
                keypad_center=QPointF(width/2,height/2+step/2)
                draw_direction_keypad(painter,keypad_center,key_size)
                hint_rect=QRectF(24.,height/2+cluster_height/2+28.,width-48.,42.)
                font.setPixelSize(max(18,round(self.hint_font_px*.82)))
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor('#626268'))
                painter.drawText(hint_rect,Qt.AlignmentFlag.AlignCenter,'请按对应方向键')
            else:
                remaining = max(1, math.ceil(self.direction_countdown_s -
                    (time.perf_counter() - self.direction_countdown_started)))
                width,height=float(self.width()),float(self.height())
                block_top=(height-236.)/2
                feedback_rect=QRectF(24.,block_top,width-48.,48.)
                number_rect=QRectF(24.,block_top+76.,width-48.,92.)
                hint_rect=QRectF(24.,block_top+196.,width-48.,40.)
                font.setPixelSize(max(20,round(self.hint_font_px)))
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor('#3f3f46'))
                painter.drawText(feedback_rect,Qt.AlignmentFlag.AlignCenter,self.direction_feedback)
                font.setPixelSize(max(24,round(64*scale)))
                font.setWeight(QFont.Weight.Medium)
                painter.setFont(font)
                painter.setPen(QColor('#18181b'))
                painter.drawText(number_rect,Qt.AlignmentFlag.AlignCenter,str(remaining))
                font.setPixelSize(max(18,round(self.hint_font_px*.8)))
                font.setWeight(QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(QColor('#71717a'))
                painter.drawText(hint_rect,Qt.AlignmentFlag.AlignCenter,'准备下一测试点')
            return
        if self.counting_down or self.fitting or self.rest_deadline is not None or self.waiting_for_next_calibration:
            # Preparation and fitting are separate from visual fixation tasks.
            painter = QPainter(self)
            painter.fillRect(self.rect(),QColor('#f0f0f0'))
            if self.rest_deadline is not None:
                seconds = max(0, math.ceil(self.rest_deadline - time.perf_counter()))
                minutes, seconds = divmod(seconds, 60)
                title = f'休息：{minutes:02d}:{seconds:02d}'
                text_size = 54
            elif self.waiting_for_next_calibration:
                title = '休息结束，按 S 开始校准'
                text_size = 34
            elif self.counting_down:
                remaining=max(1,math.ceil(self.calibration_countdown_s-(time.perf_counter()-self.countdown_started)))
                title = str(remaining)
                text_size = 64
            else:
                return
            font = QFont(self.font())
            font.setPixelSize(max(24, round(text_size * float(self.scheme.get('display_scale', 1.)))))
            font.setWeight(QFont.Weight.Medium)
            painter.setFont(font)
            painter.setPen(QColor('#242424'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, title)
            return
        super().paintEvent(event)
        if self.recording and self.direction_enabled and self.direction_prompt_visible():
            painter = QPainter(self)
            center = QPointF(*self.point_pos(self.points[self.active]))
            draw_direction_arrow(painter, center, self.direction_expected,
                                 self.target_diameter_px)

    def direction_prompt_visible(self, now=None):
        if self.direction_phase != 'point' or not self.direction_prompt_shown or self.active is None:
            return False
        now = time.perf_counter() if now is None else now
        deadline = self.direction_point_started + self.direction_prompt_delay_s + self.direction_visible_s
        return now < deadline

    def set_cal_target(self):
        x, y = self.calpoints[self.cal_index]
        self.points = [dict(x=x, y=y)]
        self.active = 0
        self.target_remaining_s = self.calibration_dwell_s
        self.main.worker.set_target(self.cal_index, [x * self.cfg['physical_width'], y * self.cfg['physical_height']])
        self.message = ''
        self.flow_progress.hide()
        self.flow_progress_label.hide()
        self.update()

    def point_pos(self,point):
        # Origin coordinates belong to the full screen, even for letterboxed images.
        return point['x']*self.width(),point['y']*self.height()

    def ready_now(self, calibration):
        if self._finishing:
            return
        self.calibrating = False
        self.counting_down = False
        self.fitting = False
        self.flow_progress.setValue(100)
        self.flow_progress.hide()
        self.flow_progress_label.hide()
        if self.post_calibrating:
            self.post_calibrating = False
            if self.segment_index == 2:
                self.finish('completed')
            elif self.rest_duration_s:
                self.rest_deadline = time.perf_counter() + self.rest_duration_s
                self.update()
            else:
                self.waiting_for_next_calibration = True
                self.update()
            return
        self.main.calibration = calibration
        self.ready = True
        self.points = copy.deepcopy(self.saved_points)
        self.active = 0 if self.mode == 'test' or self.fixation_enabled else None
        if self.fixation_enabled:
            self.target_style = self.scheme.get('fixation_style', 'cross')
            self.target_color = self.scheme.get('fixation_color', '#202020')
            self.target_diameter_px = float(self.scheme.get('fixation_diameter_px', 20)) * float(self.scheme.get('display_scale', 1))
        self.target_remaining_s = float(self.saved_points[0]['seconds'])
        self.set_hint('按 S 开始',persistent=True)
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margin=min(32,max(12,round(self.width()*.03)))
        width=max(0,self.width()-2*margin)
        label_height=max(34,math.ceil(self.hint_font_px*1.7))
        bar_y=max(0,self.height()-32)
        self.flow_progress.setGeometry(margin,bar_y,width,6)
        self.flow_progress_label.setGeometry(margin,max(0,bar_y-label_height-10),width,label_height)

    def set_hint(self,text,persistent=False):
        self._hint_generation+=1;token=self._hint_generation;self.message=text
        if not persistent:
            def clear():
                if token==self._hint_generation and not self._finishing:self.message='';self.update()
            QTimer.singleShot(1000,clear)
        self.update()

    def show_fit_progress(self, percent, message):
        if self._finishing or not self.fitting:
            return
        self.flow_progress.show()
        self.flow_progress.setValue(max(0,min(100,round(percent))))
        self.flow_progress_label.setText('正在保存校准素材' if self.cfg.get('defer_calibration') else '正在更新参数')
        self.flow_progress_label.show()
        self.message = ''
        self.update()

    def current_target(self, elapsed):
        endpoint=0.
        for index,point in enumerate(self.saved_points):
            endpoint+=float(point['seconds'])
            if elapsed<endpoint:return index
        return len(self.saved_points)-1

    def update_target(self, index):
        x, y = self.point_pos(self.points[index])
        self.main.worker.set_target(index, [x * self.cfg['dpr'], y * self.cfg['dpr']])

    def start_direction_point(self, index):
        """Start one event-driven point; response and countdown time are excluded."""
        self.active = index
        self.direction_phase = 'point'
        self.direction_feedback = ''
        self.message = ''
        self.update_target(index)
        now = time.perf_counter()
        self.direction_point_started = now
        self.direction_response_started = None
        self.direction_countdown_started = None
        self.direction_prompt_shown = False
        self.direction_prompt_first_shown = None
        self.direction_prompt_actual_visible_s = None
        self.direction_expected = random.choice(DIRECTIONS)
        point_seconds = float(self.saved_points[index]['seconds'])
        self.direction_prompt_delay_s = random.uniform(0., max(0., point_seconds - self.direction_visible_s))
        self.target_remaining_s = point_seconds
        self.direction_results.append(dict(
            target_id=index,
            expected_direction=self.direction_expected,
            prompt_delay_s=self.direction_prompt_delay_s,
            prompt_visible_s=self.direction_visible_s,
            prompt_shown=False,
            actual_prompt_visible_s=None,
            point_started_s=now-self.started,
            point_ended_s=now-self.started+point_seconds,
            response_started_s=None,
            response_direction=None,
            correct=None,
            reaction_time_s=None,
            point_finished_s=None,
            status='pending',
            excluded=False,
        ))

    def _finish_direction_prompt(self, now):
        if not self.direction_prompt_shown or self.direction_prompt_actual_visible_s is not None:
            return
        deadline = self.direction_point_started + self.direction_prompt_delay_s + self.direction_visible_s
        if now >= deadline:
            self.direction_prompt_actual_visible_s = max(0., deadline-self.direction_prompt_first_shown)
            self.direction_results[-1]['actual_prompt_visible_s'] = self.direction_prompt_actual_visible_s

    def tick_direction(self, now):
        index = self.direction_results[-1]['target_id'] if self.direction_results else 0
        if self.direction_phase == 'point':
            elapsed = now-self.direction_point_started
            seconds = float(self.saved_points[index]['seconds'])
            self.target_remaining_s = max(0., seconds-elapsed)
            prompt_time = self.direction_point_started+self.direction_prompt_delay_s
            deadline = prompt_time+self.direction_visible_s
            if not self.direction_prompt_shown and prompt_time <= now < deadline:
                self.direction_prompt_shown = True
                self.direction_prompt_first_shown = now
                self.direction_results[-1].update(prompt_shown=True, prompt_first_shown_s=now-self.started)
            self._finish_direction_prompt(now)
            if elapsed >= seconds:
                self._finish_direction_prompt(now)
                self.direction_phase = 'response'
                self.active = None
                self.target_remaining_s = None
                self.direction_response_started = now
                self.direction_results[-1]['response_started_s'] = now-self.started
                self.main.worker.set_target(-1, None)
                self.message = ''
            self.main.status.setText(f'方向任务  第 {index+1} / {len(self.saved_points)} 点')
        elif self.direction_phase == 'response':
            self.main.status.setText(f'等待方向作答  第 {index+1} / {len(self.saved_points)} 点')
        elif self.direction_phase == 'countdown':
            remaining = self.direction_countdown_s-(now-self.direction_countdown_started)
            if remaining <= 0:
                self.start_direction_point(index+1)
            else:
                self.main.status.setText(f'下一点倒计时  {max(1,math.ceil(remaining))} 秒')

    def answer_direction(self, direction):
        if not self.recording or not self.direction_enabled or self.direction_phase != 'response':
            return False
        if direction not in DIRECTIONS:
            return False
        now = time.perf_counter()
        result = self.direction_results[-1]
        index = result['target_id']
        correct = direction == result['expected_direction']
        result.update(response_direction=direction, correct=correct,
                      reaction_time_s=max(0., now-self.direction_response_started),
                      point_finished_s=now-self.started,
                      status='correct' if correct else 'incorrect', excluded=not correct)
        if not correct:
            self.direction_excluded_ids.append(index)
        self.direction_feedback = '回答正确' if correct else '方向错误，本点数据已舍弃'
        if index+1 >= len(self.saved_points):
            self.direction_phase = 'complete'
            self.finish('completed')
            return True
        if self.direction_countdown_s > 0:
            self.direction_phase = 'countdown'
            self.direction_countdown_started = now
            self.main.worker.set_target(-1, None)
        else:
            self.start_direction_point(index+1)
        self.update()
        return True

    def direction_payload(self, reason=None):
        """Finalize an unfinished point and return the auditable response record."""
        if self.recording and self.direction_results and self.direction_results[-1]['status'] == 'pending':
            result = self.direction_results[-1]
            result.update(status='unanswered', correct=False, excluded=True,
                          stop_reason=reason, point_finished_s=time.perf_counter()-self.started)
            self.direction_excluded_ids.append(result['target_id'])
        responses = copy.deepcopy(self.direction_results)
        reaction_times = [float(row['reaction_time_s']) for row in responses
                          if row.get('reaction_time_s') is not None]
        correct = sum(row.get('status') == 'correct' for row in responses)
        incorrect = sum(row.get('status') == 'incorrect' for row in responses)
        unanswered = sum(row.get('status') == 'unanswered' for row in responses)
        answered = correct+incorrect
        excluded = sorted(set(self.direction_excluded_ids))
        return dict(
            version=1, enabled=self.direction_enabled,
            visible_s=self.direction_visible_s, countdown_s=self.direction_countdown_s,
            responses=responses, excluded_target_ids=excluded,
            summary=dict(total_points=len(self.saved_points), recorded_points=len(responses),
                         answered_points=answered, correct_points=correct,
                         incorrect_points=incorrect, unanswered_points=unanswered,
                         accuracy=correct/answered if answered else None,
                         mean_reaction_time_s=sum(reaction_times)/len(reaction_times) if reaction_times else None,
                         excluded_target_ids=excluded),
        )

    def tick(self):
        if self._finishing:
            return
        screen = QApplication.primaryScreen()
        dpr = float(screen.devicePixelRatio())
        geometry = screen.geometry()
        if (screen.name() != self.cfg['screen_name'] or dpr != self.cfg['dpr']
                or round(geometry.width() * dpr) != self.cfg['physical_width']
                or round(geometry.height() * dpr) != self.cfg['physical_height']):
            self.finish('display_changed')
            self.main.show_error('屏幕或DPI发生变化，当前流程已结束。请核对屏幕设置后重新开始。')
            return
        if self.rest_deadline is not None:
            if time.perf_counter() >= self.rest_deadline:
                self.rest_deadline = None
                self.waiting_for_next_calibration = True
            self.update()
            return
        if self.counting_down:
            if time.perf_counter()-self.countdown_started>=self.calibration_countdown_s:
                try:
                    self.start_calibration()
                except Exception as error:
                    self.finish('calibration_failed')
                    self.main.show_error(str(error))
            self.update()
            return
        if self.calibrating:
            target_elapsed = max(0.0, time.perf_counter() - self.cal_start)
            self.target_remaining_s = max(0.0, self.calibration_dwell_s - target_elapsed)
        if self.calibrating and time.perf_counter() - self.cal_start >= self.calibration_dwell_s:
            self.cal_index += 1
            self.cal_start = time.perf_counter()
            if self.cal_index == len(self.calpoints):
                self._emit_calibration_marker('END')
                self.calibrating = False
                self.fitting = True
                self.points = []
                self.active = None
                self.target_remaining_s = None
                self.message = ''
                self.show_fit_progress(0,'')
                try:
                    self.main.worker.request_calibration_fit()
                except Exception as error:
                    self.finish('calibration_failed')
                    self.main.show_error(str(error))
                    return
            else:
                self.set_cal_target()
        elif self.recording:
            if self.direction_enabled:
                self.tick_direction(time.perf_counter())
                self.update()
                return
            elapsed = time.perf_counter() - self.started
            if elapsed >= self.duration:
                if self.two_part:
                    self.finish_segment()
                else:
                    self.finish('completed')
                return
            index = self.current_target(elapsed)
            if self.active is not None and index != self.active:
                self.active = index
                self.update_target(index)
            endpoint=sum(float(point['seconds']) for point in self.saved_points[:index+1])
            self.target_remaining_s=max(0.,endpoint-elapsed)
            self.main.status.setText(f'正在采集  {elapsed:.0f} / {self.duration:.0f} 秒')
        self.update()

    def prepare_recording_folder(self):
        if self.folder is not None:
            return self._record_metadata
        deferred = self.processing is not None and self.processing['mode'] == 'deferred'
        record_root = Path(self.processing['output_dir']) if deferred else ROOT / 'sessions'
        folder = record_root / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        folder.mkdir(parents=not deferred)
        image_path=None
        if self.image_path:
            if self.mode == 'experiment':
                image_path = folder / 'stimulus.png'
                if not self.pix.save(str(image_path), 'PNG'):
                    raise OSError('无法保存本次实验显示的背景')
            else:
                image_path = folder / ('stimulus' + Path(self.image_path).suffix)
                shutil.copy2(self.image_path, image_path)
        from deferred_calibration import digest_file
        metadata = dict(
            version=2, scheme=copy.deepcopy(self.scheme), schemeid=self.scheme['id'],
            participant=copy.deepcopy(self.participant),
            config=copy.deepcopy(self.cfg), calibration=copy.deepcopy(self.main.calibration),
            image_path=image_path.name if image_path else '',
            stimulus_sha256=digest_file(image_path) if image_path else None,
            points=copy.deepcopy(self.saved_points),
            model_id=self.scheme['model_id'], created_local=datetime.now().astimezone().isoformat(),
            planned_duration_s=self.duration,mode=self.mode,protocol_version=3,recording_protocol_version=3,
            actual_duration_s=0., acquisition_state='calibration_only', calibration_id=uuid.uuid4().hex,
            model_sha256=getattr(self.main.worker,'model_hash',None) or (self.main.calibration or {}).get('model_sha256'),
            fixation_enabled=self.fixation_enabled,
            background_mode=self.scheme.get('background_mode', 'solid' if not image_path else 'image'),
            background_color=self.background_color,
            background_image_matte='#151515',
            transition_exclusion_s=float(self.scheme.get('transition_exclusion_s',1.)),assume_fixation=self.mode=='test',
            direction_task_enabled=self.direction_enabled,
            direction_countdown_s=self.direction_countdown_s,
            direction_visible_s=self.direction_visible_s,
            direction_task=dict(enabled=self.direction_enabled,status='recording',responses=[],excluded_target_ids=[]),
            origin=copy.deepcopy(self.scheme.get('origin',{'x':.5,'y':.5})),
            delete_raw_after_analysis=self.scheme.get('delete_raw_after_analysis',False),
            calibration_timing=dict(countdown_s=self.calibration_countdown_s,dwell_s=self.calibration_dwell_s,
                sample_start_s=self.calibration_window_start_s,sample_end_s=self.calibration_window_end_s,
                exclude_start_ms=self.scheme.get('calibration_exclude_start_ms',500.),
                exclude_end_ms=self.scheme.get('calibration_exclude_end_ms',0.)),
            coordinates='Primary-monitor local pixels; logical=physical/DPR; delta=prediction-reference, signed',
            camera_video_timing='CFR container; capture.csv timestamps are authoritative',
            inference_mode='offline after acquisition; experiment exports TXT and test generates analysis',
        )
        metadata['acquisition_environment'] = dict(version=1,
            display=copy.deepcopy(self.cfg.get('display_snapshot',{})),
            camera=dict(index=self.cfg.get('camera_index',0),name=self.cfg.get('camera_name'),
                requested_width=self.cfg.get('camera_width',1280),requested_height=self.cfg.get('camera_height',720),
                requested_fps=self.cfg.get('camera_fps',30),camera_top_center=self.cfg.get('camera_top_center',True),
                camera_x_mm=self.cfg.get('camera_x_mm',0),camera_y_mm=self.cfg.get('camera_y_mm',0),
                camera_gap_mm=self.cfg.get('camera_y_mm',0),
                view_distance_mm=float(self.cfg.get('view_distance_cm',55))*10,
                view_distance_cm=self.cfg.get('view_distance_cm'),distance_mode=self.cfg.get('distance_mode'),
                camera_matrix=copy.deepcopy(self.cfg.get('camera_matrix')),distortion=copy.deepcopy(self.cfg.get('distortion')),
                intrinsics_resolution=copy.deepcopy(self.cfg.get('intrinsics_resolution')),
                fov_degrees=self.cfg.get('fov_degrees')))
        if self.mode == 'experiment':
            metadata['storage_layout'] = 'compact_v1'
        if self.two_part:
            from record_layout import PAIRED_EXPERIMENT_LAYOUT
            metadata.update(experiment_session_id=self.experiment_session_id, segment_index=self.segment_index,
                            segment_count=2, rest_duration_s=self.rest_duration_s,
                            experiment_layout=PAIRED_EXPERIMENT_LAYOUT)
        if self.processing is not None:
            metadata['processing'] = {
                'mode': self.processing['mode'],
                'retain_raw_rgb': self.two_part or deferred or self.processing['retain_raw_rgb'],
            }
            for key in ('device', 'cpu_threads'):
                if key in self.processing:
                    metadata['processing'][key] = self.processing[key]
            metadata['delete_raw_after_analysis'] = not metadata['processing']['retain_raw_rgb']
        if self.two_part:
            metadata['delete_raw_after_analysis'] = False
            metadata['scheme']['delete_raw_after_analysis'] = False
        write_json(folder / 'annotation.json', dict(image=image_path.name if image_path else '', points=self.saved_points,
                   background_mode=metadata['background_mode'], background_color=self.background_color))
        write_json(folder / 'session.json', metadata)
        self.folder = folder
        self._record_metadata = metadata
        return metadata

    def begin_recording(self):
        self.ready = False
        self.active = 0 if self.mode == 'test' or self.fixation_enabled else None
        self.update_target(0)
        metadata = self.prepare_recording_folder()
        folder = self.folder
        metadata['calibration'] = copy.deepcopy(self.main.calibration)
        # Persist adapted parameters before enabling formal recording.
        tracker = self.main.worker.tracker
        if tracker is not None and tracker.head is not None:
            np.savez(folder / 'personal_maml.npz', **{k: v.detach().cpu().numpy() for k, v in tracker.head.items()})
        write_json(folder / 'session.json', metadata)
        self.started = self.main.worker.begin_record(folder, metadata)
        self.duration = metadata['planned_duration_s']
        self.target_remaining_s = float(self.saved_points[0]['seconds'])
        self.folder = folder
        self.recording = True
        if self.direction_enabled:
            self.direction_results = []
            self.direction_excluded_ids = []
            self.start_direction_point(0)
        self.flow_progress.hide()
        self.flow_progress_label.hide()
        self._hint_generation+=1
        self.message = ''
        self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.finish('user_stopped')
        elif event.key() == Qt.Key.Key_S and not event.isAutoRepeat():
            try:
                if self.waiting_for_next_calibration:
                    self.waiting_for_next_calibration = False
                    self.segment_index = 2
                    self.folder = None
                    self.main.calibration = None
                    self._emit_lsl('S_2')
                    self.start_calibration_countdown()
                elif self.ready and not self.recording:
                    self.begin_recording()
                    self._emit_lsl('S_1' if self.segment_index == 1 else 'S_3')
            except Exception as error:
                self.finish('recording_failed')
                self.main.show_error(str(error))
        elif self.recording and self.direction_enabled and self.direction_phase == 'response':
            keys = {Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down',
                    Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right'}
            direction = keys.get(event.key())
            if direction is not None:
                self.answer_direction(direction)
                event.accept()

    def finish_segment(self):
        try:
            self._emit_lsl(f'FIXATION_{self.segment_index}_END')
            saved = self.main.worker.end_record('completed')
            if saved is None:
                raise RuntimeError('无法保存本段采集')
            self.segment_folders.append(Path(saved))
            self.recording = False
            self.post_calibrating = True
            self.main.worker.prepare_post_calibration_storage(saved)
            self.start_calibration_countdown()
        except Exception as error:
            self.finish('capture_or_calibration_failed')
            self.main.show_error(str(error))

    def finish(self, reason):
        if self._finishing:
            return
        self._finishing = True
        self._hint_generation+=1;self.unsetCursor()
        self.timer.stop()
        if self._awake_guard is not None:
            set_execution_state, previous = self._awake_guard
            set_execution_state(previous)
            self._awake_guard = None
        folder = self.folder
        try:
            if self.recording:
                if self.direction_enabled:
                    payload = self.direction_payload(reason)
                    payload['stop_reason'] = reason
                    record = self.main.worker.record
                    if record is not None:
                        record['meta']['direction_task'] = payload
                    if folder is not None:
                        write_json(folder / 'direction_responses.json', payload)
                saved = self.main.worker.end_record(reason)
                folder = saved or folder or self.main.worker.last_record_folder
                if self.two_part and saved is not None:
                    self.segment_folders.append(Path(saved))
        finally:
            self.recording = False
            self.main.experiment = None
            self.main.worker.calibration_stop_reason = reason
            self.main.worker.stop()
            self.close()
            if self.two_part:
                if self.segment_folders:
                    folder = self.segment_folders[-1]
                self.main.experiment_finished(folder, reason, segments=tuple(self.segment_folders))
            else:
                self.main.experiment_finished(folder, reason)
            self.main.show()
            self.main.raise_()
            self.main.activateWindow()

    def closeEvent(self, event):
        if not self._finishing:
            self.finish('window_closed')
        event.accept()
