"""Small repeatable installer builder UI, separate from the eye-tracking app.

Only Qt, the vector marks, and the standard-library build controller are loaded.
The actual project interpreter performs the build in a background process.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout, QWidget, QComboBox,
)

from build_tool import BuildConfig, run_build
from ui_symbols import brand_pixmap, icon


STYLE = '''
QWidget { color:#272727; font-size:14px; }
QDialog { background:#F5F5F5; }
QLabel { background:transparent; }
QLabel#Title { font-size:24px; font-weight:600; }
QLabel#Muted { color:#666666; }
QFrame#Card { background:#FFFFFF; border:1px solid #DDDDDD; border-radius:12px; }
QLineEdit,QComboBox { min-height:34px; border:1px solid #DDDDDD; border-radius:8px; padding:0 10px; background:#FFFFFF; selection-background-color:#252525; }
QLineEdit:focus,QComboBox:focus { border-color:#252525; }
QLineEdit:disabled,QComboBox:disabled { color:#999999; background:#F5F5F5; }
QComboBox::drop-down { width:28px; border:none; }
QPushButton { min-height:34px; border:1px solid #B8B8B8; border-radius:8px; padding:0 14px; background:#FFFFFF; color:#272727; }
QPushButton:hover:enabled { background:#EEEEEE; border-color:#888888; }
QPushButton:focus { border-color:#252525; }
QPushButton:pressed:enabled { background:#DADADA; border-color:#666666; }
QPushButton:disabled { color:#999999; background:#F1F1F1; border-color:#D5D5D5; }
QPushButton#Primary { background:#252525; border-color:#252525; color:white; font-weight:600; }
QPushButton#Primary:hover:enabled { background:#444444; border-color:#444444; }
QPushButton#Primary:pressed:enabled { background:#080808; border-color:#080808; }
QPushButton#Primary:disabled { background:#D2D2D2; border-color:#C5C5C5; color:#777777; }
QPushButton#Browse { padding:0 8px; }
QProgressBar { min-height:8px; max-height:8px; border:none; border-radius:4px; background:#DDDDDD; color:transparent; }
QProgressBar::chunk { border-radius:4px; background:#252525; }
QPlainTextEdit { border:1px solid #DDDDDD; border-radius:8px; background:#FFFFFF; padding:8px; font-size:13px; }
QComboBox QAbstractItemView { background:#FFFFFF; color:#272727; selection-background-color:#EAEAEA; }
'''


def tool_directory() -> Path:
    return Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent


def default_project() -> Path:
    nearby = tool_directory()
    if (nearby / 'app.py').is_file():
        return nearby
    return Path('G:/ADHD眼动程序')


def build_configs(project, output, selection='all', python=None):
    if selection not in ('capture','processor','all'):
        raise ValueError('请选择采集版、数据处理版或全部')
    editions = ('capture','processor') if selection == 'all' else (selection,)
    return [BuildConfig(Path(project), Path(output)/edition, python, edition) for edition in editions]


def batch_build(project=None, output=None, selection='all') -> int:
    """Build without initializing Qt, including when this tool is frozen."""
    project_dir = Path(project or default_project())
    output_dir = Path(output or project_dir / 'release_installer')
    destination = output_dir / 'tool_build_result.json'
    completed = []
    try:
        for config in build_configs(project_dir, output_dir, selection):
            result = run_build(config)
            completed.append(dict(edition=config.edition, exe_path=str(result.exe_path),
                                  manifest_path=str(result.manifest_path),log_path=str(result.log_path)))
        record = dict(ok=True, results=completed, **completed[-1])
    except Exception as exc:
        record = dict(ok=False, results=completed, error=str(exc) or type(exc).__name__)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        pending = destination.with_suffix('.json.pending')
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf8')
        os.replace(pending, destination)
    except OSError:
        return 1
    return 0 if record['ok'] else 1


def configure_appearance(app: QApplication) -> None:
    if sys.platform == 'win32':
        font_dir = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for name in ('msyh.ttc', 'simhei.ttf', 'simsun.ttc'):
            candidate = font_dir / name
            if candidate.is_file():
                QFontDatabase.addApplicationFont(str(candidate))
    families = set(QFontDatabase.families())
    preferred = [name for name in ('Microsoft YaHei UI', 'Microsoft YaHei',
                 'PingFang SC', 'Noto Sans CJK SC', 'SimHei', 'SimSun') if name in families]
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if preferred:
        font.setFamilies(preferred)
    font.setPointSize(11)
    app.setFont(font)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(brand_pixmap(64)))


def make_button(text: str, symbol: str, primary: bool = False) -> QPushButton:
    result = QPushButton(text)
    result.setIcon(icon(symbol, '#ffffff' if primary else '#666666', 20))
    result.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        result.setObjectName('Primary')
    return result


class BuildWorker(QThread):
    log = pyqtSignal(str)
    progress = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, config, parent=None, selection='all'):
        super().__init__(parent)
        self.config = config
        self.selection = selection

    def run(self):
        try:
            configs=build_configs(self.config.project_dir,self.config.output_dir,
                                  self.selection,self.config.python_exe)
            results=[]
            for index, config in enumerate(configs):
                title='采集版' if config.edition=='capture' else '数据处理版'
                def progress(value, message):
                    self.progress.emit(round((index*100+value)/len(configs)), title+' '+message)
                result = run_build(config, on_log=self.log.emit, on_progress=progress)
                results.append(result)
                self.log.emit(title+'已完成 '+str(result.exe_path))
        except Exception as exc:
            self.log.emit(traceback.format_exc())
            self.failed.emit(str(exc) or type(exc).__name__)
        else:
            self.succeeded.emit(results)


class PackagerWindow(QDialog):
    def __init__(self, project=None, output=None, config_path=None, edition=None):
        super().__init__()
        self.setWindowTitle('眼动程序打包工具')
        self.resize(720, 500)
        self.setMinimumSize(620, 470)
        self.config_path = Path(config_path) if config_path else tool_directory() / 'packager_settings.json'
        saved = self.load_preferences()
        chosen_project = Path(project or saved.get('project_dir') or default_project())
        saved_output = saved.get('output_dir')
        # Migrate only the previous default. Explicit/custom destinations belong
        # to the user and must keep working, regardless of their folder name.
        legacy_project = Path(saved.get('project_dir') or chosen_project)
        if not output and saved_output and Path(saved_output).resolve() == (legacy_project / 'release_single').resolve():
            saved_output = None
        chosen_output = Path(output or saved_output or chosen_project / 'release_installer')
        self._default_output = str(chosen_project / 'release_installer')
        self.worker = None
        self.last_result = None
        self.last_results = []
        self.last_log_path = None
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        head = QHBoxLayout()
        head.setSpacing(14)
        mark = QLabel()
        mark.setPixmap(brand_pixmap(48))
        mark.setFixedSize(48, 48)
        head.addWidget(mark)
        title = QLabel('制作安装包')
        title.setObjectName('Title')
        head.addWidget(title)
        head.addStretch()
        layout.addLayout(head)

        card = QFrame()
        card.setObjectName('Card')
        fields = QVBoxLayout(card)
        fields.setContentsMargins(22, 20, 22, 20)
        fields.setSpacing(12)
        self.project = QLineEdit(str(chosen_project))
        self.project.setAccessibleName('项目目录')
        self.output = QLineEdit(str(chosen_output))
        self.output.setAccessibleName('输出目录')
        self.edition = QComboBox()
        self.edition.setAccessibleName('打包版本')
        for title,value in [('全部','all'),('采集版','capture'),('数据处理版','processor')]:
            self.edition.addItem(title,value)
        self.edition.setCurrentIndex(max(0,self.edition.findData(edition or saved.get('edition') or 'all')))
        fields.addWidget(QLabel('打包版本'))
        fields.addWidget(self.edition)
        self.project_browse = self.path_row(fields, '项目目录', self.project, self.pick_project)
        self.output_browse = self.path_row(fields, '输出目录', self.output, self.pick_output)
        layout.addWidget(card)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName('打包进度')
        layout.addWidget(self.progress)
        self.status = QLabel('')
        self.status.setObjectName('Muted')
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(36)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        self.logs_button = make_button('查看日志', 'file')
        self.logs_button.clicked.connect(self.toggle_logs)
        actions.addWidget(self.logs_button)
        self.open_button = make_button('打开输出', 'folder')
        self.open_button.clicked.connect(self.open_output)
        actions.addWidget(self.open_button)
        actions.addStretch()
        self.build_button = make_button('开始打包', 'download', True)
        self.build_button.clicked.connect(self.start_build)
        actions.addWidget(self.build_button)
        layout.addLayout(actions)

        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(5000)
        self.logs.setMinimumHeight(180)
        self.logs.setAccessibleName('打包日志')
        self.logs.hide()
        layout.addWidget(self.logs, 1)
        self.project.editingFinished.connect(self.project_edited)

    @staticmethod
    def path_row(layout, title, editor, callback):
        layout.addWidget(QLabel(title))
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(editor, 1)
        browse = make_button('', 'folder')
        browse.setObjectName('Browse')
        browse.setAccessibleName('选择' + title)
        browse.setToolTip('选择' + title)
        browse.setFixedWidth(44)
        browse.clicked.connect(callback)
        row.addWidget(browse)
        layout.addLayout(row)
        return browse

    def load_preferences(self):
        try:
            data = json.loads(self.config_path.read_text(encoding='utf8'))
            if isinstance(data, dict):
                return {key: value for key, value in data.items()
                        if key in ('project_dir', 'output_dir','edition') and isinstance(value, str) and value.strip()}
        except (OSError, ValueError, TypeError):
            pass
        return {}

    def save_preferences(self):
        try:
            self.config_path.write_text(json.dumps({
                'project_dir': self.project.text().strip(),
                'output_dir': self.output.text().strip(),
                'edition':self.edition.currentData(),
            }, ensure_ascii=False, indent=2), encoding='utf8')
        except OSError as exc:
            self.append_log('未能保存路径偏好，本次打包仍可继续：' + str(exc))

    def project_edited(self):
        new_default = str(Path(self.project.text().strip()) / 'release_installer')
        if not self.output.text().strip() or self.output.text().strip() == self._default_output:
            self.output.setText(new_default)
        self._default_output = new_default

    def pick_project(self):
        selected = QFileDialog.getExistingDirectory(self, '选择眼动程序项目', self.project.text())
        if selected:
            self.project.setText(selected)
            self.project_edited()

    def pick_output(self):
        selected = QFileDialog.getExistingDirectory(self, '选择输出目录', self.output.text())
        if selected:
            self.output.setText(selected)

    @property
    def busy(self):
        return self._busy

    def set_busy(self, busy):
        self._busy = busy
        for widget in (self.project, self.output, self.project_browse, self.output_browse, self.build_button,self.edition):
            widget.setEnabled(not busy)
        self.build_button.setText('正在打包' if busy else '开始打包')

    def append_log(self, text):
        self.logs.appendPlainText(str(text).replace(chr(183), ' '))

    def start_build(self):
        if self.busy:
            return
        if not self.project.text().strip() or not self.output.text().strip():
            self.status.setText('请填写项目目录和输出目录。')
            return
        self.logs.clear()
        self.save_preferences()
        self.last_result = None
        self.last_results = []
        self.progress.setValue(0)
        self.status.setText('正在检查项目和打包环境。')
        try:
            config = BuildConfig(project_dir=Path(self.project.text().strip()),
                                 output_dir=Path(self.output.text().strip()))
        except Exception as exc:
            self.failed(str(exc))
            return
        self.set_busy(True)
        self.worker = BuildWorker(config, self, selection=self.edition.currentData())
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_progress)
        self.worker.succeeded.connect(self.succeeded)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(lambda: self.set_busy(False))
        self.worker.start()

    def on_progress(self, percent, message):
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status.setText(str(message).replace(chr(183), ' '))

    def succeeded(self, results):
        self.last_results = results
        self.last_result = results[-1]
        self.last_log_path = Path(results[-1].log_path)
        self.progress.setValue(100)
        self.status.setText('两个安装包已生成' if len(results)>1 else '安装包已生成：' + str(results[0].exe_path))
        for result in results:
            self.append_log('安装包：' + str(result.exe_path))
            self.append_log('构建清单：' + str(result.manifest_path))
            self.append_log('完整日志：' + str(result.log_path))

    def failed(self, message):
        self.status.setText('打包失败：' + str(message).replace(chr(183), ' '))
        self.append_log(self.status.text())
        self.logs.show()
        self.logs_button.setText('收起日志')

    def toggle_logs(self):
        visible = self.logs.isHidden()
        self.logs.setVisible(visible)
        self.logs_button.setText('收起日志' if visible else '查看日志')
        if visible and self.height() < 650:
            self.resize(self.width(), 680)

    def open_output(self):
        folder = Path(self.output.text().strip())
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))
        else:
            self.status.setText('输出目录将在开始打包后创建。')

    def closeEvent(self, event):
        if self.busy:
            self.status.setText('正在打包，请等待完成后再关闭。')
            event.ignore()
        else:
            event.accept()

    def reject(self):
        # QDialog routes Esc directly to reject(), bypassing closeEvent().
        if self.busy:
            self.status.setText('正在打包，请等待完成后再关闭。')
            return
        super().reject()


def main(argv=None):
    parser = argparse.ArgumentParser(description='重复生成眼动程序安装包')
    parser.add_argument('--project')
    parser.add_argument('--output')
    parser.add_argument('--edition',choices=('capture','processor','all'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--self-check', metavar='JSON')
    mode.add_argument('--build', action='store_true', help='无界面打包并写入tool_build_result.json')
    args = parser.parse_args(argv)
    if args.build:
        return batch_build(args.project, args.output,args.edition or 'all')
    if args.self_check:
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    application = QApplication([sys.argv[0]])
    application.setApplicationName('ADHD打包工具')
    configure_appearance(application)
    # A self-check never reads or writes a user's persisted builder preferences.
    config_path = Path(args.self_check).with_suffix('.unused-settings.json') if args.self_check else None
    window = PackagerWindow(args.project, args.output, config_path=config_path,edition=args.edition)
    if args.self_check:
        target = Path(args.self_check).resolve()
        result = {'ok': False, 'checks': []}
        try:
            window.show()
            application.processEvents()
            assert not window.busy and window.worker is None
            assert window.build_button.isEnabled()
            assert not window.build_button.icon().isNull()
            assert window.project.text() and window.output.text()
            assert [window.edition.itemData(i) for i in range(window.edition.count())]==['all','capture','processor']
            assert window.edition.currentData() in ('all','capture','processor')
            assert window.logs.isHidden() and window.logs.isReadOnly()
            assert QFontMetrics(application.font()).inFontUcs4(ord('眼'))
            target.parent.mkdir(parents=True, exist_ok=True)
            assert window.grab().save(str(target.with_suffix('.png')))
            result.update(ok=True, checks=['Standalone builder UI', 'Chinese font',
                          'Project/output/edition selection', 'Separate sequential builds', 'No build or camera access'],
                          project=window.project.text(), output=window.output.text())
        except Exception:
            result['error'] = traceback.format_exc()
        finally:
            window.close()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        return 0 if result['ok'] else 1
    window.show()
    return application.exec()


if __name__ == '__main__':
    sys.exit(main())
