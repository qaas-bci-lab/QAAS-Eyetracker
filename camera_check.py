"""Explicit camera access and live-frame check for the settings dialog."""
from __future__ import annotations

import time

import cv2
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from capture import open_working_camera
from app_paths import RESOURCE_ROOT


class CameraCheckWorker(QThread):
    frame_ready = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)

    def run(self):
        camera = None
        detector = None
        try:
            camera, frame = open_working_camera(self.config)
            try:
                import mediapipe as mp
                options = mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=mp.tasks.BaseOptions(model_asset_buffer=(RESOURCE_ROOT/'models/face_landmarker.task').read_bytes()),
                    running_mode=mp.tasks.vision.RunningMode.IMAGE, num_faces=1,
                    min_face_detection_confidence=.5, min_face_presence_confidence=.5)
                detector = mp.tasks.vision.FaceLandmarker.create_from_options(options)
                face_status = '正在检查人脸…'
            except Exception:
                face_status = '人脸检测器不可用，请检查模型文件。'
            last_detection = 0.
            misses = 0
            while not self.isInterruptionRequested():
                if frame is not None:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    height, width = rgb.shape[:2]
                    if detector is not None and time.monotonic() - last_detection >= .5:
                        try:
                            result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
                            face_status = '已检测到人脸' if result.face_landmarks else '未检测到人脸，请调整位置与光线'
                        except Exception:
                            face_status = '人脸检测失败，请检查模型与图像。'
                        last_detection = time.monotonic()
                    image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
                    self.frame_ready.emit(image, face_status)
                time.sleep(.08)
                ok, frame = camera.read()
                if not ok or frame is None or not frame.size:
                    misses += 1
                    frame = None
                    if misses >= 10:
                        raise RuntimeError('摄像头打开后持续读取失败，请检查设备或其他应用占用。')
                else:
                    misses = 0
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if camera is not None:
                camera.release()
            if detector is not None:
                detector.close()


class CameraCheckDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle('摄像头检查')
        self.resize(680, 520)
        layout = QVBoxLayout(self)
        self.status = QLabel('正在打开摄像头并读取画面…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.preview = QLabel('等待画面')
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 320)
        layout.addWidget(self.preview, 1)
        controls = QHBoxLayout()
        privacy = QPushButton('打开 Windows 相机权限设置')
        privacy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('ms-settings:privacy-webcam')))
        controls.addWidget(privacy)
        controls.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)
        self.worker = CameraCheckWorker(config, self)
        self.worker.frame_ready.connect(self.show_frame)
        self.worker.failed.connect(self.show_error)
        self.worker.start()

    def show_frame(self, image, face_status):
        self.status.setText(f'画面读取成功  {image.width()} × {image.height()}  {face_status}')
        pixmap = QPixmap.fromImage(image)
        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def show_error(self, message):
        self.status.setText(message)
        self.preview.clear()
        self.preview.setText('无法读取画面')

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
