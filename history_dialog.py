"""A separate personnel history window, leaving the home screen focused on starting."""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QMessageBox, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from participant_records import PeoplePanel, RecordsPanel, display_text
from ui_components import button, label
from window_chrome import install_chrome
from app_paths import RAW_CAPTURE


class HistoryDialog(QDialog):
    mode_changed = pyqtSignal(str)
    select_requested = pyqtSignal()
    edit_requested = pyqtSignal()
    person_deleted = pyqtSignal(str)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.mode = 'experiment'
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle('人员记录')
        self.resize(1080, 720)
        self.setMinimumSize(800, 540)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 16, 22, 20)
        outer.setSpacing(18)
        install_chrome(self, outer)
        header = QHBoxLayout()
        self.back_button = button('返回', self.hide, quiet=True)
        header.addWidget(self.back_button)
        header.addStretch()
        self.select_button = button('使用此人员', self.use_selected, primary=True)
        header.addWidget(self.select_button)
        outer.addLayout(header)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(16)
        self.splitter.setChildrenCollapsible(False)
        self.people_panel = PeoplePanel(catalog)
        self.people_panel.setMinimumWidth(215)
        self.people_panel.setMaximumWidth(310)
        self.delete_button = button('删除人员', self.delete_selected_person)
        self.delete_button.setAccessibleName('删除选中人员')
        self.people_panel.layout().addWidget(self.delete_button)
        self.splitter.addWidget(self.people_panel)
        content = QWidget()
        content.setObjectName('PersonWorkspace')
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 16, 16, 16)
        layout.setSpacing(18)
        identity = QHBoxLayout()
        self.person_title = label('选择人员', 'Title')
        self.person_title.setTextFormat(Qt.TextFormat.PlainText)
        self.person_title.setWordWrap(True)
        identity.addWidget(self.person_title, 1)
        self.edit_button = button('编辑人员', self.edit_requested.emit, quiet=True)
        identity.addWidget(self.edit_button)
        layout.addLayout(identity)
        self.person_details = label('')
        self.person_details.setTextFormat(Qt.TextFormat.PlainText)
        self.person_details.setWordWrap(True)
        layout.addWidget(self.person_details)

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
            control.setMinimumWidth(100)
            self.mode_group.addButton(control)
            segment_layout.addWidget(control)
            control.clicked.connect(lambda checked=False, value=mode: self._change_mode(value))
        mode_row = QHBoxLayout()
        mode_row.addWidget(segments)
        mode_row.addStretch()
        layout.addLayout(mode_row)
        segments.setVisible(not RAW_CAPTURE)
        self.records_panel = RecordsPanel(catalog)
        layout.addWidget(self.records_panel, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setWidget(content)
        self.splitter.addWidget(self.scroll)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([245, 770])
        outer.addWidget(self.splitter, 1)
        self.people_panel.selected.connect(self.show_person)
        self.show_person(self.people_panel.selected_person())
        self.set_mode('experiment')

    def show_person(self, person):
        name = (person.get('name') or person.get('participant_id') or '未命名人员') if person else '选择人员'
        self.person_title.setText(display_text(name))
        details = []
        if person:
            if person.get('participant_id'):
                details.append('编号 ' + str(person['participant_id']))
            if person.get('sex'):
                details.append(str(person['sex']))
            if person.get('age') is not None:
                details.append(f"{person['age']} 岁")
        self.person_details.setText(display_text('    '.join(details)))
        self.person_details.setVisible(bool(details))
        self.edit_button.setEnabled(bool(person))
        self.delete_button.setEnabled(bool(person))
        self.select_button.setEnabled(bool(person))
        self.records_panel.set_context(person.get('key') if person else None, self.mode)

    def set_mode(self, mode):
        """Synchronize from the home screen without sending a change back."""
        if RAW_CAPTURE:
            mode = 'experiment'
        if mode not in ('experiment', 'test'):
            raise ValueError('模式只能选择实验或测试。')
        self.mode = mode
        for control, value in ((self.experiment_button, 'experiment'), (self.test_button, 'test')):
            blocked = control.blockSignals(True)
            control.setChecked(mode == value)
            control.blockSignals(blocked)
        person = self.people_panel.selected_person()
        self.records_panel.set_context(person.get('key') if person else None, mode)

    def _change_mode(self, mode):
        changed = mode != self.mode
        self.set_mode(mode)
        if changed:
            self.mode_changed.emit(mode)

    def use_selected(self):
        if self.people_panel.selected_person() is not None:
            self.select_requested.emit()
            self.hide()

    def delete_selected_person(self):
        person = self.people_panel.selected_person()
        if person is None or not self.delete_button.isEnabled():
            return
        name = person.get('name') or '未命名人员'
        identifier = person.get('participant_id') or '未填写'
        confirm = QMessageBox(self)
        confirm.setWindowTitle('删除人员')
        confirm.setMinimumWidth(420)
        confirm.setIcon(QMessageBox.Icon.NoIcon)
        confirm.setTextFormat(Qt.TextFormat.PlainText)
        confirm.setText(f'删除“{name}”（编号：{identifier}）？')
        confirm.setInformativeText('该人员将从列表移除，历史实验文件保留。可通过原文件夹查看，之后也可新增人员。')
        paths = [row['path'] for row in self.catalog.records(person['key'])]
        if paths:
            confirm.setDetailedText('历史记录文件夹：\n' + '\n'.join(paths))
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirm.button(QMessageBox.StandardButton.Yes).setText('删除')
        confirm.button(QMessageBox.StandardButton.Cancel).setText('取消')
        for control in confirm.findChildren(QPushButton):
            if control not in (confirm.button(QMessageBox.StandardButton.Yes), confirm.button(QMessageBox.StandardButton.Cancel)):
                control.setText('记录位置')
                control.clicked.connect(lambda checked=False, target=control: target.setText('记录位置'))
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.catalog.delete_person(person['key'])
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '未能删除人员', str(error))
            return
        self.people_panel.refresh(selected_key=None)
        if removed:
            self.person_deleted.emit(person['key'])

    def reject(self):
        self.hide()
