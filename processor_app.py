"""Standalone entry point for experiment processing, without camera acquisition."""
import os
os.environ['ADHD_EYETRACK_EDITION'] = 'processor'

import ctypes
import multiprocessing
from pathlib import Path
import sys
import traceback

from app_paths import DATA_ROOT, RESOURCE_ROOT, configure_cache_environment
configure_cache_environment()
if sys.stdout is None or sys.stderr is None:
    logs = DATA_ROOT / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    _log = (logs / 'processor.log').open('a', encoding='utf8', buffering=1)
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log


def run_batch_cli(argv):
    import argparse
    from processing_hardware import recommended_jobs, resolve_device, plan_execution
    from processor_service import process_batch, scan_sessions

    parser = argparse.ArgumentParser(description='批量处理眼动实验记录')
    parser.add_argument('--batch', required=True, type=Path, metavar='记录目录')
    parser.add_argument('--device', default='auto', help='auto、cpu、cuda 或 cuda:N')
    parser.add_argument('--jobs', type=int, choices=range(1, 9), metavar='1-8')
    parser.add_argument('--cpu-threads', type=int, metavar='总预算', help='CPU 推理线程总预算，自动按机器核数限制')
    args = parser.parse_args(argv)
    try:
        device = resolve_device(args.device)
        jobs = args.jobs if args.jobs is not None else recommended_jobs(device)
        folders = [row['path'] for row in scan_sessions(args.batch) if row['processable']]
        plan = plan_execution(device, min(jobs, max(1, len(folders))), args.cpu_threads)
        print(f"设备 {device}，并行 {plan['jobs']}，每路 {plan['threads_per_job']} 推理线程，"
              f"合计 {plan['cpu_threads_used']}，待处理 {len(folders)} 条", flush=True)
        rows = process_batch(folders, device=device, jobs=jobs,
                             cpu_threads=args.cpu_threads,
                             on_progress=lambda p, message: print(f'{p:3d}% {message}', flush=True))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    failed = [row for row in rows if row.get('status') in ('处理失败', '无有效坐标')]
    for row in failed:
        print(f"{row['path']}  {row.get('error', row['status'])}", file=sys.stderr)
    print(f'完成 {len(rows)} 条，需检查 {len(failed)} 条', flush=True)
    return int(bool(failed))


def main():
    multiprocessing.freeze_support()
    if '--self-check' in sys.argv:
        from processor_check import run
        output = Path(sys.argv[sys.argv.index('--self-check') + 1])
        sys.exit(run(output))
    if '--batch' in sys.argv[1:]:
        sys.exit(run_batch_cli(sys.argv[1:]))
    if sys.platform == 'win32':
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from processor_ui import ProcessorWindow
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app = QApplication(sys.argv)
    app.setApplicationName('ADHD实验数据处理')
    app.setWindowIcon(QIcon(str(RESOURCE_ROOT / 'assets/app.ico')))
    def exception_hook(kind, error, tb):
        traceback.print_exception(kind, error, tb)
        QMessageBox.critical(None, '处理出错', str(error))
    sys.excepthook = exception_hook
    window = ProcessorWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
