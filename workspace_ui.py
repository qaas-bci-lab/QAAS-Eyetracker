"""Person-first desktop workspace, separate from full-screen acquisition."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QSizePolicy,
    QButtonGroup, QComboBox, QDialog, QFrame, QMessageBox)
from participant_info import ParticipantInfo
from history_dialog import HistoryDialog
from participant_store import Catalog
from ui_components import button, label
from scheme_store import processing_for_mode
from app_paths import RAW_CAPTURE


class ParticipantWorkspace:
    def build_workspace(self, root):
        from workspace_style import STYLE
        app = QApplication.instance()
        app.setStyleSheet(app.styleSheet() + STYLE)
        self.workspace_mode = 'experiment'
        self.current_person = None
        self.catalog = Catalog(root)
        self.catalog.refresh(record_roots=self.history_roots())
        self.resize(860, 620)
        self.setMinimumSize(720, 560)
        host = QWidget()
        host.setObjectName('Workspace')
        self.setCentralWidget(host)
        outer = QVBoxLayout(host)
        outer.setContentsMargins(24, 6, 24, 10)
        outer.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(label('ADHD 眼动采集' if RAW_CAPTURE else 'ADHD 眼动', 'HeroTitle'))
        header.addStretch()
        self.history_button = button('历史记录', self.open_history, quiet=True)
        self.settings_button = button('设置', self.open_settings, quiet=True)
        header.addWidget(self.history_button)
        header.addWidget(self.settings_button)
        outer.addLayout(header)
        outer.addStretch(1)

        self.home_card = QFrame()
        self.home_card.setObjectName('LaunchCard')
        self.home_card.setFixedWidth(420)
        self.home_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self.home_card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(17)
        segments = QWidget()
        segments.setObjectName('ModeSwitch')
        segment_layout = QHBoxLayout(segments)
        segment_layout.setContentsMargins(4, 4, 4, 4)
        segment_layout.setSpacing(4)
        self.mode_group = QButtonGroup(self)
        self.experiment_button = button('实验')
        self.test_button = button('测试')
        for control, mode in ((self.experiment_button, 'experiment'), (self.test_button, 'test')):
            control.setCheckable(True)
            control.setProperty('segment', True)
            self.mode_group.addButton(control)
            segment_layout.addWidget(control, 1)
            control.clicked.connect(lambda checked=False, value=mode: self.switch_workspace_mode(value))
        self.experiment_button.setChecked(True)
        layout.addWidget(segments)
        segments.setVisible(not RAW_CAPTURE)

        person_group = QVBoxLayout()
        person_group.setSpacing(8)
        person_label = label('人员', 'FieldGroupTitle')
        person_group.addWidget(person_label)
        person_row = QHBoxLayout()
        person_row.setSpacing(10)
        self.participant_combo = QComboBox()
        self.participant_combo.setAccessibleName('当前人员')
        self.participant_combo.setMinimumWidth(0)
        self.participant_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.participant_combo.setMinimumContentsLength(6)
        self.participant_combo.currentIndexChanged.connect(self.home_person_changed)
        person_label.setBuddy(self.participant_combo)
        person_row.addWidget(self.participant_combo, 1)
        self.new_person_button = button('新增', self.new_person)
        self.new_person_button.setAccessibleName('新增人员')
        person_row.addWidget(self.new_person_button)
        person_group.addLayout(person_row)
        layout.addLayout(person_group)

        scheme_group = QVBoxLayout()
        scheme_group.setSpacing(8)
        self.scheme_label = label('方案', 'FieldGroupTitle')
        scheme_group.addWidget(self.scheme_label)
        scheme_row = QHBoxLayout()
        scheme_row.setSpacing(10)
        self.scheme_combo = QComboBox()
        self.scheme_combo.setMinimumWidth(0)
        self.scheme_combo.setAccessibleName('实验方案')
        self.scheme_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scheme_combo.setMinimumContentsLength(6)
        self.scheme_combo.currentIndexChanged.connect(self.scheme_changed)
        self.scheme_label.setBuddy(self.scheme_combo)
        scheme_row.addWidget(self.scheme_combo, 1)
        self.scheme_settings_button = button('编辑', self.open_scheme_settings)
        self.scheme_settings_button.setAccessibleName('编辑实验方案')
        scheme_row.addWidget(self.scheme_settings_button)
        scheme_group.addLayout(scheme_row)
        layout.addLayout(scheme_group)

        self.start_button = button('开始实验', self.start_selected, primary=True)
        self.start_button.setObjectName('LaunchButton')
        self.start_button.setMinimumHeight(40)
        layout.addWidget(self.start_button)
        outer.addWidget(self.home_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(1)
        self.status = label('', wrap=True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFixedHeight(28)
        outer.addWidget(self.status)

        self.history_dialog = HistoryDialog(self.catalog, self)
        self.people_panel = self.history_dialog.people_panel
        self.records_panel = self.history_dialog.records_panel
        self.person_title = self.history_dialog.person_title
        self.person_details = self.history_dialog.person_details
        self.edit_person_button = self.history_dialog.edit_button
        self.analysis_button = self.records_panel.analyze_button
        self.people_panel.new_requested.connect(self.new_person)
        self.people_panel.selected.connect(self.person_selected)
        self.records_panel.analyze_requested.connect(lambda path: self.open_analysis(folder=path))
        self.records_panel.record_imported.connect(self.record_imported)
        self.history_dialog.mode_changed.connect(self.switch_workspace_mode)
        self.history_dialog.edit_requested.connect(self.edit_person)
        self.history_dialog.person_deleted.connect(self.person_deleted)
        self.history_dialog.select_requested.connect(self.return_to_home)

        self.participant_info = ParticipantInfo(self)
        self.participant_info.hide()
        self._legacy_controls = QWidget(self)
        self._legacy_controls.hide()
        for name in ('scheme_summary', 'processing_badge', 'analysis_link', 'record_folder_link'):
            control = label('')
            control.setParent(self._legacy_controls)
            setattr(self, name, control)
        self.refresh_schemes()
        self.people_panel.refresh()

    def home_person_changed(self):
        key = self.participant_combo.currentData()
        if key:
            self.people_panel.select_person(key)
        else:
            self.people_panel.select_person(None)

    def sync_person_selector(self, person):
        key = person.get('key') if person else None
        self.participant_combo.blockSignals(True)
        self.participant_combo.clear()
        self.participant_combo.addItem('', None)
        for entry in self.catalog.people():
            name = entry.get('name') or '未命名人员'
            identifier = entry.get('participant_id')
            title = f"{name}  ({identifier})" if identifier else name
            self.participant_combo.addItem(title.replace(chr(183), ' '), entry['key'])
        self.participant_combo.setCurrentIndex(max(0, self.participant_combo.findData(key)))
        self.participant_combo.blockSignals(False)

    def open_history(self):
        if self._closing or self._preparing or self.experiment is not None:
            return
        self.refresh_history()
        self.history_dialog.set_mode(self.workspace_mode)
        self.history_dialog.show()
        self.history_dialog.raise_()
        self.history_dialog.activateWindow()

    def return_to_home(self):
        self.history_dialog.hide()
        self.show()
        self.raise_()
        self.activateWindow()

    def history_roots(self):
        roots = []
        for mode in (('experiment',) if RAW_CAPTURE else ('experiment', 'test')):
            policy = processing_for_mode(self.settings, mode) or {}
            if policy.get('output_dir'):
                roots.append(policy['output_dir'])
        if self.last_folder is not None:
            roots.append(self.last_folder.parent)
        return roots

    def person_selected(self, person):
        self.current_person = person
        self.sync_person_selector(person)
        self.participant_info.set_values(person or {})
        self.person_title.setText((person.get('name') or person.get('participant_id') or '未命名人员') if person else '选择人员')
        details = []
        if person:
            if person.get('participant_id'):
                details.append('编号 ' + person['participant_id'])
            if person.get('sex'):
                details.append(person['sex'])
            if person.get('age') is not None:
                details.append(f"{person['age']} 岁")
        self.person_details.setText('    '.join(details))
        self.person_details.setVisible(bool(details))
        self.edit_person_button.setEnabled(bool(person))
        self.records_panel.set_context(person.get('key') if person else None, self.workspace_mode)

    def new_person(self):
        return self.person_dialog()

    def edit_person(self):
        return self.person_dialog(self.current_person)

    def person_deleted(self, key):
        self.refresh_history()
        self.status.setText('人员已删除，历史实验文件保留。')

    def person_dialog(self, person=None):
        dialog = QDialog(self)
        dialog.setWindowTitle('编辑人员' if person else '新增人员')
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 24)
        layout.setSpacing(24)
        fields = ParticipantInfo()
        fields.set_values(person or {})
        layout.addWidget(fields)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(button('取消', dialog.reject))
        def save_person():
            try:
                values = fields.values()
                if not values['name'] and not values['participant_id']:
                    raise ValueError('请填写姓名或编号。')
                saved = self.catalog.upsert_person(values)
                self.people_panel.select_person(saved['key'])
                dialog.accept()
            except (ValueError, OSError) as error:
                QMessageBox.warning(dialog, '人员信息', str(error))
        actions.addWidget(button('保存', save_person, primary=True))
        layout.addLayout(actions)
        return dialog.exec() == QDialog.DialogCode.Accepted

    def switch_workspace_mode(self, mode):
        if RAW_CAPTURE:
            return
        if self._closing or self._preparing or self.experiment is not None or self._pending_test_analysis or self._analysis_busy():
            self.experiment_button.setChecked(self.workspace_mode == 'experiment')
            self.test_button.setChecked(self.workspace_mode == 'test')
            self.history_dialog.set_mode(self.workspace_mode)
            return
        self.workspace_mode = mode
        self.experiment_button.setChecked(mode == 'experiment')
        self.test_button.setChecked(mode == 'test')
        title = '实验' if mode == 'experiment' else '测试'
        self.scheme_settings_button.setAccessibleName('编辑' + title + '方案')
        self.scheme_combo.setAccessibleName(title + '方案')
        self.start_button.setText('开始' + title)
        self.refresh_schemes()
        self.history_dialog.set_mode(mode)
        self.records_panel.set_context(self.current_person.get('key') if self.current_person else None, mode)

    def start_selected(self):
        if self._closing or self._preparing or self.experiment is not None or self._pending_test_analysis or self._analysis_busy():
            return
        if not self.current_person and not any(self.participant_info.values().get(key) for key in ('name', 'participant_id')):
            if not self.new_person():
                return
        self.history_dialog.hide()
        self._start_capture(self.selected_scheme(), self.workspace_mode)

    def refresh_history(self, *_):
        key = self.current_person.get('key') if self.current_person else None
        self.catalog.refresh(record_roots=self.history_roots())
        self.people_panel.refresh(selected_key=key)
        self.records_panel.refresh()

    def record_imported(self, path):
        entry = self.catalog.remember_record(path)
        self.people_panel.select_person(entry['person_key'])
        self.switch_workspace_mode(entry['mode'])
