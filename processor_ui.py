"""Experiment RGB batch processing with explicit selection and visible file mappings."""
from pathlib import Path
import json
import threading
import os

from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLineEdit,
    QLabel, QMainWindow, QMessageBox, QProgressBar, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem,
    QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from app_paths import DATA_ROOT, RESOURCE_ROOT
from processor_art import ProcessorIllustration, processor_icon
from processor_service import export_mapping, process_batch, scan_sessions, segment_label
from processor_theme import choose_source_directory, install_processor_theme
from ui_components import button, configure_system_font, label, MODEL_NAMES
from window_chrome import install_chrome

_HARDWARE_TASKS = set()


class HardwareTask(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            from processing_hardware import probe_hardware
            self.done.emit(probe_hardware(refresh=True))
        except Exception as error:
            self.failed.emit(str(error))


def text(value):
    return str(value if value is not None else '').replace(chr(183), ' ')


class ScanTask(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, folder):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            self.done.emit(scan_sessions(self.folder))
        except Exception as error:
            self.failed.emit(str(error))


class BatchTask(QThread):
    progress = pyqtSignal(int, str)
    record_done = pyqtSignal(object)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, folders, retain_raw_rgb=True, *, device='cpu', jobs=1, cpu_threads=None):
        super().__init__()
        self.folders = tuple(folders)
        self.retain_raw_rgb = retain_raw_rgb
        self.device = device
        self.jobs = jobs
        self.cpu_threads = cpu_threads
        self._stop = threading.Event()

    def stop_after_current(self):
        self._stop.set()

    def run(self):
        try:
            from processing_hardware import plan_execution, probe_hardware
            plan = plan_execution(self.device, min(self.jobs, max(1, len(self.folders))), self.cpu_threads)
            device, jobs = plan['device'], plan['jobs']
            if device == 'cpu':
                self.progress.emit(0, f"CPU {jobs} 路 × 每路 {plan['threads_per_job']} 推理线程 = {plan['cpu_threads_used']} 线程")
            else:
                gpu = next(item for item in probe_hardware()['cuda_devices'] if item['device'] == device)
                self.progress.emit(0, 'GPU 处理，' + gpu['name'])
            self.done.emit(process_batch(self.folders, self.retain_raw_rgb,
                          on_progress=self.progress.emit, on_record=self.record_done.emit,
                          should_stop=self._stop.is_set, device=device, jobs=jobs,
                          cpu_threads=plan['cpu_threads_budget']))
        except Exception as error:
            self.failed.emit(str(error))


class ProcessorWindow(QMainWindow):
    def __init__(self, *, preferences_root=None):
        super().__init__()
        configure_system_font()
        self.setWindowTitle('ADHD 实验数据处理')
        self.setWindowIcon(QIcon(str(RESOURCE_ROOT / 'assets/app.ico')))
        self.setMinimumSize(800, 560)
        self.records = []
        self.mapping_records = []
        self.task = None
        self.directory = None
        self._active_folders = []
        self.preferences_root = Path(preferences_root) if preferences_root is not None else DATA_ROOT
        from processing_hardware import load_preferences
        preferences = load_preferences(self.preferences_root)
        self.hardware = None
        self.hardware_task = None
        self._closing = False
        self._preferences_ready = False
        host = QWidget()
        host.setObjectName('ProcessorWorkspace')
        self.setCentralWidget(host)
        columns = QHBoxLayout(host)
        columns.setContentsMargins(12, 10, 12, 12)
        columns.setSpacing(10)
        self.sidebar = self.build_sidebar()
        columns.addWidget(self.sidebar)

        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(2, 2, 2, 0)
        layout.setSpacing(8)
        columns.addWidget(main, 1)
        layout.addWidget(self.build_heading())

        directory_card = QWidget()
        directory_card.setObjectName('ProcessorCard')
        directory_layout = QVBoxLayout(directory_card)
        directory_layout.setContentsMargins(12, 9, 12, 10)
        directory_layout.setSpacing(6)
        directory_layout.addLayout(self.icon_heading('folder', '原始数据文件夹'))
        directory_row = QHBoxLayout()
        self.directory_input = QLineEdit()
        self.directory_input.setPlaceholderText('请选择包含实验数据的文件夹…')
        self.directory_input.setAccessibleName('原始数据文件夹')
        self.directory_input.returnPressed.connect(self.scan_entered)
        directory_row.addWidget(self.directory_input, 1)
        self.choose_button = self.action('folder', '选择文件夹', self.choose_directory, primary=True)
        self.scan_button = self.action('search', '扫描记录', self.scan_entered)
        directory_row.addWidget(self.choose_button)
        directory_row.addWidget(self.scan_button)
        directory_layout.addLayout(directory_row)
        layout.addWidget(directory_card)

        device_card = QWidget()
        device_card.setObjectName('ProcessorCard')
        device_layout = QVBoxLayout(device_card)
        self.device_layout = device_layout
        device_layout.setContentsMargins(12, 9, 12, 9)
        device_layout.setSpacing(6)
        device_fields = QWidget()
        device_fields.setMinimumHeight(58)
        device_row = QHBoxLayout(device_fields)
        device_row.setContentsMargins(0, 0, 0, 0)
        device_row.setSpacing(12)
        self.device_choice = QComboBox()
        self.device_choice.setAccessibleName('处理设备')
        self.device_choice.addItem('自动设备', 'auto')
        self.device_choice.addItem('CPU', 'cpu')
        self.device_choice.addItem('NVIDIA GPU', 'cuda')
        if self.device_choice.findData(preferences['device']) < 0:
            self.device_choice.addItem(preferences['device'], preferences['device'])
        self.device_choice.setCurrentIndex(self.device_choice.findData(preferences['device']))
        self.device_choice.setToolTip('自动优先使用可用的 NVIDIA GPU，否则使用 CPU')
        self.device_choice.setMinimumWidth(130)
        self.device_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.device_choice.setMinimumContentsLength(12)
        self.device_field(device_row, 'cpu', '处理设备', self.device_choice, 2)
        self.cpu_jobs = QSpinBox()
        self.cpu_jobs.setRange(1, 8)
        self.cpu_jobs.setValue(preferences['jobs'])
        self.cpu_jobs.setAccessibleName('同时处理的记录数')
        self.cpu_jobs.setToolTip('CPU 同时处理的记录数，NVIDIA GPU 模式逐条处理')
        self.device_field(device_row, 'cpu', 'CPU 并行', self.cpu_jobs, 1)
        self.cpu_threads = QSpinBox()
        self.cpu_threads.setRange(1, max(1, os.cpu_count() or 1))
        self.cpu_threads.setValue(preferences['cpu_threads'])
        self.cpu_threads.setAccessibleName('CPU 推理线程总预算')
        self.cpu_threads.setToolTip('CPU 推理线程在并行记录之间均分；解码与人脸检测会另用辅助线程')
        self.device_field(device_row, 'layers', '推理线程预算', self.cpu_threads, 1)
        self.refresh_hardware_button = self.action('activity', '检测设备', self.refresh_hardware, quiet=True)
        device_row.addWidget(self.refresh_hardware_button, 0, Qt.AlignmentFlag.AlignBottom)
        device_layout.addWidget(device_fields)

        status_bar = QWidget()
        status_bar.setObjectName('ProcessorStatusBar')
        status_bar.setMinimumHeight(36)
        status_row = QHBoxLayout(status_bar)
        status_row.setContentsMargins(9, 5, 9, 5)
        status_row.setSpacing(9)
        status_row.addWidget(self.mark('cpu', 15))
        self.hardware_status = label(f'CPU {os.cpu_count() or 1} 逻辑核    GPU 检测中', 'ProcessorStatDetail')
        self.hardware_status.setTextFormat(Qt.TextFormat.PlainText)
        self.hardware_status.setWordWrap(True)
        self.hardware_status.setMinimumWidth(0)
        status_row.addWidget(self.hardware_status, 2)
        status_row.addWidget(self.mark('layers', 15))
        self.allocation_label = label('', 'ProcessorStatDetail', wrap=True)
        self.allocation_label.setTextFormat(Qt.TextFormat.PlainText)
        self.allocation_label.setMinimumWidth(0)
        status_row.addWidget(self.allocation_label, 2)
        device_layout.addWidget(status_bar)
        self.retain_raw = QCheckBox('保留原始 RGB')
        self.retain_raw.setChecked(preferences['retain_raw_rgb'])
        device_layout.addWidget(self.retain_raw, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(device_card)

        actions = QHBoxLayout()
        self.action_row = actions
        actions.addStretch()
        self.select_all = button('全选待处理', lambda: self.set_checked(True), quiet=True)
        self.select_none = button('取消全选', lambda: self.set_checked(False), quiet=True)
        actions.addWidget(self.select_all)
        actions.addWidget(self.select_none)
        self.start_button = self.action('activity', '处理选中记录', self.start_processing, primary=True)
        self.stop_button = button('停止排队', self.stop_processing)
        self.stop_button.hide()
        actions.addWidget(self.stop_button)
        actions.addWidget(self.start_button)
        layout.addLayout(actions)

        self.tabs = QTabWidget()
        self.record_table = self.make_table(['选择', '实验时间', '姓名', '编号', '方案', '模型', '状态', '原始 RGB', '实验段'])
        self.record_table.setToolTip('勾选已完成的记录可一起查看轨迹，也可按住 Ctrl 或 Shift 选择多行')
        self.record_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.record_table.setColumnWidth(0, 48)
        self.record_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.record_table.setColumnWidth(1, 112)
        self.record_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.record_table.setColumnWidth(5, 142)
        self.record_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        self.record_table.setColumnWidth(8, 75)
        self.mapping_table = self.make_table(['姓名', '编号', '性别', '年龄', '数据 TXT', '信息JSON', '状态', '实验段'])
        self.mapping_table.setToolTip('按住 Ctrl 或 Shift 可选择多条记录一起查看轨迹')
        for column, width in ((0, 110), (1, 80), (2, 56), (3, 56), (6, 98), (7, 75)):
            self.mapping_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.mapping_table.setColumnWidth(column, width)
        self.tabs.addTab(self.record_table, '实验记录')
        self.tabs.addTab(self.mapping_table, '人员与文件')
        self.tabs.setTabIcon(0, processor_icon('list'))
        self.tabs.setTabIcon(1, processor_icon('users'))
        layout.addWidget(self.tabs, 1)
        self.record_table.itemChanged.connect(self.update_actions)
        self.record_table.itemSelectionChanged.connect(self.update_actions)
        self.record_table.itemDoubleClicked.connect(lambda _: self.show_capture_info())
        self.mapping_table.itemSelectionChanged.connect(self.update_actions)
        self.tabs.currentChanged.connect(self.update_actions)
        self.progress_title = label('')
        self.progress_title.setTextFormat(Qt.TextFormat.PlainText)
        self.progress_title.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress_title.hide()
        self.progress.hide()
        layout.addWidget(self.progress_title)
        layout.addWidget(self.progress)
        self.selection_path = label('', 'ProcessorPath')
        self.selection_path.setTextFormat(Qt.TextFormat.PlainText)
        self.selection_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selection_path.setFixedHeight(20)
        self.selection_path.setMinimumWidth(0)
        self.selection_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.selection_path.hide()
        layout.addWidget(self.selection_path)
        bottom = QHBoxLayout()
        self.view_button = self.action('link', '查看轨迹', self.view_selected, primary=True)
        self.open_data_button = self.action('file', '打开数据 TXT', lambda: self.open_selected('txt_path'))
        self.open_person_button = self.action('code', '打开信息JSON', lambda: self.open_selected('participant_json_path'))
        self.open_folder_button = self.action('folder', '打开记录文件夹', lambda: self.open_selected('path'))
        bottom.addWidget(self.view_button)
        bottom.addWidget(self.open_data_button)
        bottom.addWidget(self.open_person_button)
        bottom.addWidget(self.open_folder_button)
        bottom.addStretch()
        self.export_button = self.action('download', '导出映射表', self.export_csv)
        bottom.addWidget(self.export_button)
        layout.addLayout(bottom)
        self.stats_panel = self.build_statistics()
        columns.addWidget(self.stats_panel)
        install_chrome(self)
        install_processor_theme(self)
        area = self.screen().availableGeometry()
        self.resize(min(1320, area.width() - 40), min(872, area.height() - 40))
        self.update_columns()
        self._preferences_ready = True
        for signal in (self.device_choice.currentIndexChanged, self.cpu_jobs.valueChanged,
                       self.cpu_threads.valueChanged, self.retain_raw.toggled):
            signal.connect(self.preferences_changed)
        self.update_device_controls()
        self.update_actions()
        QTimer.singleShot(0, self.refresh_hardware)

    @staticmethod
    def mark(symbol, size=20):
        icon = QLabel()
        icon.setPixmap(processor_icon(symbol, size=size).pixmap(size, size))
        icon.setFixedSize(size, size)
        return icon

    @staticmethod
    def action(symbol, title, callback, *, primary=False, quiet=False):
        action = button(title, callback, primary=primary, quiet=quiet)
        action.setIcon(processor_icon(symbol, color='#FFFFFF' if primary else '#7569D3'))
        action.setIconSize(QSize(17, 17))
        return action

    @classmethod
    def icon_heading(cls, symbol, title, size=20):
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(cls.mark(symbol, size))
        heading.addWidget(label(title, 'ProcessorHeading'))
        heading.addStretch()
        return heading

    @classmethod
    def device_field(cls, row, symbol, title, control, stretch):
        field = QVBoxLayout()
        field.setSpacing(4)
        field.addLayout(cls.icon_heading(symbol, title, 17))
        field.addWidget(control)
        row.addLayout(field, stretch)

    def build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName('ProcessorSidebar')
        sidebar.setFixedWidth(170)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 17, 12, 16)
        layout.setSpacing(8)
        brain = ProcessorIllustration('brain')
        brain.setFixedHeight(88)
        layout.addWidget(brain)
        layout.addWidget(label('ADHD\n实验数据处理', 'ProcessorNavBrand'))
        layout.addSpacing(19)
        work = self.action('layers', '实验数据处理', lambda: self.tabs.setCurrentIndex(0), primary=True)
        work.setObjectName('ProcessorNavActive')
        self.settings_button = self.action('settings', '设置', self.show_settings)
        self.settings_button.setObjectName('ProcessorNav')
        self.about_button = self.action('info', '关于', self.show_about)
        self.about_button.setObjectName('ProcessorNav')
        for action in (work, self.settings_button, self.about_button):
            layout.addWidget(action)
        layout.addStretch()
        return sidebar

    def build_heading(self):
        hero = QWidget()
        hero.setObjectName('ProcessorHero')
        row = QHBoxLayout(hero)
        row.setContentsMargins(12, 7, 10, 8)
        row.setSpacing(10)
        accent = QWidget()
        accent.setObjectName('ProcessorTitleAccent')
        accent.setFixedSize(6, 25)
        row.addWidget(accent, 0, Qt.AlignmentFlag.AlignTop)
        title = QVBoxLayout()
        title.setSpacing(3)
        title.addWidget(label('实验数据处理', 'ProcessorTitle'))
        self.subtitle = label('导入 ADHD 实验原始数据，进行批量处理与分析。', 'ProcessorSubtitle')
        title.addWidget(self.subtitle)
        row.addLayout(title, 1)
        self.count_label = label('0 条记录', 'ProcessorBadge')
        row.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.compact_settings = self.action('settings', '设置', self.show_settings, quiet=True)
        self.compact_about = self.action('info', '关于', self.show_about, quiet=True)
        row.addWidget(self.compact_settings)
        row.addWidget(self.compact_about)
        return hero

    def build_statistics(self):
        panel = QWidget()
        panel.setObjectName('ProcessorStats')
        panel.setFixedWidth(232)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 15, 12, 13)
        layout.setSpacing(9)
        layout.addLayout(self.icon_heading('chart', '处理统计', 22))
        layout.addSpacing(5)
        self.pending_value = self.add_metric(layout, 'folder', '待处理记录', '0', '条')
        self.completed_value = self.add_metric(layout, 'check', '已完成', '0', '条')
        self.gpu_detail = label('', 'ProcessorStatDetail')
        self.gpu_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.gpu_detail.setWordWrap(True)
        self.gpu_value = self.add_metric(layout, 'gpu', 'GPU 状态', '检测中', detail=self.gpu_detail)
        self.gpu_value.setObjectName('ProcessorGpuState')
        self.thread_value = self.add_metric(layout, 'layers', '线程预算', str(self.cpu_threads.value()))
        layout.addStretch(1)
        artwork = ProcessorIllustration('monitor')
        artwork.setMinimumHeight(126)
        artwork.setMaximumHeight(230)
        layout.addWidget(artwork, 4)
        layout.addWidget(label('ADHD 实验数据处理', 'ProcessorHeading'), alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        return panel

    @classmethod
    def add_metric(cls, layout, symbol, title, value, unit='', *, detail=None):
        metric = QWidget()
        metric.setObjectName('ProcessorStatRow')
        row = QHBoxLayout(metric)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(7)
        row.addWidget(cls.mark(symbol, 20))
        description = QVBoxLayout()
        description.setSpacing(1)
        description.addWidget(label(title, 'ProcessorStatLabel'))
        if detail is not None:
            description.addWidget(detail)
        row.addLayout(description, 1)
        number = label(value, 'ProcessorStatValue')
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(number)
        if unit:
            row.addWidget(label(unit, 'ProcessorStatDetail'))
        layout.addWidget(metric)
        return number

    def update_columns(self):
        show_sidebar = self.width() >= 990
        compact_height = self.height() < 650
        self.sidebar.setVisible(show_sidebar)
        self.stats_panel.setVisible(self.width() >= 1180 and not compact_height)
        self.compact_settings.setVisible(not show_sidebar)
        self.compact_about.setVisible(not show_sidebar)
        self.subtitle.setVisible(not compact_height)
        if compact_height and self.action_row.indexOf(self.retain_raw) < 0:
            self.device_layout.removeWidget(self.retain_raw)
            self.action_row.insertWidget(0, self.retain_raw)
        elif not compact_height and self.device_layout.indexOf(self.retain_raw) < 0:
            self.action_row.removeWidget(self.retain_raw)
            self.device_layout.addWidget(self.retain_raw, alignment=Qt.AlignmentFlag.AlignLeft)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'stats_panel'):
            self.update_columns()
            self.update_selection_path()

    def show_settings(self):
        if self.busy:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('处理设置')
        dialog.setMinimumWidth(410)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        device = QComboBox()
        for index in range(self.device_choice.count()):
            device.addItem(self.device_choice.itemText(index), self.device_choice.itemData(index))
        device.setCurrentIndex(self.device_choice.currentIndex())
        jobs = QSpinBox()
        jobs.setRange(self.cpu_jobs.minimum(), self.cpu_jobs.maximum())
        jobs.setValue(self.cpu_jobs.value())
        threads = QSpinBox()
        threads.setRange(self.cpu_threads.minimum(), self.cpu_threads.maximum())
        threads.setValue(self.cpu_threads.value())
        raw = QCheckBox('保留原始 RGB')
        raw.setChecked(self.retain_raw.isChecked())
        form.addRow('处理设备', device)
        form.addRow('CPU 并行', jobs)
        form.addRow('推理线程预算', threads)
        form.addRow('', raw)
        layout.addLayout(form)

        def update_cpu_fields():
            value = device.currentData() or 'auto'
            gpu = value.startswith('cuda') or (value == 'auto' and (self.hardware or {}).get('cuda_available'))
            jobs.setEnabled(not gpu)
            threads.setEnabled(not gpu)

        device.currentIndexChanged.connect(update_cpu_fields)
        update_cpu_fields()
        self.add_dialog_actions(layout, dialog, save=True)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        for widget in (self.device_choice, self.cpu_jobs, self.cpu_threads, self.retain_raw):
            widget.blockSignals(True)
        index = self.device_choice.findData(device.currentData())
        if index >= 0:
            self.device_choice.setCurrentIndex(index)
        self.cpu_jobs.setValue(jobs.value())
        self.cpu_threads.setValue(threads.value())
        self.retain_raw.setChecked(raw.isChecked())
        for widget in (self.device_choice, self.cpu_jobs, self.cpu_threads, self.retain_raw):
            widget.blockSignals(False)
        self.preferences_changed()

    def show_about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('关于')
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.addLayout(self.icon_heading('eye', 'ADHD 实验数据处理', 26))
        layout.addWidget(label('批量处理实验记录，查看注视点分布与回放。', 'ProcessorSubtitle', wrap=True))
        self.add_dialog_actions(layout, dialog)
        dialog.exec()

    @staticmethod
    def add_dialog_actions(layout, dialog, *, save=False):
        actions = QHBoxLayout()
        actions.addStretch()
        if save:
            actions.addWidget(button('取消', dialog.reject))
        actions.addWidget(button('保存' if save else '关闭', dialog.accept, primary=True))
        layout.addLayout(actions)

    @staticmethod
    def make_table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(44)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setWordWrap(False)
        return table

    @property
    def busy(self):
        return self.task is not None and self.task.isRunning()

    def refresh_hardware(self):
        if self._closing or self.busy or self.hardware_task is not None:
            return
        task = HardwareTask()
        self.hardware_task = task
        # Detection may outlive a closing window without destroying a running
        # QThread or blocking the UI while CUDA initializes.
        _HARDWARE_TASKS.add(task)
        task.done.connect(self.hardware_ready)
        task.failed.connect(self.hardware_failed)
        task.finished.connect(self.hardware_finished)
        task.finished.connect(lambda: _HARDWARE_TASKS.discard(task))
        task.finished.connect(task.deleteLater)
        self.refresh_hardware_button.setEnabled(False)
        self.hardware_status.setText(f'CPU {os.cpu_count() or 1} 逻辑核    GPU 检测中')
        self.set_gpu_stat('检测中')
        task.start()

    def hardware_ready(self, result):
        self.hardware = result
        cores = max(1, int(result.get('cpu_count') or os.cpu_count() or 1))
        self.cpu_threads.setMaximum(cores)
        devices = result.get('cuda_devices') or []
        if result.get('cuda_available'):
            names = '、'.join(item['name'] for item in devices if item.get('available', True))
            status = f'CPU {cores} 逻辑核    GPU 可用：{names}'
            self.set_gpu_stat('可用', names, available=True)
        else:
            reason = str(result.get('cuda_reason') or '未检测到 NVIDIA GPU')
            brief = reason.splitlines()[0]
            if len(brief) > 70:
                brief = brief[:67] + '…'
            status = f'CPU {cores} 逻辑核    GPU 不可用：{brief}'
            self.set_gpu_stat('不可用')
        self.hardware_status.setText(status)
        self.hardware_status.setToolTip(str(result.get('cuda_reason') or status))
        selected = self.device_choice.currentData()
        self.device_choice.blockSignals(True)
        self.device_choice.clear()
        for title, value in [('自动设备', 'auto'), ('CPU', 'cpu'), ('NVIDIA GPU', 'cuda')]:
            self.device_choice.addItem(title, value)
        for item in devices:
            suffix = '' if item.get('available', True) else '（不可用）'
            self.device_choice.addItem(f"GPU {item['index']}：{item['name']}{suffix}", item['device'])
        if self.device_choice.findData(selected) < 0:
            self.device_choice.addItem(str(selected) + '（不可用）', selected)
        self.device_choice.setCurrentIndex(self.device_choice.findData(selected))
        self.device_choice.blockSignals(False)
        self.update_device_controls()
        self.update_actions()

    def set_gpu_stat(self, state, detail='', *, available=False):
        self.gpu_value.setText(state)
        self.gpu_value.setProperty('available', available)
        style = self.gpu_value.style()
        style.unpolish(self.gpu_value)
        style.polish(self.gpu_value)
        self.gpu_detail.setText(detail)
        self.gpu_detail.setToolTip(detail)
        self.gpu_detail.setVisible(bool(detail))

    def hardware_failed(self, reason):
        self.hardware_ready(dict(cpu_count=os.cpu_count() or 1, cuda_available=False,
                                 cuda_devices=[], cuda_reason=reason))

    def hardware_finished(self):
        self.hardware_task = None
        if not self._closing:
            self.refresh_hardware_button.setEnabled(not self.busy)

    def preferences_changed(self, *_):
        self.update_device_controls()
        self.update_actions()
        if not self._preferences_ready:
            return
        from processing_hardware import save_preferences
        try:
            save_preferences(dict(device=self.device_choice.currentData(), jobs=self.cpu_jobs.value(),
                                  cpu_threads=self.cpu_threads.value(), retain_raw_rgb=self.retain_raw.isChecked()),
                             self.preferences_root)
        except (OSError, ValueError) as error:
            self.show_error('处理设置未能保存：' + str(error))

    def selected_device_available(self):
        selected = self.device_choice.currentData() or 'auto'
        if not selected.startswith('cuda') or self.hardware is None:
            return True
        if not self.hardware.get('cuda_available'):
            return False
        if selected == 'cuda':
            return True
        return any(item['device'] == selected and item.get('available', True)
                   for item in self.hardware.get('cuda_devices', []))

    def choose_directory(self):
        chosen = choose_source_directory(self, self.directory_input.text() or str(DATA_ROOT))
        if chosen:
            self.load_directory(chosen)

    def scan_entered(self):
        if not self.directory_input.text().strip():
            self.choose_directory()
            return
        self.load_directory(self.directory_input.text().strip())

    def load_directory(self, folder):
        if self.busy:
            return
        self.directory = Path(folder).expanduser()
        self.directory_input.setText(str(self.directory))
        self.progress_title.setText('正在扫描实验记录')
        self.progress_title.show()
        self.progress.setRange(0, 0)
        self.progress.show()
        self.task = ScanTask(self.directory)
        self.task.done.connect(self.show_records)
        self.task.failed.connect(self.show_error)
        self.task.finished.connect(self.task_finished)
        self.set_busy(True)
        self.task.start()

    def show_records(self, records):
        self.records = list(records)
        self.populate_tables()
        self.progress_title.setText('扫描完成' if records else '未找到实验记录')
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

    def populate_tables(self):
        selected = self.selected_record()
        selected_path = selected.get('path') if selected else None
        self.record_table.blockSignals(True)
        self.mapping_table.blockSignals(True)
        self.record_table.setRowCount(len(self.records))
        self.mapping_records = [row for row in self.records if row.get('complete')
                                and row.get('txt_path') and self.information_path(row).is_file()]
        self.mapping_table.setRowCount(len(self.mapping_records))
        selected_index = 0
        for index, row in enumerate(self.records):
            check = QTableWidgetItem()
            check.setData(Qt.ItemDataRole.UserRole, row['path'])
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            if not (row.get('processable') or row.get('complete')):
                check.setFlags(Qt.ItemFlag.ItemIsSelectable)
            self.record_table.setItem(index, 0, check)
            short_models = {'affnet_natural': 'AFF-Net 全量混合', 'affnet_mixed': 'AFF-Net 均衡混合',
                            'official': 'FAZE 官方', 'mixed': 'FAZE 混合', 'gc_full': 'FAZE GC'}
            created = row.get('created', '').replace('T', ' ')[:19]
            short_time = created[5:16] if len(created) >= 16 and created[4:5] == '-' else created
            values = [short_time, row.get('name'), row.get('participant_id'),
                      row.get('scheme_name'), short_models.get(row.get('model_id'), row.get('model_id')),
                      row.get('status'), '已保留' if row.get('has_raw') else '未保留', segment_label(row)]
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(text(value))
                tooltip = (created if column == 1 else row.get('error') if column == 6 and row.get('error') else
                           MODEL_NAMES.get(row.get('model_id'), row.get('model_id')) if column == 5 else
                           row.get('experiment_session_id') if column == 8 else value)
                item.setToolTip(text(tooltip))
                self.record_table.setItem(index, column, item)
            if row['path'] == selected_path:
                selected_index = index
        mapping_index = 0
        for index, row in enumerate(self.mapping_records):
            information = self.information_path(row)
            mapping = [row.get('name'), row.get('participant_id'), row.get('sex'), row.get('age'),
                       Path(row['txt_path']).name, information.name, row.get('status'), segment_label(row)]
            for column, value in enumerate(mapping):
                item = QTableWidgetItem(text(value))
                item.setData(Qt.ItemDataRole.UserRole, row['path'])
                tooltip = (row.get('txt_path') if column == 4 else str(information) if column == 5
                           else row.get('error') if column == 6 and row.get('error')
                           else row.get('experiment_session_id') if column == 7 else value)
                item.setToolTip(text(tooltip))
                self.mapping_table.setItem(index, column, item)
            if row['path'] == selected_path:
                mapping_index = index
        if self.records:
            self.record_table.selectRow(selected_index)
        if self.mapping_records:
            self.mapping_table.selectRow(mapping_index)
        self.record_table.blockSignals(False)
        self.mapping_table.blockSignals(False)
        complete = sum(row.get('complete', False) for row in self.records)
        pending = sum(bool(row.get('processable') and not row.get('complete')) for row in self.records)
        self.count_label.setText(f'{len(self.records)} 条记录')
        self.pending_value.setText(str(pending))
        self.completed_value.setText(str(complete))
        self.update_actions()

    def selected_record(self):
        table = self.mapping_table if self.tabs.currentIndex() == 1 else self.record_table
        index = table.currentRow()
        item = table.item(index, 0) if index >= 0 else None
        path = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return next((row for row in self.records if row['path'] == path), None)

    def selected_folders(self):
        return [row['path'] for index, row in enumerate(self.records)
                if row.get('processable') and self.record_table.item(index, 0).checkState() == Qt.CheckState.Checked]

    def visual_folders(self):
        if self.tabs.currentWidget() is self.record_table:
            checked = [row['path'] for index, row in enumerate(self.records)
                       if row.get('complete') and self.record_table.item(index, 0).checkState() == Qt.CheckState.Checked]
            if checked:
                return checked
            table, records = self.record_table, self.records
        else:
            table, records = self.mapping_table, self.mapping_records
        indices = sorted(index.row() for index in table.selectionModel().selectedRows())
        return [records[index]['path'] for index in indices if records[index].get('complete')]

    def set_checked(self, checked):
        for index, row in enumerate(self.records):
            if row.get('processable') or (not checked and row.get('complete')):
                self.record_table.item(index, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def update_actions(self, *_):
        if not hasattr(self, 'open_data_button'):
            return
        row = self.selected_record() or {}
        count = len(self.visual_folders())
        self.view_button.setText(f'查看轨迹（{count}）' if count > 1 else '查看轨迹')
        self.view_button.setEnabled(count > 0 and not self.busy)
        self.open_data_button.setEnabled(bool(row.get('txt_path')))
        self.open_person_button.setEnabled(bool(row.get('path')) and self.information_path(row).is_file())
        self.open_folder_button.setEnabled(bool(row.get('path')))
        self.export_button.setEnabled(bool(self.mapping_records) and not self.busy)
        self.start_button.setEnabled(bool(self.selected_folders()) and not self.busy and self.selected_device_available())
        path = row.get('path', '')
        self.selection_path.setToolTip(path)
        self.selection_path.setVisible(bool(path))
        self.update_selection_path()

    def update_selection_path(self):
        path = self.selection_path.toolTip()
        self.selection_path.setText(self.selection_path.fontMetrics().elidedText(
            path, Qt.TextElideMode.ElideMiddle, max(20, self.selection_path.width())))

    def set_busy(self, busy):
        for control in (self.choose_button, self.scan_button, self.directory_input, self.retain_raw,
                        self.select_all, self.select_none, self.start_button, self.export_button,
                        self.view_button, self.device_choice, self.settings_button, self.compact_settings):
            control.setEnabled(not busy)
        self.update_device_controls()
        self.record_table.setEnabled(not busy)
        self.stop_button.setVisible(busy and isinstance(self.task, BatchTask))
        self.stop_button.setEnabled(True)
        self.refresh_hardware_button.setEnabled(not busy and self.hardware_task is None)

    def update_device_controls(self):
        selected = self.device_choice.currentData() or 'auto'
        gpu = selected.startswith('cuda') or (selected == 'auto' and bool((self.hardware or {}).get('cuda_available')))
        enabled = self.device_choice.isEnabled() and not gpu
        self.cpu_jobs.setEnabled(enabled)
        self.cpu_threads.setEnabled(enabled)
        self.thread_value.setText(str(self.cpu_threads.value()))
        if gpu:
            self.allocation_label.setText(f'GPU 单路批量处理    CPU 辅助推理线程 {min(4, os.cpu_count() or 1)}')
        else:
            from processing_hardware import plan_execution
            plan = plan_execution('cpu', self.cpu_jobs.value(), self.cpu_threads.value())
            self.allocation_label.setText(
                f"CPU 分配 {plan['jobs']} 路 × 每路 {plan['threads_per_job']} 推理线程 = {plan['cpu_threads_used']} 线程")
        self.allocation_label.setToolTip('该预算分配给模型推理；视频解码、人脸检测和系统会使用其他辅助线程')

    def start_processing(self):
        if self.busy:
            return
        folders = self.selected_folders()
        if not folders:
            return
        self._active_folders = folders
        self.task = BatchTask(folders, self.retain_raw.isChecked(),
                              device=self.device_choice.currentData(), jobs=self.cpu_jobs.value(),
                              cpu_threads=self.cpu_threads.value())
        self.task.progress.connect(self.on_progress)
        self.task.record_done.connect(self.record_finished)
        self.task.done.connect(self.batch_finished)
        self.task.failed.connect(self.show_error)
        self.task.finished.connect(self.task_finished)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.progress_title.setText('开始处理')
        self.progress_title.show()
        self.set_busy(True)
        self.task.start()

    def stop_processing(self):
        if isinstance(self.task, BatchTask) and self.busy:
            self.task.stop_after_current()
            self.stop_button.setEnabled(False)
            self.progress_title.setText('等待正在处理的记录完成')

    def on_progress(self, percent, message):
        self.progress.setValue(percent)
        self.progress_title.setText(text(message))

    def record_finished(self, updated):
        self.records = [dict(row, **updated) if row['path'] == updated['path'] else row for row in self.records]
        self.populate_tables()

    def batch_finished(self, rows):
        failures = sum(row.get('status') in ('处理失败', '无有效坐标') for row in rows)
        stopped = len(rows) < len(self._active_folders)
        self.progress_title.setText(f"{'已停止' if stopped else '处理结束'}    {len(rows)} 条    需检查 {failures} 条")
        if not stopped:
            self.progress.setValue(100)

    def task_finished(self):
        self.progress.setRange(0, 100)
        self.set_busy(False)
        self.stop_button.hide()
        self.update_actions()

    def show_error(self, message):
        self.progress_title.setText(text(message))
        self.progress_title.show()

    @staticmethod
    def information_path(row):
        """New and legacy records both expose their original information JSON."""
        folder = Path(row.get('path') or '')
        expected = folder / 'session.json'
        supplied = row.get('participant_json_path')
        if supplied:
            candidate = Path(supplied)
            if not candidate.is_absolute():
                candidate = folder / candidate
            if candidate.resolve() == expected.resolve():
                return candidate
        return expected

    def open_selected(self, key):
        row = self.selected_record() or {}
        path = self.information_path(row) if key == 'participant_json_path' and row.get('path') else row.get(key)
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def view_selected(self):
        folders = self.visual_folders()
        if self.busy or not folders:
            return
        from experiment_viewer import ExperimentViewer
        selected = set(folders)
        records = {row['path']: row for row in self.records if row['path'] in selected}
        if len(folders) == 1:
            row = records.get(folders[0], {})
            if 'experiment_session_id' in row:
                self.visual_dialog = ExperimentViewer(folders[0], self, records=records)
            else:
                self.visual_dialog = ExperimentViewer(folders[0], self)
        else:
            self.visual_dialog = ExperimentViewer(folders, self, records=records)
        self.visual_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.visual_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.visual_dialog.show()

    def show_capture_info(self):
        row = self.selected_record()
        if not row:
            return
        try:
            meta = json.loads((Path(row['path']) / 'session.json').read_text(encoding='utf-8-sig'))
            cfg = meta.get('config') or {}
            camera = (meta.get('acquisition_environment') or {}).get('camera') or {}
            calibration = meta.get('calibration') or {}
            values = []
            if 'experiment_session_id' in row:
                values.extend([('实验编号', row['experiment_session_id']), ('实验段', segment_label(row))])
            values.extend([('屏幕分辨率', f"{cfg.get('physical_width', '—')} × {cfg.get('physical_height', '—')} px"),
                      ('逻辑分辨率', f"{cfg.get('logical_width', '—')} × {cfg.get('logical_height', '—')} px"),
                      ('DPI 缩放', cfg.get('dpr', '—')),
                      ('屏幕尺寸', f"{cfg.get('screen_width_mm', '—')} × {cfg.get('screen_height_mm', '—')} mm"),
                      ('相机编号', cfg.get('camera_index', '—')),
                      ('相机画面', f"{camera.get('actual_width', '—')} × {camera.get('actual_height', '—')} px"),
                      ('模型', MODEL_NAMES.get(meta.get('model_id'), meta.get('model_id', '—'))),
                      ('校准点数', calibration.get('target_count', cfg.get('calibration_points', 0))),
                      ('校准状态', {'pending': '待离线校准', 'incomplete': '校准采集未完成',
                                    'complete': '已完成'}.get(calibration.get('status'),
                                    '未启用' if not calibration.get('target_count') else '已保存')),
                      ('记录位置', row['path'])])
            dialog = QDialog(self)
            dialog.setWindowTitle('采集信息')
            dialog.resize(min(560, self.width() - 60), min(480, self.height() - 60))
            dialog.setMinimumSize(400, 330)
            layout = QVBoxLayout(dialog)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            content = QWidget()
            form = QFormLayout(content)
            form.setVerticalSpacing(14)
            for name, value in values:
                field = label(text(value), wrap=True)
                field.setTextFormat(Qt.TextFormat.PlainText)
                field.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(name, field)
            scroll.setWidget(content)
            layout.addWidget(scroll, 1)
            layout.addWidget(button('关闭', dialog.accept))
            dialog.exec()
        except (OSError, ValueError, TypeError) as error:
            self.show_error(str(error))

    def export_csv(self):
        if self.busy or not self.mapping_records:
            return
        root = self.directory if self.directory and self.directory.is_dir() else DATA_ROOT
        destination, _ = QFileDialog.getSaveFileName(self, '导出人员与文件映射表', str(root / '人员与实验数据映射.csv'), 'CSV (*.csv)')
        if destination:
            try:
                export_mapping(self.mapping_records, destination)
                self.progress_title.setText('映射表已保存')
                self.progress_title.show()
            except OSError as error:
                QMessageBox.warning(self, '无法保存', str(error))

    def closeEvent(self, event):
        if self.busy:
            self.stop_processing()
            self.progress_title.setText('请等待当前记录处理完成后关闭')
            self.progress_title.show()
            event.ignore()
            return
        self._closing = True
        event.accept()
