"""People navigation and mode-filtered recording history for the desktop workspace."""
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy,
)

from ui_components import button, label
from app_paths import RAW_CAPTURE

_KEEP_SELECTION = object()


def display_text(value):
    return str(value or '').replace(chr(183), ' ')


class _PeopleList(QListWidget):
    def focusInEvent(self, event):
        if self.currentItem() is not None:
            super().focusInEvent(event)
            return
        # Qt initializes the first current row on focus. Merely tabbing into
        # history must not assign a participant to the next recording.
        blocked = self.blockSignals(True)
        try:
            super().focusInEvent(event)
            self.setCurrentItem(None)
            self.clearSelection()
        finally:
            self.blockSignals(blocked)


class PeoplePanel(QWidget):
    selected = pyqtSignal(object)
    new_requested = pyqtSignal()

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self._selected_key = None
        self.setObjectName('PeoplePanel')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 20)
        layout.setSpacing(16)
        title_row = QHBoxLayout()
        title_row.addWidget(label('人员', 'CardTitle'))
        self.count = label('0')
        title_row.addStretch()
        title_row.addWidget(self.count)
        layout.addLayout(title_row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索姓名或编号')
        self.search.setAccessibleName('搜索人员')
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(40)
        layout.addWidget(self.search)
        self.list = _PeopleList()
        self.list.setObjectName('PeopleList')
        self.list.setAccessibleName('历史人员')
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSpacing(4)
        self.list.setStyleSheet('QListWidget { border: none; background: transparent; outline: none; } '
                               'QListWidget::item { padding: 14px 12px; border-radius: 8px; color: #272727; } '
                               'QListWidget::item:selected { background: #EAEAEA; color: #333333; } '
                               'QListWidget::item:hover:!selected { background: #F5F5F5; }')
        layout.addWidget(self.list, 1)
        self.new_button = button('新增人员', self.new_requested.emit)
        self.new_button.setMinimumHeight(42)
        layout.addWidget(self.new_button)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.list.currentItemChanged.connect(self._selection_changed)
        self.refresh()

    def refresh(self, selected_key=_KEEP_SELECTION):
        """Preserve an explicit selection; None clears it without choosing a person."""
        desired = self._selected_key if selected_key is _KEEP_SELECTION else selected_key
        query = self.search.text().strip().casefold()
        people = self.catalog.people()
        self.count.setText(str(len(people)))
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for person in people:
            if query and query not in (person.get('name', '') + ' ' + person.get('participant_id', '')).casefold():
                continue
            name = display_text(person.get('name')) or '未命名人员'
            identifier = display_text(person.get('participant_id'))
            detail = '编号 ' + identifier if identifier else str(person.get('count', 0)) + ' 次记录'
            item = QListWidgetItem(name + '\n' + detail)
            item.setData(Qt.ItemDataRole.UserRole, person)
            self.list.addItem(item)
            if person['key'] == desired:
                target = item
        self.list.setCurrentItem(target)
        self.list.blockSignals(False)
        self._selection_changed(target, None)

    def _selection_changed(self, current, previous):
        person = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._selected_key = person['key'] if person is not None else None
        self.selected.emit(person)

    def selected_person(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def select_person(self, key):
        # A just-added participant should remain visible regardless of a previous search.
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh(key)


class RecordsPanel(QWidget):
    analyze_requested = pyqtSignal(str)
    record_imported = pyqtSignal(str)

    def __init__(self, catalog, parent=None):
        super().__init__(parent)
        self.catalog = catalog
        self.person_key = None
        self.mode = 'experiment'
        self.rows = []
        self.setObjectName('RecordsPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title_row = QHBoxLayout()
        self.title = label('实验记录', 'CardTitle')
        title_row.addWidget(self.title)
        title_row.addStretch()
        self.count = label('0 次')
        title_row.addWidget(self.count)
        self.import_button = button('导入记录', self.import_record)
        title_row.addWidget(self.import_button)
        self.import_button.setVisible(not RAW_CAPTURE)
        layout.addLayout(title_row)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName('RecordsTable')
        self.table.setAccessibleName('人员历史记录')
        self.table.setHorizontalHeaderLabels(['时间', '方案', '状态', '原始视频', '存储位置'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(62)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 116)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 105)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 88)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(180)
        self.table.setStyleSheet('QTableWidget { border: none; background: #FFFFFF; outline: none; } '
                                'QHeaderView::section { background: #F5F5F5; color: #666666; padding: 12px 8px; border: none; } '
                                'QTableWidget::item { padding: 8px; border-bottom: 1px solid #DDDDDD; color: #272727; } '
                                'QTableWidget::item:selected { background: #EAEAEA; color: #333333; }')
        layout.addWidget(self.table, 1)
        self.empty = label('暂无实验记录')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        self.path_label = QLabel()
        self.path_label.setMinimumWidth(0)
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.path_label.setFixedHeight(24)
        self.path_label.setTextFormat(Qt.TextFormat.PlainText)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        action_row = QHBoxLayout()
        self.open_button = button('打开文件夹', self.open_folder)
        self.analyze_button = button('数据分析', self.analyze_selected, primary=True)
        action_row.addWidget(self.open_button)
        action_row.addStretch()
        action_row.addWidget(self.analyze_button)
        self.analyze_button.setVisible(not RAW_CAPTURE)
        layout.addLayout(action_row)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda _: self.open_folder() if RAW_CAPTURE else self.analyze_selected())
        self.refresh()

    def set_context(self, person_key, mode):
        self.person_key = person_key
        self.mode = mode
        self.refresh()

    def refresh(self):
        prior = self.selected_folder()
        self.rows = self.catalog.records(self.person_key, self.mode)
        self.title.setText('测试记录' if self.mode == 'test' else '实验记录')
        self.count.setText(f'{len(self.rows)} 次')
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        selected = 0
        for row_index, row in enumerate(self.rows):
            values = [row['date'].replace(' ', '\n'), row['scheme_name'], row['status'],
                      ('已保留' if row['has_raw'] else '未保留') if row['available'] else '不可用', row['path']]
            for column, text in enumerate(values):
                item = QTableWidgetItem(display_text(text))
                item.setToolTip(display_text(text))
                self.table.setItem(row_index, column, item)
            if prior == Path(row['path']):
                selected = row_index
        if self.rows:
            self.table.selectRow(selected)
        self.table.blockSignals(False)
        self.empty.setText('暂无测试记录' if self.mode == 'test' else '暂无实验记录')
        self.empty.setVisible(not self.rows)
        self._selection_changed()

    def selected_record(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def selected_folder(self):
        row = self.selected_record()
        return Path(row['path']) if row else None

    def _selection_changed(self):
        row = self.selected_record()
        available = bool(row and row['available'])
        self.open_button.setEnabled(available)
        self.analyze_button.setEnabled(available)
        self.analyze_button.setText('查看结果' if row and row['analyzed'] else '开始分析')
        self._full_path = display_text(row['path']) if row else ''
        self.path_label.setToolTip(self._full_path)
        self._fit_path()
        self.path_label.setVisible(bool(row))

    def _fit_path(self):
        self.path_label.setText(self.path_label.fontMetrics().elidedText(
            getattr(self, '_full_path', ''), Qt.TextElideMode.ElideMiddle, max(20, self.width()-8)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'path_label'):
            self._fit_path()

    def analyze_selected(self):
        row = self.selected_record()
        if not row or not row['available']:
            return
        if not (Path(row['path']) / 'session.json').is_file():
            QMessageBox.information(self, '记录位置不可用', '请连接保存数据的磁盘后重试。')
            return
        self.analyze_requested.emit(row['path'])

    def open_folder(self):
        folder = self.selected_folder()
        if folder and folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def import_record(self):
        chosen = QFileDialog.getExistingDirectory(self, '选择单次记录文件夹', str(self.catalog.root))
        if not chosen:
            return
        try:
            record = self.catalog.remember_record(chosen)
        except (OSError, ValueError, TypeError):
            QMessageBox.information(self, '无法导入', '请选择包含有效 session.json 的单次记录文件夹。')
            return
        self.set_context(record['person_key'], record['mode'])
        self.record_imported.emit(record['path'])
