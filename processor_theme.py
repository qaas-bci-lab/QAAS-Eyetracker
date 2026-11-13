"""Lavender styling for the standalone experiment processor and its dialogs."""

from PyQt6.QtCore import QEvent, QLibraryInfo, QObject, Qt, QTranslator
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QHeaderView, QLabel, QTreeView

from ui_symbols import icon
from window_chrome import install_chrome


PROCESSOR_STYLE = '''
QWidget { color:#252548; font-size:13px; }
QMainWindow, QWidget#ProcessorWorkspace {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #F4F2FF,stop:.51 #F0F0FD,stop:1 #E5E5FC);
}
QDialog { background:#F7F6FF; }
QLabel { background:transparent; }
QLabel#ProcessorTitle { color:#242348; font-size:28px; font-weight:700; }
QLabel#ProcessorSubtitle, QLabel#Muted { color:#625E7C; font-size:13px; }
QLabel#ProcessorHeading, QLabel#SectionTitle, QLabel#CardTitle { color:#252448; font-size:16px; font-weight:650; }
QLabel#ProcessorStatLabel { color:#252448; font-size:13px; font-weight:650; }
QLabel#ProcessorStatDetail, QLabel#ProcessorPath { color:#625E7C; font-size:12px; }
QLabel#ProcessorNavBrand { color:#29274D; font-size:17px; font-weight:700; }
QWidget#ProcessorTitleAccent {
    background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #9F98FF,stop:1 #7465DF);
    border-radius:4px;
}
QWidget#ProcessorSidebar, QWidget#ProcessorStats {
    background:qlineargradient(x1:0,y1:0,x2:.45,y2:1,stop:0 #FAF9FF,stop:.57 #F3F2FE,stop:1 #EDEBFD);
    border:1px solid #FFFFFF; border-radius:17px;
}
QWidget#ProcessorHero { background:transparent; }
QWidget#ProcessorCard {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #FCFBFF,stop:1 #F7F6FF);
    border:1px solid #FFFFFF; border-radius:15px;
}
QWidget#ProcessorStatRow, QWidget#ProcessorStatusBar {
    background:#EFEDFC; border:1px solid #F5F3FF; border-radius:11px;
}
QLabel#ProcessorBadge, QLabel#ProcessorStatValue {
    color:#5346B4; background:#F4F2FF; border:1px solid #DDD8FA; border-radius:13px;
    font-weight:650; padding:3px 8px;
}
QLabel#ProcessorStatValue { font-size:16px; }
QLabel#ProcessorGpuState { color:#59547A; background:#E8E5FA; border-radius:9px; padding:2px 7px; font-size:12px; }
QLabel#ProcessorGpuState[available="true"] { color:#236D48; background:#DDF2E4; }

QPushButton {
    min-height:31px; padding:0 10px; border:1px solid #D7D2EF; border-radius:9px;
    background:#FDFBFF; color:#29264D; font-size:13px; font-weight:550;
}
QPushButton:hover:enabled { background:#F0ECFF; border-color:#ABA0EE; color:#29264D; }
QPushButton:pressed:enabled { background:#E7E1FC; border-color:#8170DE; }
QPushButton:disabled { background:#EEEBF8; border-color:#E1DDF1; color:#79748E; }
QPushButton:focus, QPushButton[quiet="true"]:focus, QPushButton[primary="true"]:focus {
    border:2px solid #4635A8;
}
QPushButton[primary="true"], QDialogButtonBox QPushButton:default {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7466DD,stop:1 #5649B9);
    border:1px solid #6759CA; color:#FFFFFF; font-weight:650;
}
QPushButton[primary="true"]:hover:enabled, QDialogButtonBox QPushButton:default:hover:enabled {
    background:#5E4EC6; border-color:#5E4EC6; color:#FFFFFF;
}
QPushButton[primary="true"]:pressed:enabled { background:#4A3DA8; color:#FFFFFF; }
QPushButton[primary="true"]:disabled, QDialogButtonBox QPushButton:default:disabled {
    background:#E3DEFA; border-color:#DED8F7; color:#625994;
}
QPushButton[primary="true"]:focus, QDialogButtonBox QPushButton:default:focus {
    border:2px solid #382B95;
}
QPushButton[quiet="true"] { background:transparent; border-color:transparent; color:#625A82; padding:0 8px; }
QPushButton[quiet="true"]:hover:enabled { background:#EFEBFF; border-color:#DDD6FA; color:#28234B; }
QPushButton[quiet="true"]:disabled { color:#88809F; background:transparent; border-color:transparent; }
QPushButton[quiet="true"]:focus { border:2px solid #4635A8; }
QPushButton#ProcessorNav, QPushButton#ProcessorNavActive {
    min-height:39px; text-align:left; padding:0 10px; border-radius:11px;
}
QPushButton#ProcessorNav { background:transparent; border:1px solid transparent; color:#38335D; }
QPushButton#ProcessorNav:hover { background:#EFEBFF; border-color:#E6DFFA; }
QPushButton#ProcessorNavActive, QPushButton#ProcessorNav:checked {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7567DF,stop:1 #5749BE);
    border:1px solid #6D5FD5; color:#FFFFFF; font-weight:650;
}
QPushButton#ProcessorNav:focus, QPushButton#ProcessorNavActive:focus { border:2px solid #4635A8; }

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
    min-height:29px; background:#FEFDFF; border:1px solid #D8D3EF; border-radius:8px;
    color:#29264D; selection-background-color:#6758CD; selection-color:#FFFFFF;
}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color:#ADA2E6; }
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border:2px solid #8172E2; }
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background:#F0EDF9; border-color:#E2DDF1; color:#77708B;
}
QSpinBox, QDoubleSpinBox { padding-left:8px; padding-right:21px; }
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit, QComboBox QLineEdit {
    border:0; min-height:0; padding:0; background:transparent;
}
QComboBox QAbstractItemView {
    background:#FDFCFF; color:#29264D; border:1px solid #DAD4F2; border-radius:8px;
    selection-background-color:#E9E3FC; selection-color:#30265E;
}
QCheckBox { spacing:8px; min-height:24px; }
QCheckBox:focus { color:#4635A8; }

QTabWidget::pane { border:0; }
QTabBar::tab {
    padding:8px 11px; margin:0 4px 3px 0; border-bottom:2px solid transparent;
    background:transparent; color:#615B7B;
}
QTabBar::tab:hover { color:#4E3DB4; }
QTabBar::tab:selected { color:#29244F; border-bottom:3px solid #8271EC; font-weight:650; }
QTableWidget {
    background:#FBFAFF; alternate-background-color:#F8F6FF; border:1px solid #E4DEF5; border-radius:11px;
    gridline-color:#EBE7F8; selection-background-color:#E8E1FB; selection-color:#30265E;
}
QHeaderView { background:transparent; }
QHeaderView::section {
    padding:8px 7px; background:#EEECFA; color:#4A4567; border:0; font-size:12px; font-weight:600;
}
QTableWidget::item { padding:4px 5px; }
QAbstractItemView::item:hover:!selected { background:#F2EEFF; }
QProgressBar { min-height:8px; max-height:8px; background:#E6E1F8; border:0; border-radius:4px; }
QProgressBar::chunk {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #A091FF,stop:1 #6756CF);
    border-radius:4px;
}
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar:horizontal { background:transparent; height:10px; margin:2px; }
QScrollBar::handle { background:#C9C0EB; border-radius:4px; min-width:28px; min-height:28px; }
QScrollBar::handle:hover { background:#AA9BDD; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
QScrollBar::add-page, QScrollBar::sub-page { background:transparent; }
QSlider::groove:horizontal { border:0; border-radius:3px; height:5px; background:#E1DBF5; }
QSlider::handle:horizontal { background:#7766D6; border:1px solid #6453C2; width:12px; margin:-4px 0; border-radius:6px; }
QSlider::handle:horizontal:focus { border:2px solid #382B95; }
QToolTip { background:#FDFBFF; color:#29264D; border:1px solid #D8D3F0; border-radius:7px; padding:6px; }

QFileDialog QTreeView, QFileDialog QListView {
    background:#FCFBFF; alternate-background-color:#F7F4FF; border:1px solid #E3DDF5;
    border-radius:9px; selection-background-color:#E9E3FC; selection-color:#30265E;
}
QFileDialog QToolButton { color:#494077; background:transparent; border:1px solid transparent; border-radius:6px; }
QFileDialog QToolButton:hover { background:#EDE8FA; border-color:#DCD4F3; }
QFileDialog QToolButton:focus { border-color:#8172E2; }
'''


def style_processor_titlebar(window):
    """Restyle the existing draggable caption without changing its controls."""
    bar = getattr(window, '_app_titlebar', None)
    if bar is None:
        return
    bar.setStyleSheet('''
        TitleBar {
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #FAF9FF,stop:1 #ECE9FF);
            border-bottom:1px solid #E2DCF6;
        }
    ''')
    bar.layout().setContentsMargins(16, 0, 8, 0)
    for label in bar.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly):
        if label.pixmap().isNull():
            label.setStyleSheet('font-size:15px; font-weight:700; color:#2C2852; background:transparent;')
        else:
            label.setPixmap(icon('eye', color='#7567DD', size=28).pixmap(28, 28))


class _ProcessorDialogTheme(QObject):
    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Show and isinstance(widget, QDialog):
            ancestor = widget.parentWidget()
            while ancestor is not None:
                if ancestor is self.parent():
                    style_processor_titlebar(widget)
                    break
                ancestor = ancestor.parentWidget()
        return False


def install_processor_theme(window):
    """Apply the theme only to the processor and its descendants."""
    palette = window.palette()
    for role, color in (
        (QPalette.ColorRole.Window, '#F7F6FF'),
        (QPalette.ColorRole.Base, '#FCFBFF'),
        (QPalette.ColorRole.Highlight, '#7566D5'),
        (QPalette.ColorRole.HighlightedText, '#FFFFFF'),
        (QPalette.ColorRole.Text, '#252548'),
        (QPalette.ColorRole.WindowText, '#252548'),
    ):
        palette.setColor(role, QColor(color))
    window.setPalette(palette)
    window.setStyleSheet(PROCESSOR_STYLE)
    style_processor_titlebar(window)
    window._processor_dialog_theme = _ProcessorDialogTheme(window)
    QApplication.instance().installEventFilter(window._processor_dialog_theme)


def choose_source_directory(parent, initial):
    """Return an existing source directory selected in a themed Qt picker."""
    app = QApplication.instance()
    translator = QTranslator()
    translations = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    translated = translator.load('qtbase_zh_CN', translations) and app.installTranslator(translator)
    dialog = None
    try:
        dialog = QFileDialog(parent)
        dialog.setOptions(QFileDialog.Option.DontUseNativeDialog | QFileDialog.Option.ShowDirsOnly)
        dialog.setWindowTitle('选择原始数据文件夹')
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setViewMode(QFileDialog.ViewMode.Detail)
        dialog.setDirectory(str(initial))
        for field, label in (
            (QFileDialog.DialogLabel.LookIn, '位置'),
            (QFileDialog.DialogLabel.FileName, '文件夹'),
            (QFileDialog.DialogLabel.FileType, '文件类型'),
            (QFileDialog.DialogLabel.Accept, '选择'),
            (QFileDialog.DialogLabel.Reject, '取消'),
        ):
            dialog.setLabelText(field, label)
        for view in dialog.findChildren(QTreeView):
            header = view.header()
            if header.count() == 4:
                header.setStretchLastSection(False)
                header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                for column, width in ((1, 56), (2, 68), (3, 144)):
                    header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                    header.resizeSection(column, width)
        install_chrome(dialog, resizable=True)
        style_processor_titlebar(dialog)
        dialog.resize(640, 440)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            folders = dialog.selectedFiles()
            if folders:
                return folders[0]
        return ''
    finally:
        if dialog is not None:
            dialog.deleteLater()
        if translated:
            app.removeTranslator(translator)
