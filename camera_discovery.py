"""Discover OpenCV camera indices by opening each device and reading a frame."""
from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QPushButton, QVBoxLayout)

from capture import open_working_camera
from camera_check import CameraCheckDialog


class CameraScanWorker(QThread):
    found = pyqtSignal(int, int, int)
    progress = pyqtSignal(int)

    def __init__(self, config, parent=None, *, max_index=20):
        super().__init__(parent)
        self.config = dict(config)
        self.max_index = max_index

    def run(self):
        for index in range(self.max_index + 1):
            if self.isInterruptionRequested():
                return
            self.progress.emit(index)
            camera = None
            try:
                camera, frame = open_working_camera(dict(self.config, camera_index=index))
                height, width = frame.shape[:2]
                self.found.emit(index, width, height)
            except Exception:
                continue
            finally:
                if camera is not None:
                    camera.release()


class CameraDiscoveryDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.selected_index = None
        self.setWindowTitle('检测摄像头')
        self.resize(540, 420)
        layout = QVBoxLayout(self)
        self.status = QLabel('正在检测可读取画面的摄像头…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.cameras = QListWidget()
        layout.addWidget(self.cameras, 1)
        self.note = QLabel('找到设备后可停止检测。选择摄像头并预览，确认画面后点击“使用此摄像头”。')
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.stop_button = QPushButton('停止检测')
        self.stop_button.clicked.connect(self.stop_scan)
        buttons.addWidget(self.stop_button)
        self.preview_button = QPushButton('预览画面')
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.preview_camera)
        buttons.addWidget(self.preview_button)
        buttons.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.select_button = QPushButton('使用此摄像头')
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self.select_camera)
        buttons.addWidget(self.select_button)
        layout.addLayout(buttons)
        self.cameras.currentRowChanged.connect(self.refresh_buttons)
        self.worker = CameraScanWorker(self.config, self)
        self.worker.found.connect(self.add_camera)
        self.worker.progress.connect(lambda index: self.status.setText(f'正在检测摄像头 {index}，已找到 {self.cameras.count()} 个…'))
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def add_camera(self, index, width, height):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QListWidgetItem
        item = QListWidgetItem(f'摄像头 {index}    {width} × {height}')
        item.setData(Qt.ItemDataRole.UserRole, index)
        self.cameras.addItem(item)
        if index == self.config.get('camera_index') or self.cameras.currentRow() < 0:
            self.cameras.setCurrentItem(item)

    def refresh_buttons(self, *_):
        selectable = self.cameras.currentItem() is not None and not self.worker.isRunning()
        self.preview_button.setEnabled(selectable)
        self.select_button.setEnabled(selectable)

    def scan_finished(self):
        self.stop_button.setEnabled(False)
        count = self.cameras.count()
        if self.worker.isInterruptionRequested():
            self.status.setText(f'检测已停止，找到 {count} 个摄像头。')
        else:
            self.status.setText(f'检测完成，找到 {count} 个摄像头。' if count else
                                '未找到可读取画面的摄像头，请检查相机权限和设备占用。')
        self.refresh_buttons()

    def stop_scan(self):
        self.worker.requestInterruption()
        self.stop_button.setEnabled(False)
        self.status.setText('正在结束检测，请稍候…')

    def selected_camera(self):
        from PyQt6.QtCore import Qt
        item = self.cameras.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def preview_camera(self):
        index = self.selected_camera()
        if index is not None and not self.worker.isRunning():
            CameraCheckDialog(dict(self.config, camera_index=index), self).exec()

    def select_camera(self):
        if self.worker.isRunning():
            return
        self.selected_index = self.selected_camera()
        if self.selected_index is not None:
            self.accept()

    def stop_worker(self):
        self.worker.requestInterruption()
        if not self.worker.wait(3000):
            self.status.setText('正在等待摄像头释放，请稍候…')
            return False
        return True

    def reject(self):
        if self.stop_worker():
            super().reject()

    def closeEvent(self, event):
        if self.stop_worker():
            event.accept()
        else:
            event.ignore()
