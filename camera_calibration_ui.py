"""User-triggered camera intrinsics wizard. Independent of gaze/model inference."""
from __future__ import annotations
import json,threading,time,traceback
from pathlib import Path
from datetime import datetime
import cv2,numpy as np
from PyQt6.QtCore import Qt,QThread,pyqtSignal,QByteArray,QRectF
from PyQt6.QtGui import QImage,QPixmap,QPainter,QPageSize,QPageLayout
from PyQt6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QComboBox,QSpinBox,QCheckBox,
    QDoubleSpinBox,QPushButton,QProgressBar,QPlainTextEdit,QMessageBox,QFileDialog,QScrollArea)
from app_paths import DATA_ROOT
from camera_calibration import BoardSpec,Calibrator,detect_board,calibrate_views
from calibration_board import board_svg,save_board,save_electronic_board

class BoardCamera(QThread):
    frame=pyqtSignal(object,str);collected=pyqtSignal(int,str);failed=pyqtSignal(str)
    def __init__(self,index,size,spec,calibrator):
        super().__init__();self.index=index;self.size=size;self.spec=spec;self.calibrator=calibrator
        self.running=True;self.requested=threading.Event();self.saved_frames=[]
    def stop(self):self.running=False
    def run(self):
        cap=None
        try:
            backend=cv2.CAP_DSHOW if __import__('sys').platform=='win32' else cv2.CAP_ANY
            cap=cv2.VideoCapture(self.index,backend)
            if not cap.isOpened() and __import__('sys').platform=='win32':
                cap.release();cap=cv2.VideoCapture(self.index,cv2.CAP_MSMF)
            if not cap.isOpened():raise RuntimeError('无法打开摄像头，请检查编号、系统权限和其他应用占用。')
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,self.size[0]);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,self.size[1]);cap.set(cv2.CAP_PROP_FPS,30)
            last_detect=0;corners=None;missing=0
            while self.running:
                ok,image=cap.read()
                if not ok:
                    missing+=1
                    if missing>=30:raise RuntimeError('摄像头持续读取失败，请重新连接后重试。')
                    time.sleep(.02);continue
                missing=0;now=time.perf_counter();h,w=image.shape[:2]
                if self.calibrator.resolution is not None and tuple(self.calibrator.resolution)!=(w,h):
                    raise RuntimeError('采集中图像分辨率改变，请清空样本后重新标定。')
                if now-last_detect<.20:
                    continue
                last_detect=now
                corners=detect_board(image,self.spec)
                message=f'实际图像 {w}×{h}  '+('已检测到完整棋盘，可采集' if corners is not None else '未找到完整棋盘，请调整位置、光照和清晰度')
                view=image.copy()
                if corners is not None:cv2.drawChessboardCorners(view,self.spec.pattern_size,corners,True)
                self.frame.emit(view,message)
                if self.requested.is_set():
                    self.requested.clear()
                    if corners is None:
                        self.collected.emit(len(self.calibrator.views),'本张未采集：需要完整清晰的棋盘。');continue
                    if self.calibrator.add_view(corners,(w,h)):
                        self.saved_frames.append(image.copy())
                        self.collected.emit(len(self.calibrator.views),'已采集。请把棋盘移向其他区域，并向左右或上下倾斜。')
                    else:self.collected.emit(len(self.calibrator.views),'本张与已有姿态太相似，请改变位置、距离和倾斜角度。')
        except Exception as error:self.failed.emit(str(error))
        finally:
            if cap is not None:cap.release()

class CalibrationTask(QThread):
    progress=pyqtSignal(int,str);done=pyqtSignal(object);failed=pyqtSignal(str)
    def __init__(self,points,resolution,spec):
        super().__init__();self.points=[p.copy() for p in points];self.resolution=tuple(resolution);self.spec=spec
    def run(self):
        try:self.done.emit(calibrate_views(self.points,self.resolution,self.spec,on_progress=self.progress.emit))
        except Exception as error:self.failed.emit(str(error))

class CameraCalibrationDialog(QDialog):
    def __init__(self,camera_index=0,resolution=(1280,720),parent=None,data_root=None):
        super().__init__(parent);self.setWindowTitle('相机内参标定');self.resize(1100,820)
        self.data_root=Path(data_root) if data_root else DATA_ROOT
        self.camera_thread=None;self.task=None;self.intrinsics_result=None;self.result_folder=None
        self.frames=[];self.spec=BoardSpec();self.calibrator=Calibrator(self.spec)
        outer=QVBoxLayout(self);outer.setContentsMargins(24,20,24,20)
        from window_chrome import install_chrome
        install_chrome(self,outer)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1);body=QWidget();scroll.setWidget(body);layout=QVBoxLayout(body)
        top=QHBoxLayout();layout.addLayout(top);form=QFormLayout();top.addLayout(form,1)
        self.index=QSpinBox();self.index.setRange(0,20);self.index.setValue(camera_index)
        self.resolution=QComboBox()
        sizes=[(640,480),(1280,720),(1920,1080)]
        if tuple(resolution) not in sizes:sizes.append(tuple(resolution))
        for w,h in sizes:self.resolution.addItem(f'{w} × {h}',(w,h))
        self.resolution.setCurrentIndex(sizes.index(tuple(resolution)))
        self.columns=QSpinBox();self.columns.setRange(3,14);self.columns.setValue(9)
        self.rows=QSpinBox();self.rows.setRange(3,12);self.rows.setValue(6)
        self.square=QDoubleSpinBox();self.square.setRange(5,60);self.square.setDecimals(2);self.square.setSuffix(' mm');self.square.setValue(20)
        self.digital=QCheckBox('电子棋盘模式：无需量格子尺寸');self.digital.setChecked(True);self.square.setEnabled(False)
        self.digital.toggled.connect(lambda checked:self.square.setEnabled(not checked))
        form.addRow('',self.digital)
        for text,control in [('摄像头编号',self.index),('请求分辨率',self.resolution),('横向内角点数量',self.columns),('纵向内角点数量',self.rows),('实际格子边长',self.square)]:form.addRow(text,control)
        printing=QHBoxLayout();layout.addLayout(printing)
        self.export_board=QPushButton('导出电子棋盘格（PNG）');self.export_board.clicked.connect(self.export_electronic_pattern);printing.addWidget(self.export_board);printing.addStretch()
        self.print_board=QPushButton('打印棋盘格（可选）');self.print_board.clicked.connect(self.print_pattern);printing.addWidget(self.print_board)
        self.preview=QLabel('点击“打开摄像头”，此时才开始预览。');self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480,280);self.preview.setStyleSheet('background:#1a1a1a;color:white;border-radius:9px;');layout.addWidget(self.preview)
        self.status=QLabel('未打开摄像头。请先准备好棋盘格。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.progress=QProgressBar();self.progress.setRange(0,15);self.progress.setValue(0);self.progress.setFormat('有效姿态 %v / 至少15');layout.addWidget(self.progress)
        controls=QHBoxLayout();layout.addLayout(controls)
        self.open_button=QPushButton('打开摄像头');self.open_button.clicked.connect(self.start_camera);controls.addWidget(self.open_button)
        self.capture_button=QPushButton('采集当前棋盘');self.capture_button.clicked.connect(self.take_sample);self.capture_button.setEnabled(False);controls.addWidget(self.capture_button)
        self.stop_button=QPushButton('停止预览');self.stop_button.clicked.connect(self.stop_camera);self.stop_button.setEnabled(False);controls.addWidget(self.stop_button)
        self.clear_button=QPushButton('清空样本');self.clear_button.clicked.connect(self.clear_samples);controls.addWidget(self.clear_button)
        self.solve_button=QPushButton('计算内参');self.solve_button.clicked.connect(self.solve);self.solve_button.setEnabled(False);controls.addWidget(self.solve_button)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMinimumHeight(140);layout.addWidget(self.details)
        foot=QHBoxLayout();outer.addLayout(foot);self.close_button=QPushButton('返回');self.close_button.clicked.connect(self.reject);foot.addWidget(self.close_button);foot.addStretch()
        self.use_button=QPushButton('保存并采用内参');self.use_button.setProperty('primary',True);self.use_button.setEnabled(False);self.use_button.clicked.connect(self.save_and_use);foot.addWidget(self.use_button)
    def current_spec(self):return BoardSpec(columns=self.columns.value(),rows=self.rows.value(),square_mm=1. if self.digital.isChecked() else self.square.value())
    def lock_profile(self,locked):
        for item in [self.index,self.resolution,self.columns,self.rows,self.digital]:item.setEnabled(not locked)
        self.square.setEnabled(not locked and not self.digital.isChecked())
    def export_electronic_pattern(self):
        try:
            path,_=QFileDialog.getSaveFileName(self,'保存电子棋盘格',str(self.data_root/'camera_chessboard.png'),'PNG (*.png)')
            if path:
                save_electronic_board(path,self.current_spec())
                self.status.setText('已导出PNG。传到手机或平板后完整显示，保持正方形和白边；用前置摄像头从不同角度拍摄。')
        except Exception as error:QMessageBox.warning(self,'电子棋盘',str(error))
    def export_pattern(self):
        try:
            spec=self.current_spec();board_svg(spec)
            path,_=QFileDialog.getSaveFileName(self,'保存100%比例打印棋盘格',str(self.data_root/'camera_chessboard_A4.svg'),'SVG (*.svg)')
            if path:save_board(path,spec);self.status.setText('已导出。打印选择A4横向、实际大小100%，打印后用尺量边长再填写上方数值。')
        except Exception as error:QMessageBox.warning(self,'棋盘格',str(error))
    def print_pattern(self):
        try:
            from PyQt6.QtSvg import QSvgRenderer
            from PyQt6.QtPrintSupport import QPrinter,QPrintDialog
            print_spec=BoardSpec(columns=self.columns.value(),rows=self.rows.value(),square_mm=self.square.value())
            svg=board_svg(print_spec);printer=QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4));printer.setPageOrientation(QPageLayout.Orientation.Landscape);printer.setFullPage(True)
            dialog=QPrintDialog(printer,self)
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            painter=QPainter(printer)
            if not painter.isActive():raise ValueError('无法开始打印')
            try:
                renderer=QSvgRenderer(QByteArray(svg.encode('utf8')))
                # Explicit physical scaling avoids silently fitting the checkerboard to a different paper size.
                factor=printer.resolution()/25.4
                renderer.render(painter,QRectF(0,0,297*factor,210*factor))
            finally:painter.end()
            self.status.setText('打印完成后请量100mm标尺与格子边长；纸张缩放不符时，以实测边长填写。')
        except Exception as error:QMessageBox.warning(self,'打印失败',str(error))
    def start_camera(self):
        if self.camera_thread is not None and self.camera_thread.isRunning():return
        if self.task is not None and self.task.isRunning():return
        if self.calibrator.views:
            if self.current_spec()!=self.spec:
                QMessageBox.warning(self,'参数改变','请先清空样本再改变棋盘参数。');return
        else:self.spec=self.current_spec();self.calibrator=Calibrator(self.spec)
        self.intrinsics_result=None;self.use_button.setEnabled(False);self.lock_profile(True);self.open_button.setEnabled(False)
        self.camera_thread=BoardCamera(self.index.value(),self.resolution.currentData(),self.spec,self.calibrator)
        self.camera_thread.saved_frames=[f.copy() for f in self.frames]
        self.camera_thread.frame.connect(self.show_frame);self.camera_thread.collected.connect(self.sample_added);self.camera_thread.failed.connect(self.camera_failed)
        self.camera_thread.finished.connect(self.camera_stopped);self.capture_button.setEnabled(True);self.stop_button.setEnabled(True)
        self.status.setText('正在打开摄像头…');self.camera_thread.start()
    def show_frame(self,bgr,message):
        rgb=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)
        im=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format.Format_RGB888).copy()
        self.preview.setPixmap(QPixmap.fromImage(im).scaled(820,360,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        self.status.setText(message)
    def take_sample(self):
        if self.camera_thread is not None and self.camera_thread.isRunning():self.camera_thread.requested.set()
    def sample_added(self,count,message):
        self.progress.setValue(min(count,15));self.progress.setFormat(f'有效姿态 {count} / 至少15');self.status.setText(message)
        self.solve_button.setEnabled(count>=15)
    def camera_failed(self,message):
        self.status.setText(message);QMessageBox.warning(self,'摄像头',message)
    def camera_stopped(self):
        if self.camera_thread is not None and self.camera_thread.isRunning():return
        if self.camera_thread is not None:
            self.frames=[f.copy() for f in self.camera_thread.saved_frames]
            self.camera_thread=None
        if self.task is not None and self.task.isRunning():return
        self.capture_button.setEnabled(False);self.stop_button.setEnabled(False);self.open_button.setEnabled(True)
        self.lock_profile(bool(self.calibrator.views))
    def stop_camera(self):
        if self.camera_thread is not None and self.camera_thread.isRunning():
            self.camera_thread.stop()
            if not self.camera_thread.wait(5000):
                self.status.setText('摄像头正在退出，请稍候。');return False
        self.camera_stopped();return True
    def clear_samples(self):
        if not self.stop_camera():return
        self.intrinsics_result=None;self.frames=[];self.spec=self.current_spec();self.calibrator=Calibrator(self.spec)
        self.lock_profile(False);self.use_button.setEnabled(False);self.solve_button.setEnabled(False)
        self.progress.setRange(0,15);self.progress.setValue(0);self.progress.setFormat('有效姿态 %v / 至少15');self.details.clear();self.status.setText('样本已清空，请重新采集不同姿态。')
    def solve(self):
        if not self.stop_camera():return
        if len(self.calibrator.views)<15:return
        if self.task is not None and self.task.isRunning():return
        self.progress.setRange(0,100);self.progress.setFormat('内参计算 %p%');self.progress.setValue(0)
        for button in [self.solve_button,self.open_button,self.clear_button,self.use_button,self.close_button]:button.setEnabled(False)
        self.task=CalibrationTask(self.calibrator.views,self.calibrator.resolution,self.spec)
        self.task.progress.connect(self.fit_progress);self.task.done.connect(self.fit_done);self.task.failed.connect(self.fit_failed)
        self.status.setText('正在计算内参与留出验证，摄像头已关闭…');self.task.start()
    def fit_progress(self,percent,message):self.progress.setValue(percent);self.status.setText(message)
    def fit_done(self,result):
        self.intrinsics_result=dict(result,camera_index=self.index.value(),created_local=datetime.now().astimezone().isoformat())
        self.intrinsics_result['board']=dict(result['board'])
        self.intrinsics_result['board']['display_type']='electronic' if self.digital.isChecked() else 'physical_board'
        if self.digital.isChecked():
            self.intrinsics_result['board']['square_mm']=None
            self.intrinsics_result['board']['square_size_units']=1.
            self.intrinsics_result['board']['physical_size_measured']=False
            self.intrinsics_result['board']['note']='One arbitrary unit per square; intrinsics do not require absolute board size. Board pose translations are not metric.'
        self.progress.setValue(100)
        k=np.asarray(result['camera_matrix']);warnings=result.get('quality',{}).get('warnings',[])
        summary=(f"焦距 fx={k[0,0]:.2f}px，fy={k[1,1]:.2f}px；主点 cx={k[0,2]:.2f}px，cy={k[1,2]:.2f}px\n"
                 f"全样本重投影 RMS={result.get('rms_px',0):.3f}px；留出视图重投影 RMS={result.get('holdout_rms_px',0):.3f}px\n"
                 +'\n'.join('注意：'+str(w) for w in warnings)+'\n\n详细参数：\n')
        self.details.setPlainText(summary+json.dumps(self.intrinsics_result,ensure_ascii=False,indent=2))
        self.use_button.setEnabled(True);self.solve_button.setEnabled(True);self.clear_button.setEnabled(True);self.close_button.setEnabled(True);self.open_button.setEnabled(True)
        self.status.setText('计算完成。查看下方quality警告与留出误差；低重投影误差不等于眼动误差。保存后还需在设置页点击“保存设置”。')
    def fit_failed(self,message):
        self.status.setText(message);self.details.setPlainText(message);self.solve_button.setEnabled(True);self.clear_button.setEnabled(True);self.close_button.setEnabled(True);self.open_button.setEnabled(True)
        QMessageBox.warning(self,'标定未通过',message)
    def save_and_use(self):
        if self.intrinsics_result is None:return
        try:
            if len(self.frames)!=len(self.calibrator.views):
                raise ValueError('原始图像数量与角点数量不一致，请重新采集标定样本。')
            if self.intrinsics_result['view_count']!=len(self.frames):
                raise ValueError('计算后样本数量发生变化，请重新计算内参。')
            expected=tuple(self.intrinsics_result['intrinsics_resolution'])
            if any((im.shape[1],im.shape[0])!=expected for im in self.frames):
                raise ValueError('原始图像分辨率与标定结果不一致，请重新采集。')
            folder=self.data_root/'calibrations'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');folder.mkdir(parents=True,exist_ok=False)
            # Save samples and detected points so a calibration can be audited or recalculated.
            for i,image in enumerate(self.frames):
                ok,encoded=cv2.imencode('.png',image)
                if not ok:raise ValueError('无法编码标定样本图片')
                encoded.tofile(str(folder/f'view_{i+1:03d}.png'))
            np.savez_compressed(folder/'detected_corners.npz',**{f'view_{i+1:03d}':v for i,v in enumerate(self.calibrator.views)})
            path=folder/'camera_intrinsics.json';path.write_text(json.dumps(self.intrinsics_result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
            self.result_folder=folder;self.accept()
        except Exception as error:QMessageBox.warning(self,'保存失败',str(error))
    def reject(self):
        if self.task is not None and self.task.isRunning():self.status.setText('内参计算尚未结束，请稍候。');return
        if not self.stop_camera():return
        super().reject()
    def closeEvent(self,event):
        if self.task is not None and self.task.isRunning():event.ignore();self.status.setText('请等待内参计算完成后关闭。');return
        if not self.stop_camera():event.ignore();return
        event.accept()
