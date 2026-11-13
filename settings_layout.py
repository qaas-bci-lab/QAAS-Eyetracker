"""Compact settings navigation and grouped forms for the desktop dialogs."""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QFrame, QLabel, QFormLayout, QVBoxLayout, QPushButton, QButtonGroup


SETTINGS_STYLE = '''
QDialog#EyeSettings { background:#F5F5F5; }
QDialog#EyeSettings QWidget { font-size:14px; }
QDialog#EyeSettings QPushButton { min-height:32px; padding:0 10px; border-radius:8px; border:1px solid #B8B8B8; background:#FFFFFF; color:#272727; }
QDialog#EyeSettings QPushButton:hover:enabled { background:#EEEEEE; border-color:#888888; }
QDialog#EyeSettings QPushButton:pressed:enabled { background:#DADADA; border-color:#666666; }
QDialog#EyeSettings QPushButton:focus { border:2px solid #252525; }
QDialog#EyeSettings QPushButton:disabled { background:#F1F1F1; border-color:#D5D5D5; color:#999999; }
QDialog#EyeSettings QPushButton[primary="true"] { background:#252525; border-color:#252525; color:#FFFFFF; }
QDialog#EyeSettings QPushButton[primary="true"]:hover:enabled { background:#444444; border-color:#444444; }
QDialog#EyeSettings QPushButton[primary="true"]:pressed:enabled { background:#080808; border-color:#080808; }
QDialog#EyeSettings QPushButton[primary="true"]:disabled { background:#D2D2D2; border-color:#C5C5C5; color:#777777; }
QDialog#EyeSettings QComboBox,QDialog#EyeSettings QLineEdit,QDialog#EyeSettings QSpinBox,QDialog#EyeSettings QDoubleSpinBox { min-height:32px; border-radius:8px; }
QDialog#EyeSettings QCheckBox { min-height:24px; spacing:8px; }
QDialog#EyeSettings QLabel#CardTitle { font-size:15px; }
QDialog#EyeSettings QWidget#SettingsPage { background:#F5F5F5; }
QDialog#EyeSettings QFrame#SettingsNavigation { background:#F5F5F5; border:1px solid #DDDDDD; border-radius:12px; }
QDialog#EyeSettings QPushButton#SettingsNav { text-align:left; padding:0 12px; border:1px solid transparent; border-radius:8px; color:#666666; background:transparent; }
QDialog#EyeSettings QPushButton#SettingsNav:checked { background:#FFFFFF; border-color:#888888; color:#252525; font-weight:600; }
QDialog#EyeSettings QPushButton#SettingsNav:hover:!checked { background:#EAEAEA; border-color:#B8B8B8; }
QDialog#EyeSettings QPushButton#SettingsNav:pressed { background:#DADADA; border-color:#666666; }
QDialog#EyeSettings QFrame#SettingsGroup, QDialog#EyeSettings QFrame#Card { background:#FFFFFF; border:1px solid #DDDDDD; border-radius:12px; }
QDialog#EyeSettings QLabel#SettingsHeading { color:#272727; font-size:15px; font-weight:600; }
QDialog#EyeSettings QListWidget#SchemeList { background:#F5F5F5; border:1px solid #DDDDDD; border-radius:12px; padding:5px; }
QDialog#EyeSettings QListWidget#SchemeList::item { min-height:30px; padding:6px 8px; border-radius:8px; }
QDialog#EyeSettings QListWidget#SchemeList::item:selected { background:#EAEAEA; color:#272727; }
QDialog#EyeSettings QTabBar::tab { padding:6px 12px; margin-right:6px; }
'''


def settings_group(title):
    frame = QFrame()
    frame.setObjectName('SettingsGroup')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(10)
    heading = QLabel(title)
    heading.setObjectName('SettingsHeading')
    layout.addWidget(heading)
    form = QFormLayout()
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(8)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    layout.addLayout(form)
    return frame, form


def group_form_rows(source, sections):
    """Move existing rows into named sections without replacing any controls."""
    rows = []
    while source.rowCount():
        row = source.takeRow(0)
        rows.append(row)
    groups = {}
    forms = {}
    for key, title, _ in sections:
        frame, form = settings_group(title)
        groups[key], forms[key] = frame, form
        source.addRow(frame)
    for row in rows:
        caption = row.labelItem.widget() if row.labelItem is not None else None
        title = caption.text() if isinstance(caption, QLabel) else ''
        key = next((key for key, _, titles in sections if title in titles), sections[0][0])
        item = row.fieldItem
        field = item.widget() or item.layout()
        if caption is None:
            forms[key].addRow(field)
        else:
            forms[key].addRow(caption, field)
    source.setContentsMargins(0, 0, 0, 0)
    source.setSpacing(10)
    return groups


def settings_navigation(tabs):
    navigation = QFrame()
    navigation.setObjectName('SettingsNavigation')
    navigation.setFixedWidth(120)
    layout = QVBoxLayout(navigation)
    layout.setContentsMargins(5, 5, 5, 5)
    layout.setSpacing(5)
    navigation.button_group = QButtonGroup(navigation)
    navigation.button_group.setExclusive(True)
    buttons = []
    for index in range(tabs.count()):
        control = QPushButton(tabs.tabText(index))
        control.setObjectName('SettingsNav')
        control.setCheckable(True)
        control.setAutoDefault(False)
        control.setCursor(Qt.CursorShape.PointingHandCursor)
        control.setAccessibleName(tabs.tabText(index))
        navigation.button_group.addButton(control, index)
        control.clicked.connect(lambda checked=False, page=index: tabs.setCurrentIndex(page))
        layout.addWidget(control)
        buttons.append(control)
    layout.addStretch()
    def select(index):
        for position, control in enumerate(buttons):
            control.setChecked(position == index)
    tabs.currentChanged.connect(select)
    select(tabs.currentIndex())
    return navigation, buttons


def fit_settings_window(dialog):
    """Keep editor popups smaller than the host window and inside its screen."""
    parent = dialog.parentWidget()
    screen = parent.screen() if parent is not None else dialog.screen()
    available = screen.availableGeometry()
    width = min(dialog.width(), available.width() - 24)
    height = min(dialog.height(), available.height() - 24)
    if parent is not None and parent.isVisible():
        width = min(width, parent.width() - 32)
        height = min(height, parent.height() - 32)
    dialog.setMinimumSize(min(dialog.minimumWidth(), width), min(dialog.minimumHeight(), height))
    dialog.resize(QSize(width, height))
    center = parent.frameGeometry().center() if parent is not None else available.center()
    x = max(available.left(), min(center.x() - dialog.width() // 2, available.right() - dialog.width() + 1))
    y = max(available.top(), min(center.y() - dialog.height() // 2, available.bottom() - dialog.height() + 1))
    dialog.move(x, y)
