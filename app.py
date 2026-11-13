"""Entry point for the PyQt6 scheme-driven eye-tracking application."""
import os,sys,ctypes,multiprocessing,traceback
from pathlib import Path
from app_paths import DATA_ROOT,RESOURCE_ROOT,configure_cache_environment
ROOT=DATA_ROOT
configure_cache_environment()
# Windowed executables have no attached console. Keep diagnostic output readable.
if sys.stdout is None or sys.stderr is None:
    logs=DATA_ROOT/'logs';logs.mkdir(parents=True,exist_ok=True)
    _log=(logs/'application.log').open('a',encoding='utf8',buffering=1)
    if sys.stdout is None:sys.stdout=_log
    if sys.stderr is None:sys.stderr=_log
if sys.platform=='win32':
    try:ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:pass
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from main_window import Main,Experiment
from capture import Worker
from ui_components import ImageBoard,configure_system_font

def main():
    multiprocessing.freeze_support()
    if '--self-check' in sys.argv:
        from packaged_check import run
        output=Path(sys.argv[sys.argv.index('--self-check')+1])
        sys.exit(run(output))
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    application=QApplication(sys.argv);application.setApplicationName('ADHD眼动实验')
    from PyQt6.QtGui import QIcon
    application.setWindowIcon(QIcon(str(RESOURCE_ROOT/'assets/app.ico')))
    def exception_hook(kind,error,tb):
        traceback.print_exception(kind,error,tb)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None,'程序出现错误',str(error)+'\n详细记录：'+str(DATA_ROOT/'logs/application.log'))
    sys.excepthook=exception_hook
    window=Main();window.show();sys.exit(application.exec())
if __name__=='__main__':main()
