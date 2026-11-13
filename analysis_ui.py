"""Mode-aware offline analysis; returning home preserves an active task."""
from datetime import datetime
from pathlib import Path
import json,math,traceback
from PyQt6.QtCore import Qt,QThread,pyqtSignal,QUrl
from PyQt6.QtGui import QPixmap,QDesktopServices,QImage
from PyQt6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
    QComboBox,QProgressBar,QScrollArea,QLabel,QFileDialog,QMessageBox,QSizePolicy,
    QStackedWidget,QTableWidget,QTableWidgetItem,QHeaderView)
from PyQt6.QtMultimedia import QMediaPlayer,QAudioOutput
from PyQt6.QtMultimediaWidgets import QVideoWidget
from ui_components import label,button,card,spin,MODEL_NAMES
from ui_symbols import icon
from window_chrome import install_chrome
from app_paths import DATA_ROOT
from coordinates import session_mode
ROOT=DATA_ROOT

class PlotLabel(QLabel):
    def __init__(self,text='—'):super().__init__(text);self.original=None
    def set_image(self,pix):self.original=pix;self.fit_image()
    def fit_image(self):
        if self.original is not None and not self.original.isNull():self.setPixmap(self.original.scaled(self.contentsRect().size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
    def resizeEvent(self,event):super().resizeEvent(event);self.fit_image()

class AnalysisTask(QThread):
    progress=pyqtSignal(int,str);done=pyqtSignal(object);failed=pyqtSignal(str)
    def __init__(self,folder,start_s=None,end_s=None,*,processing_config=None):
        super().__init__();self.folder=Path(folder);self.start_s=start_s;self.end_s=end_s
        self.processing_config = dict(processing_config or {})
    def run(self):
        try:
            from offline import infer_session
            from reporting import report
            metadata=json.loads((self.folder/'session.json').read_text(encoding='utf-8-sig'))
            experiment=session_mode(metadata)=='experiment'
            if experiment:
                from processor_service import completed_result
                completed=completed_result(self.folder)
                if completed is not None:
                    self.progress.emit(100,'分析完成');self.done.emit(completed);return
            import os
            from processing_hardware import plan_execution

            requested_threads = self.processing_config.get('cpu_threads', min(8, os.cpu_count() or 1))
            plan = plan_execution(self.processing_config.get('device', 'auto'), cpu_threads=requested_threads)
            device = plan['device']
            if device == 'cpu':
                threads = plan['threads_per_job']
                options = dict(batch_size=8, cpu_threads=threads)
                device_label = f'CPU {threads} 线程'
            else:
                import torch
                options = {}
                device_label = 'GPU ' + torch.cuda.get_device_name(device)
            infer_session(self.folder, progress=None, device=device, **options,
                          on_progress=lambda p, m: self.progress.emit(round(p * .70), f'{device_label}  {m}'))
            result=report(self.folder,start_s=self.start_s,end_s=self.end_s,compact_experiment=experiment,on_progress=lambda p,m:self.progress.emit(70+round(p*.30),m))
            self.progress.emit(100,'分析完成');self.done.emit(result)
        except Exception:self.failed.emit(traceback.format_exc())

class AnalysisWindow(QDialog):
    analysis_finished = pyqtSignal(str)

    def __init__(self,parent=None,initial=None,*,record_roots=(),record_mode=None,allowed_folders=None):
        super().__init__(parent);self.setWindowFlag(Qt.WindowType.Window,True);self.setWindowTitle('数据处理');self.resize(1080,760)
        self.record_mode, self.allowed_folders = self.normalize_context(record_mode, allowed_folders)
        self.processing_config = {}
        self.record_roots = tuple(Path(root) for root in record_roots if root)
        self.task=None;self.folder=None;self.result_dir=None;self.video_path=None;self.txt_path=None;self.participant_json_path=None;self._scatter_pix=None;self.mode='test';self.metadata={};self._complete_experiment=False
        self.analysis_available = False
        outer=QVBoxLayout(self);outer.setContentsMargins(28,20,28,22);outer.setSpacing(13)
        install_chrome(self,outer)
        head=QHBoxLayout();self.back=button('返回',self.return_home,quiet=True);head.addWidget(self.back);head.addStretch()
        self.open_folder=button('打开记录文件夹',self.open_record_folder)
        self.open_folder.setEnabled(False);head.addWidget(self.open_folder)
        self.open_txt=button('打开 TXT',self.external_txt);self.open_txt.setEnabled(False);head.addWidget(self.open_txt);outer.addLayout(head)
        self.open_participant=button('打开信息JSON',self.external_participant)
        self.open_participant.setEnabled(False);self.open_participant.hide();head.addWidget(self.open_participant)
        chooser=QHBoxLayout();self.records_label=label('记录');chooser.addWidget(self.records_label);self.records=QComboBox();self.records.setMinimumWidth(230);self.records.setAccessibleName('记录');chooser.addWidget(self.records,1)
        self.pick=button('选择其他记录',self.pick_record);chooser.addWidget(self.pick);outer.addLayout(chooser)
        provenance=QHBoxLayout();self.provenance={}
        self.record_state=label('未选中记录','SchemeBadge');provenance.addWidget(self.record_state)
        for key,title in [('mode','模式'),('model','模型'),('calibration','校准')]:
            provenance.addWidget(label(title));value=label('—');self.provenance[key]=value;provenance.addWidget(value,1)
        outer.addLayout(provenance)
        range_row=QHBoxLayout();range_row.addWidget(label('分析时间段'));self.start_time=spin(0,0,100000,' 秒',1);self.end_time=spin(0,0,100000,' 秒',1)
        range_row.addWidget(self.start_time);range_row.addWidget(label('至'));range_row.addWidget(self.end_time);range_row.addStretch()
        self.generate=button('开始分析',self.run_analysis,True);range_row.addWidget(self.generate);outer.addLayout(range_row)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.hide();outer.addWidget(self.progress)
        self.status=label('','',True);self.status.hide();outer.addWidget(self.status)
        scroll=QScrollArea();scroll.setWidgetResizable(True);outer.addWidget(scroll,1);body=QWidget();scroll.setWidget(body);content=QVBoxLayout(body);content.setContentsMargins(0,0,4,0);content.setSpacing(18)
        self.pending_card,pending_layout=card()
        self.pending_title=label('','CardTitle');pending_layout.addWidget(self.pending_title)
        self.pending_message=label('',None,True);pending_layout.addWidget(self.pending_message)
        self.pending_path=label('',None,True);self.pending_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pending_layout.addWidget(self.pending_path);content.addWidget(self.pending_card)
        self.coordinate_card,cl=card();cl.addWidget(label('坐标TXT','CardTitle'));self.coordinate_status=label('—');cl.addWidget(self.coordinate_status)
        self.coordinate_file=label('');self.coordinate_file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);self.coordinate_file.setWordWrap(True);cl.addWidget(self.coordinate_file);content.addWidget(self.coordinate_card)
        self.participant_file=label('');self.participant_file.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);self.participant_file.setWordWrap(True);cl.addWidget(self.participant_file)
        self.error_card,el=card();el.addWidget(label('误差结果','CardTitle'));metrics=QGridLayout();self.metrics={}
        definitions=[('mean','平均误差'),('angle','平均角度误差'),('ci','均值95%置信区间'),('valid','有效帧 / 采集帧'),('median','中位误差'),('rmse','RMSE'),('distance_p95','误差P95'),('rate','有效率'),('bias_x','X偏差'),('bias_y','Y偏差'),('angle_median','中位角度误差'),('angle_p95','角度误差P95')]
        for i,(key,title) in enumerate(definitions):
            col=QVBoxLayout();col.addWidget(label(title));value=label('—','Metric');value.setStyleSheet('font-size:23px;font-weight:600;');self.metrics[key]=value;col.addWidget(value);metrics.addLayout(col,i//4,i%4)
        self.metrics['p95']=self.metrics['angle'];el.addLayout(metrics);self.detail=label('');self.detail.hide();el.addWidget(self.detail);content.addWidget(self.error_card)
        self.point_card,pl=card();pl.addWidget(label('各测试点','CardTitle'));self.point_table=QTableWidget(0,7)
        self.point_table.setHorizontalHeaderLabels(['点','采集帧','有效帧','有效率','平均误差 cm','RMSE cm','平均角度 °']);self.point_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.point_table.verticalHeader().hide();self.point_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.point_table.setMinimumHeight(170);pl.addWidget(self.point_table);content.addWidget(self.point_card)
        lower=QHBoxLayout();lower.setSpacing(18);content.addLayout(lower,1)
        self.scatter_card,sl=card();sl.addWidget(label('预测点分布','CardTitle'));self.scatter=PlotLabel();self.scatter.setAlignment(Qt.AlignmentFlag.AlignCenter);self.scatter.setMinimumSize(320,230);self.scatter.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.scatter.setStyleSheet('background:#f5f5f5;border-radius:4px;');sl.addWidget(self.scatter,1);lower.addWidget(self.scatter_card,1)
        self.scatter.setCursor(Qt.CursorShape.PointingHandCursor);self.scatter.mousePressEvent=lambda event:self.enlarge_scatter();self.scatter.setFixedHeight(280)
        self.video_card,vl=card();vl.addWidget(label('注视点回放','CardTitle'));self.video=QVideoWidget();self.video.setMinimumSize(320,230)
        self.poster=PlotLabel();self.poster.setAlignment(Qt.AlignmentFlag.AlignCenter);self.poster.setStyleSheet('background:#151518;color:white;border-radius:10px;');self.poster.setMinimumSize(320,230);self.poster.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.video.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        self.video_stack=QStackedWidget();self.video_stack.setFixedHeight(250);self.video_stack.addWidget(self.poster);self.video_stack.addWidget(self.video);vl.addWidget(self.video_stack,1)
        self.player=QMediaPlayer(self);self.audio=QAudioOutput(self);self.audio.setMuted(True);self.player.setAudioOutput(self.audio);self.player.setVideoOutput(self.video)
        controls=QHBoxLayout();self.play=button('播放',self.toggle_play);self.play.setEnabled(False);controls.addWidget(self.play);self.open_video=button('打开视频',self.external_video);self.open_video.setEnabled(False);controls.addWidget(self.open_video);vl.addLayout(controls);lower.addWidget(self.video_card,1)
        self.player.playbackStateChanged.connect(self.update_play_button);self.player.errorOccurred.connect(lambda *_:self.set_status('内嵌播放不可用，可点击“打开视频”。'))
        self.records.currentIndexChanged.connect(self.select_record);self.refresh_context_title();self.populate(initial)
    def set_status(self,text):self.status.setText(text.replace(chr(183),' '));self.status.setVisible(bool(text))
    @staticmethod
    def normalize_context(record_mode, allowed_folders):
        if record_mode not in (None,'experiment','test'):
            raise ValueError('数据处理模式必须是实验或测试')
        folders = None if allowed_folders is None else frozenset(Path(path).resolve() for path in allowed_folders)
        return record_mode, folders

    def refresh_context_title(self):
        title = {'experiment':'实验数据处理','test':'测试数据处理'}.get(self.record_mode,'数据处理')
        self.setWindowTitle(title)
        record_title = {'experiment':'实验记录','test':'测试记录'}.get(self.record_mode,'记录')
        self.records_label.setText(record_title)
        self.records.setAccessibleName(record_title)
        # The person's history is the only source of records in a scoped view.
        self.pick.setVisible(self.allowed_folders is None)

    def set_record_context(self,record_mode=None,allowed_folders=None,record_roots=None):
        if self.busy:
            return False
        mode, folders = self.normalize_context(record_mode, allowed_folders)
        self.record_mode, self.allowed_folders = mode, folders
        if record_roots is not None:
            self.record_roots = tuple(Path(root) for root in record_roots if root)
        self.refresh_context_title()
        self.folder = None
        self.populate()
        return True

    def read_allowed_metadata(self,folder):
        folder = Path(folder).resolve()
        if self.allowed_folders is not None and folder not in self.allowed_folders:
            raise ValueError('该记录不属于当前人员')
        metadata = json.loads((folder/'session.json').read_text(encoding='utf-8-sig'))
        if not isinstance(metadata,dict):
            raise ValueError('记录信息格式不正确')
        if self.record_mode is not None and session_mode(metadata) != self.record_mode:
            raise ValueError('该记录不属于当前模式')
        return metadata
    def update_play_button(self,state):
        playing=state==QMediaPlayer.PlaybackState.PlayingState;self.play.setText('暂停' if playing else '播放');self.play.setIcon(icon('pause' if playing else 'play',size=20))
    @property
    def busy(self):return self.task is not None and self.task.isRunning()
    def return_home(self):
        self.player.pause();self.hide();parent=self.parentWidget()
        if parent is not None:parent.show();parent.raise_();parent.activateWindow()
    def start_automatic(self,folder):
        if self.busy:return False
        if not self.populate(folder) or not self.analysis_available:return False
        self.show();self.run_analysis();return True
    def populate(self,initial=None):
        if self.busy:
            return
        target = Path(initial).resolve() if initial else self.folder
        entries = {}
        candidates = set(self.allowed_folders or ())
        if self.allowed_folders is None:
            for root in (ROOT/'sessions', *self.record_roots):
                try:
                    candidates.update(path.parent.resolve() for path in Path(root).glob('*/session.json'))
                except (OSError,RuntimeError):
                    continue
        if target is not None:
            candidates.add(target)
        for folder in candidates:
            try:
                self.read_allowed_metadata(folder)
                entries[folder] = self.record_entry(folder)
            except (OSError,ValueError,TypeError,RuntimeError):
                continue

        self.records.blockSignals(True)
        try:
            self.records.clear()
            for folder, (_, title) in sorted(entries.items(), key=lambda item: item[1], reverse=True):
                self.records.addItem(title, str(folder))
            if target is not None:
                index = self.records.findData(str(target))
                # An unavailable or out-of-scope request must never select a
                # different person's/latest recording in its place.
                self.records.setCurrentIndex(index)
        finally:
            self.records.blockSignals(False)
        self.select_record(self.records.currentIndex())
        return self.folder is not None and (initial is None or self.folder == target)

    @staticmethod
    def record_entry(folder):
        meta_path = folder/'session.json'
        metadata = json.loads(meta_path.read_text(encoding='utf-8-sig'))
        if not isinstance(metadata, dict):
            raise ValueError('记录信息格式不正确。')
        created = str(metadata.get('created_local') or '')
        try:
            date = datetime.fromisoformat(created)
        except ValueError:
            try:
                date = datetime.strptime(folder.name, '%Y%m%d_%H%M%S_%f')
            except ValueError:
                date = datetime.fromtimestamp(meta_path.stat().st_mtime)
        scheme = metadata.get('scheme')
        title = str((scheme.get('name') if isinstance(scheme,dict) else '') or '记录').replace(chr(183),' ')
        mode = '实验' if session_mode(metadata) == 'experiment' else '测试'
        return date.timestamp(), f'{date:%Y-%m-%d %H:%M:%S}  {mode}  {title}'

    def pick_record(self):
        if self.busy:
            return
        browse_root = next((Path(root) for root in self.record_roots if Path(root).is_dir()), ROOT/'sessions')
        chosen = QFileDialog.getExistingDirectory(self,'选择单次记录的文件夹',str(browse_root))
        if not chosen:
            return
        folder = Path(chosen).resolve()
        try:
            self.read_allowed_metadata(folder)
            _, title = self.record_entry(folder)
        except (OSError, ValueError,TypeError,RuntimeError) as error:
            message = str(error) if isinstance(error,ValueError) and not isinstance(error,json.JSONDecodeError) else '请选择完整的单次记录文件夹'
            QMessageBox.information(self,'选择记录',message)
            return
        path = str(folder)
        index = self.records.findData(path)
        if index < 0:
            self.records.insertItem(0, title, path)
            index = 0
        self.records.setCurrentIndex(index)

    def open_record_folder(self):
        if self.folder is not None and self.folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder)))

    def show_pending(self, state, title, message):
        self.record_state.setText(state)
        self.pending_title.setText(title)
        self.pending_message.setText(message.replace(chr(183),' '))
        self.pending_message.setVisible(bool(message))
        self.pending_path.setText(str(self.folder) if self.folder is not None else '')
        self.pending_card.show()
        for widget in (self.coordinate_card,self.error_card,self.point_card,self.scatter_card,self.video_card):
            widget.hide()

    def set_mode(self,mode):
        self.mode=mode;testing=mode=='test'
        for widget in (self.error_card,self.point_card):widget.setVisible(testing)
        self.scatter_card.show();self.video_card.show()
        self.coordinate_card.setVisible(not testing);self.provenance['mode'].setText('测试模式' if testing else '实验模式')
        self.open_participant.setVisible(not testing)
    def select_record(self,index):
        if self.busy:
            return
        self.clear_result()
        self.folder=Path(self.records.itemData(index)) if index>=0 else None
        self.metadata={}
        self._complete_experiment=False
        self.analysis_available=False
        self.set_controls(True)
        self.generate.setText('开始分析')
        for value in self.provenance.values():
            value.setText('—')
        self.open_folder.setEnabled(self.folder is not None and self.folder.is_dir())
        if self.folder is None:
            self.show_pending('未选中记录','暂无记录','')
            return
        try:
            self.metadata=self.read_allowed_metadata(self.folder)
        except (OSError,ValueError,TypeError):
            self.show_pending('记录不完整','记录无法读取','请确认文件夹中有完整的 session.json。')
            return
        meta=self.metadata;self.set_mode(session_mode(meta));self.provenance['model'].setText(MODEL_NAMES.get(meta.get('model_id'),meta.get('model_id','—')))
        try:
            calibration=meta.get('calibration') or {};count=int(calibration.get('target_count',0))
            duration=float(meta.get('actual_duration_s',meta.get('planned_duration_s',0)))
            if not math.isfinite(duration):raise ValueError('invalid duration')
        except (ValueError,TypeError,AttributeError):
            self.show_pending('记录不完整','记录无法读取','校准信息或采集时长无效')
            return
        self.provenance['calibration'].setText(f'{count}点' if count else '0点')
        self.start_time.setMaximum(max(duration,0));self.end_time.setMaximum(max(duration,0));self.start_time.setValue(0);self.end_time.setValue(duration);self.full_end_display=self.end_time.value()
        from record_layout import has_capture_data,read_result
        try:
            has_capture = has_capture_data(self.folder,meta)
        except (OSError,ValueError,TypeError,UnicodeError):
            has_capture = False
        has_video = (self.folder/'camera.mp4').is_file()
        has_coordinates = False
        if has_capture and not has_video:
            try:
                with (self.folder/'gaze.csv').open(encoding='utf-8-sig') as source:
                    source.readline()
                    has_coordinates = bool(source.readline().strip())
            except (OSError, UnicodeError):
                pass
        self.analysis_available = duration > 0 and has_capture and (has_video or has_coordinates)
        self.set_controls(True)
        if self.mode=='experiment':
            from processor_service import completed_result
            completed=completed_result(self.folder)
            if completed is not None:
                self._complete_experiment=True
                self.analysis_available=False
                self.show_result(completed)
                self.set_controls(True)
                return
            try:
                stored=read_result(self.folder)
                if isinstance(stored,dict) and (stored.get('mode')=='experiment'
                                                or stored.get('txt_path') or stored.get('output_dir')):
                    self.show_result(stored|{'output_dir':str(self.folder)})
                    return
            except (ValueError,OSError,TypeError):
                pass
        else:
            try:
                stored=json.loads((self.folder/'report.json').read_text(encoding='utf-8-sig'))
                self.show_result(stored|{'output_dir':str(self.folder)})
                return
            except (ValueError,OSError,TypeError):
                pass
        if self.analysis_available:
            self.show_pending('待处理','等待分析','')
        else:
            self.show_pending('记录不完整','暂时无法分析','缺少有效的采集时段、原始视频或采集时间戳。')
    def clear_result(self):
        self.progress.hide()
        self.player.stop();self.player.setSource(QUrl());self.play.setEnabled(False);self.open_video.setEnabled(False);self.open_txt.setEnabled(False);self.txt_path=None;self.video_path=None;self.video_stack.setCurrentWidget(self.poster);self.poster.original=None;self.poster.clear();self.poster.setText('—')
        for value in self.metrics.values():value.setText('—')
        self.scatter.original=None;self.scatter.clear();self.scatter.setText('—');self.progress.setValue(0);self._scatter_pix=None;self.point_table.setRowCount(0);self.coordinate_status.setText('—');self.coordinate_file.setText('');self.participant_file.setText('');self.participant_json_path=None;self.open_participant.setEnabled(False);self.set_status('')
    def run_analysis(self):
        if self.busy or not self.analysis_available:return
        try:
            self.read_allowed_metadata(self.folder)
        except (OSError,ValueError,TypeError,RuntimeError) as error:
            self.analysis_available=False;self.set_controls(True)
            self.analysis_error(str(error));return
        start=self.start_time.value();end=self.end_time.value()
        if end<=start:QMessageBox.information(self,'分析时间段','结束时间应大于开始时间。');return
        self.clear_result();self.set_controls(False);actual_end=None if end==self.full_end_display else end;actual_start=None if start==0 else start
        self.show_pending('处理中','正在分析','')
        self.task = AnalysisTask(self.folder, actual_start, actual_end,
                                 processing_config=self.processing_config)
        self.task.progress.connect(self.on_progress)
        self.task.done.connect(self.analysis_succeeded)
        self.task.failed.connect(self.analysis_error)
        self.task.finished.connect(lambda: self.set_controls(True))
        self.task.start()
    def analysis_succeeded(self,stats):
        if stats.get('mode')=='experiment':
            from processor_service import completed_result
            self._complete_experiment=completed_result(self.folder) is not None
            if self._complete_experiment:self.analysis_available=False
        self.show_result(stats)
        self.analysis_finished.emit(str(self.folder))
    def set_controls(self,enabled):
        for widget in (self.records,self.pick):widget.setEnabled(enabled)
        for widget in (self.generate,self.start_time,self.end_time):widget.setEnabled(enabled and self.analysis_available)
    def on_progress(self,percent,message):self.progress.show();self.progress.setValue(percent);self.set_status(f'{percent}%  {message.replace(chr(183)," ")}')
    def show_result(self,stats):
        self.pending_card.hide();self.record_state.setText('已分析');self.generate.setText('已完成' if self._complete_experiment else '重新分析')
        self.set_mode(stats.get('mode',session_mode(self.metadata)));self.result_dir=Path(stats.get('output_dir',self.folder));self.progress.setValue(100);self.txt_path=Path(stats.get('txt_path') or self.result_dir/'gaze_coordinates.txt')
        if not self.txt_path.is_absolute():self.txt_path=self.result_dir/self.txt_path
        self.open_txt.setEnabled(self.txt_path.is_file())
        if self.mode=='experiment':
            self.coordinate_status.setText(f"已保存　{stats.get('export_rows',stats.get('prediction_rows',0)):,} 行"
                                           + (f"　{stats['sample_hz']} Hz" if stats.get('sample_hz') else ''))
            self.coordinate_file.setText(str(self.txt_path))
            self.participant_json_path=self.folder/'session.json'
            self.open_participant.setEnabled(self.participant_json_path.is_file())
            self.participant_file.setText(str(self.participant_json_path))
            reasons=stats.get('invalid_reasons') or {}
            if stats.get('prediction_rows',0) and not stats.get('valid_predictions',0):
                details='、'.join(f'{reason} {count}帧' for reason,count in sorted(reasons.items(),key=lambda item:item[1],reverse=True)[:3])
                self.set_status('没有有效注视预测。'+(details or '请检查摄像头画面和人脸位置。'))
            else:
                self.set_status('')
            self.load_visuals()
            return
        def fmt(value,unit=''):return f'{value:.2f}{unit}' if value is not None else '—'
        for key,field,unit in [('mean','mean_error_cm',' cm'),('angle','mean_angular_error_deg','°'),('median','median_error_cm',' cm'),('rmse','rmse_cm',' cm'),('distance_p95','p95_cm',' cm'),('bias_x','signed_bias_x_cm',' cm'),('bias_y','signed_bias_y_cm',' cm'),('angle_median','median_angular_error_deg','°'),('angle_p95','p95_angular_error_deg','°')]:self.metrics[key].setText(fmt(stats.get(field),unit))
        ci=stats.get('mean_error_95ci_cm');self.metrics['ci'].setText(f'{ci[0]:.2f}–{ci[1]:.2f} cm' if ci else '—');self.metrics['valid'].setText(f"{stats.get('valid_predictions',0):,} / {stats.get('capture_frames',0):,}");self.metrics['rate'].setText(fmt(stats.get('valid_fraction')*100 if stats.get('valid_fraction') is not None else None,'%'))
        points=stats.get('per_point',[]);self.point_table.setRowCount(len(points))
        for r,point in enumerate(points):
            point_id=point.get('target_id','—')
            if str(point_id).lstrip('-').isdigit():point_id=int(point_id)+1
            values=[point_id,point.get('capture_frames',0),point.get('valid_predictions',0),fmt((point.get('valid_fraction') or 0)*100,'%'),fmt(point.get('mean_error_cm')),fmt(point.get('rmse_cm')),fmt(point.get('mean_angular_error_deg'))]
            for c,value in enumerate(values):self.point_table.setItem(r,c,QTableWidgetItem(str(value)))
        self.load_visuals()
        self.set_status('')
    def load_visuals(self):
        self._scatter_pix=QPixmap(str(self.result_dir/'distribution.png'));self.resize_scatter();path=self.result_dir/'gaze_replay.mp4'
        if path.exists():
            self.video_path=path;self.player.setSource(QUrl.fromLocalFile(str(path)));self.play.setEnabled(True);self.open_video.setEnabled(True)
            import cv2
            cap=cv2.VideoCapture(str(path));ok,frame=cap.read();cap.release()
            if ok:
                rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB);image=QImage(rgb.data,rgb.shape[1],rgb.shape[0],rgb.strides[0],QImage.Format.Format_RGB888).copy();self.poster.set_image(QPixmap.fromImage(image))
            self.video_stack.setCurrentWidget(self.poster)
    def external_txt(self):
        if self.txt_path and self.txt_path.is_file():QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.txt_path)))
    def external_participant(self):
        if self.participant_json_path and self.participant_json_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.participant_json_path)))
    def resize_scatter(self):
        if getattr(self,'_scatter_pix',None) is not None and not self._scatter_pix.isNull():self.scatter.set_image(self._scatter_pix)
    def enlarge_scatter(self):
        if self._scatter_pix is None or self._scatter_pix.isNull():return
        dialog=QDialog(self);dialog.setWindowTitle('预测散点图');dialog.resize(1140,760);layout=QVBoxLayout(dialog);install_chrome(dialog,layout);image=QLabel();image.setAlignment(Qt.AlignmentFlag.AlignCenter);image.setPixmap(self._scatter_pix.scaled(1100,680,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation));layout.addWidget(image,1);layout.addWidget(button('返回',dialog.accept));dialog.exec()
    def resizeEvent(self,e):super().resizeEvent(e);self.resize_scatter()
    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState:self.player.pause()
        else:self.video_stack.setCurrentWidget(self.video);self.player.play()
    def external_video(self):
        if self.video_path:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.video_path)))
    def analysis_error(self,text):
        summary = text.strip().splitlines()[-1] if text.strip() else '无法生成分析结果。'
        self.show_pending('分析未完成','处理未完成',summary)
        self.set_status('')
        QMessageBox.warning(self,'分析未完成',summary)
    def closeEvent(self,e):
        self.player.pause();parent=self.parentWidget()
        if parent is not None:parent.show();parent.raise_();parent.activateWindow()
        # Main retains this dialog; closing only hides it, even with a running task.
        e.accept()
