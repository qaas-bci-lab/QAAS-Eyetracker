"""Distribution and gaze replay for one or several processed experiments."""
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QImage, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QProgressBar,
                             QScrollArea, QSizePolicy, QSlider, QStackedWidget, QTabWidget,
                             QVBoxLayout, QWidget)

from ui_components import button, label
from window_chrome import install_chrome


class VisualTask(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = folders

    def run(self):
        total = len(self.folders)
        for index, folder in enumerate(self.folders):
            def progress(value, message):
                if total > 1:
                    message = f'{index + 1}/{total}  {message}'
                self.progress.emit((index * 100 + value) // total, message)

            try:
                from experiment_visuals import ensure_session_visuals
                self.done.emit(index, ensure_session_visuals(folder, on_progress=progress))
            except Exception as error:
                self.failed.emit(index, str(error))


class ImageView(QLabel):
    def __init__(self):
        super().__init__()
        self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setStyleSheet('background:#FFFFFF;border:1px solid #DDDDDD;border-radius:8px;')

    def load(self, path):
        self.original = QPixmap(str(path))
        self.fit()

    def load_video_frame(self, path):
        import cv2
        cap = cv2.VideoCapture(str(path))
        try:
            ok, frame = cap.read()
        finally:
            cap.release()
        if ok:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0],
                           QImage.Format.Format_RGB888).copy()
            self.original = QPixmap.fromImage(image)
            self.fit()

    def fit(self):
        if not self.original.isNull():
            self.setPixmap(self.original.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()


class ExperimentPanel(QObject):
    def __init__(self, folder, parent, *, comparison, record):
        super().__init__(parent)
        self.paths = {}
        self.plot = QWidget()
        plot_layout = QVBoxLayout(self.plot)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.distribution = ImageView()
        self.replay = QWidget()
        replay_layout = QVBoxLayout(self.replay)
        replay_layout.setContentsMargins(0, 8, 0, 0)
        segment = record.get('segment_index')
        count = record.get('segment_count')
        section = f'第 {segment} / {count} 段' if type(segment) is int and type(count) is int else ''
        if comparison or section:
            person = str(record.get('name') or '').strip()
            identifier = record.get('participant_id')
            identifier = '' if identifier is None else str(identifier).strip()
            title = '　'.join(part for part in (person, f'编号 {identifier}' if identifier else '', section) if part)
            timestamp = self.record_time(record.get('created'), folder)
            for layout in (plot_layout, replay_layout):
                header = QHBoxLayout()
                name = label(title or ('实验记录' if timestamp else folder.name), 'CardTitle')
                name.setTextFormat(Qt.TextFormat.PlainText)
                name.setWordWrap(True)
                name.setToolTip(str(folder))
                header.addWidget(name, 1)
                if timestamp:
                    recorded_at = label(timestamp, 'Muted')
                    recorded_at.setTextFormat(Qt.TextFormat.PlainText)
                    recorded_at.setToolTip(str(folder))
                    header.addWidget(recorded_at, alignment=Qt.AlignmentFlag.AlignVCenter)
                layout.addLayout(header)
        self.plot_notice = self.notice(plot_layout)
        self.replay_notice = self.notice(replay_layout)
        plot_layout.addWidget(self.distribution, 1)
        self.open_plot = button('打开散点图', lambda: self.open_file('distribution_path'))
        if comparison:
            plot_layout.addWidget(self.open_plot, 0, Qt.AlignmentFlag.AlignLeft)
        self.poster = ImageView()
        self.video = QVideoWidget()
        self.video.setMinimumSize(240, 180)
        self.video_stack = QStackedWidget()
        self.video_stack.setMinimumSize(240, 180)
        self.video_stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_stack.addWidget(self.poster)
        self.video_stack.addWidget(self.video)
        replay_layout.addWidget(self.video_stack, 1)
        timeline = QHBoxLayout()
        self.position = label('00:00 / 00:00')
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setAccessibleName('回放位置')
        timeline.addWidget(self.seek, 1)
        timeline.addWidget(self.position)
        replay_layout.addLayout(timeline)
        controls = QHBoxLayout()
        self.play = button('播放', self.toggle_play, primary=True)
        self.open_video = button('打开视频', lambda: self.open_file('video_path'))
        controls.addWidget(self.play)
        controls.addWidget(self.open_video)
        controls.addStretch()
        replay_layout.addLayout(controls)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.positionChanged.connect(self.position_changed)
        self.player.playbackStateChanged.connect(self.playback_changed)
        self.player.errorOccurred.connect(self.player_error)
        self.seek.sliderMoved.connect(self.player.setPosition)
        for control in (self.play, self.open_video, self.open_plot, self.seek):
            control.setEnabled(False)

    @staticmethod
    def record_time(value, folder):
        try:
            return datetime.fromisoformat(value or '').strftime('%Y-%m-%d %H:%M:%S')
        except (TypeError, ValueError):
            try:
                return datetime.strptime(folder.name[:15], '%Y%m%d_%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                return ''

    @staticmethod
    def notice(layout):
        notice = label('')
        notice.setTextFormat(Qt.TextFormat.PlainText)
        notice.setWordWrap(True)
        notice.hide()
        layout.addWidget(notice)
        return notice

    def show_result(self, paths):
        self.paths.update(paths)
        self.distribution.load(paths['distribution_path'])
        self.poster.load_video_frame(paths['video_path'])
        self.player.setSource(QUrl.fromLocalFile(str(paths['video_path'])))
        for control in (self.play, self.open_video, self.open_plot, self.seek):
            control.setEnabled(True)

    def show_error(self, message):
        for notice in (self.plot_notice, self.replay_notice):
            notice.setText(message.replace(chr(183), ' '))
            notice.show()

    def player_error(self, *_):
        if self.player.error() != QMediaPlayer.Error.NoError:
            self.replay_notice.setText('内置播放器无法播放，可点击“打开视频”查看')
            self.replay_notice.show()

    @staticmethod
    def clock(milliseconds):
        seconds = max(0, int(milliseconds) // 1000)
        return f'{seconds // 60:02d}:{seconds % 60:02d}'

    def duration_changed(self, duration):
        self.seek.setRange(0, max(0, duration))
        self.position_changed(self.player.position())

    def position_changed(self, position):
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.position.setText(f'{self.clock(position)} / {self.clock(self.player.duration())}')

    def playback_changed(self, state):
        self.play.setText('暂停' if state == QMediaPlayer.PlaybackState.PlayingState else '播放')

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.start_playback()

    def start_playback(self):
        if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
        self.video_stack.setCurrentWidget(self.video)
        self.player.play()

    def open_file(self, key):
        path = self.paths.get(key)
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())


class ExperimentViewer(QDialog):
    def __init__(self, folder, parent=None, *, records=None):
        super().__init__(parent)
        folders = [folder] if isinstance(folder, (str, Path)) else folder
        self.folders = tuple(dict.fromkeys(Path(path) for path in folders))
        if not self.folders:
            raise ValueError('请选择要查看的实验记录')
        self.folder = self.folders[0]
        self.task = None
        self.comparison = len(self.folders) > 1
        self.setWindowTitle('实验轨迹对比' if self.comparison else '实验轨迹')
        self.setWindowModality(Qt.WindowModality.WindowModal)
        width, height = (1040, 640) if self.comparison else (820, 560)
        available = self.screen().availableGeometry()
        self.resize(min(width, available.width() - 40), min(height, available.height() - 40))
        self.setMinimumSize(620, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        install_chrome(self, layout)
        records = records or {}
        self.panels = [ExperimentPanel(path, self, comparison=self.comparison, record=records.get(str(path), {}))
                       for path in self.folders]
        first = self.panels[0]
        self.paths, self.distribution, self.poster = first.paths, first.distribution, first.poster
        self.player, self.play, self.open_video, self.seek = first.player, first.play, first.open_video, first.seek
        self.open_plot = first.open_plot
        self.tabs = QTabWidget()
        self.tabs.addTab(self.gallery('plot'), '散点图')
        self.tabs.addTab(self.gallery('replay'), '注视点回放')
        self.tabs.currentChanged.connect(self.tab_changed)
        layout.addWidget(self.tabs, 1)
        self.status = label('正在准备轨迹')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        self.play_all = None
        if self.comparison:
            self.play_all = button('全部播放', self.toggle_all, primary=True)
            self.play_all.hide()
            self.play_all.setEnabled(False)
            for panel in self.panels:
                panel.player.playbackStateChanged.connect(self.update_play_all)
            bottom.addWidget(self.play_all)
        else:
            bottom.addWidget(self.open_plot)
        bottom.addStretch()
        self.back = button('返回', self.reject)
        bottom.addWidget(self.back)
        layout.addLayout(bottom)
        QTimer.singleShot(0, self.prepare)

    def gallery(self, attribute):
        if not self.comparison:
            return getattr(self.panels[0], attribute)
        area = QScrollArea()
        area.setWidgetResizable(True)
        contents = QWidget()
        grid = QGridLayout(contents)
        grid.setContentsMargins(0, 0, 2, 2)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(14)
        for index, panel in enumerate(self.panels):
            grid.addWidget(getattr(panel, attribute), index // 2, index % 2)
            grid.setRowStretch(index // 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        area.setWidget(contents)
        return area

    @property
    def busy(self):
        return self.task is not None and self.task.isRunning()

    def prepare(self):
        self.task = VisualTask(self.folders, self)
        self.task.progress.connect(self.on_progress)
        self.task.done.connect(self.show_result)
        self.task.failed.connect(self.show_error)
        self.task.finished.connect(self.prepared)
        self.back.setEnabled(False)
        self.task.start()

    def on_progress(self, value, message):
        self.progress.setValue(value)
        self.status.setText(message.replace(chr(183), ' '))

    def show_result(self, index, paths):
        self.panels[index].show_result(paths)

    def show_error(self, index, message):
        self.panels[index].show_error(message)

    def prepared(self):
        self.progress.hide()
        self.status.hide()
        self.back.setEnabled(True)
        if self.play_all is not None:
            self.play_all.setEnabled(any(panel.paths for panel in self.panels))

    def tab_changed(self, index):
        if index == 0:
            for panel in self.panels:
                panel.player.pause()
        if self.play_all is not None:
            self.play_all.setVisible(index == 1)

    def is_playing(self):
        return any(panel.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                   for panel in self.panels)

    def update_play_all(self, *_):
        self.play_all.setText('全部暂停' if self.is_playing() else '全部播放')

    def toggle_all(self):
        pause = self.is_playing()
        for panel in self.panels:
            if not panel.paths:
                continue
            if pause:
                panel.player.pause()
            else:
                panel.start_playback()

    def done(self, result):
        if self.busy:
            return
        for panel in self.panels:
            panel.stop()
        super().done(result)

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
            return
        for panel in self.panels:
            panel.stop()
        event.accept()
