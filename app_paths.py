"""Read-only application resources and persistent per-user experiment data.

Source checkouts keep their existing directory layout. New frozen installations
use the independent data directory selected in Setup. Earlier installations keep
their LocalAppData location. A portable frozen build writes beside its EXE in a
fixed data folder, independent of the temporary one-file extraction directory.
Capture uses EYE_TRACKING_DATA_DIR. Raw capture and the processor use
EYE_TRACKING_RAW_DATA_DIR and EYE_TRACKING_PROCESSOR_DATA_DIR respectively.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
SOURCE_ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', SOURCE_ROOT)).resolve()
APP_EDITION = os.environ.get('EYE_TRACKING_EDITION', os.environ.get('ADHD_EYETRACK_EDITION', 'capture')).strip()
if FROZEN and (RESOURCE_ROOT / 'installer_mode.json').is_file():
    _marker = json.loads((RESOURCE_ROOT / 'installer_mode.json').read_text(encoding='utf-8-sig'))
    APP_EDITION = _marker.get('edition', APP_EDITION)
_data_profile = APP_EDITION
if APP_EDITION == 'capture_cpu':
    APP_EDITION = 'capture'
_editions = {
    'capture': ('QAAS-Eyetracker', '眼动追踪数据', 'EYE_TRACKING_DATA_DIR', ''),
    'capture_cpu': ('QAAS-EyetrackerModel', '眼动追踪采集数据', 'EYE_TRACKING_DATA_DIR', ''),
    'capture_raw': ('QAAS-EyetrackerRaw', '眼动追踪原始采集数据', 'EYE_TRACKING_RAW_DATA_DIR', 'raw_capture_data'),
    'processor': ('QAAS-EyetrackerProcessor', '眼动追踪处理数据', 'EYE_TRACKING_PROCESSOR_DATA_DIR', 'processor_data'),
}
if APP_EDITION not in _editions:
    raise ValueError('未知的程序版本，请使用采集版或数据处理版。')
RAW_CAPTURE = APP_EDITION == 'capture_raw'
_product_folder, _portable_folder, _override_name, _source_folder = _editions[_data_profile]
# Existing installations and launch scripts can keep using their original data.
_legacy_paths = {
    'capture': ('ADHDEyeTracker', 'ADHD眼动数据', 'ADHD_EYETRACK_DATA_DIR'),
    'capture_cpu': ('ADHDEyeTrackerCPU', 'ADHD眼动数据_CPU特殊版', 'ADHD_EYETRACK_DATA_DIR'),
    'capture_raw': ('ADHDEyeCaptureRaw', 'ADHD眼动原始采集数据', 'ADHD_EYETRACK_RAW_DATA_DIR'),
    'processor': ('ADHDEyeProcessor', 'ADHD眼动处理数据', 'ADHD_EYEPROCESSOR_DATA_DIR'),
}
_legacy_product, _legacy_portable, _legacy_override = _legacy_paths[_data_profile]
_override = os.environ.get(_override_name, '').strip() or os.environ.get(_legacy_override, '').strip()


def _with_legacy_fallback(path: Path, legacy: Path) -> Path:
    return (legacy if not path.exists() and legacy.is_dir() else path).resolve()


def _local_data_root() -> Path:
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
    return _with_legacy_fallback(local / _product_folder / 'data', local / _legacy_product / 'data')


if _override:
    DATA_ROOT = Path(_override).expanduser().resolve()
elif FROZEN and (RESOURCE_ROOT / 'installer_mode.json').is_file():
    # Setup writes this installer-owned UTF-8 file beside the program EXE.
    # It is deliberately outside _MEIPASS and the selected data directory.
    _configuration = Path(sys.executable).resolve().parent / 'data_directory.txt'
    if _configuration.exists():
        try:
            _configured_path = _configuration.read_text(encoding='utf-8-sig').strip()
        except (OSError, UnicodeError) as exc:
            raise ValueError(f'无法读取安装版数据目录配置：{_configuration}') from exc
        if not _configured_path or not Path(_configured_path).is_absolute():
            raise ValueError('安装版数据目录配置必须是非空的绝对路径。请重新运行安装程序选择数据目录。')
        DATA_ROOT = Path(_configured_path).resolve()
    else:
        DATA_ROOT = _local_data_root()
elif FROZEN and (RESOURCE_ROOT / 'portable_mode.json').is_file():
    # The marker selects a mode, not an arbitrary path. Never trust directory
    # names from its contents, which could otherwise escape the EXE directory.
    _app_directory = Path(sys.executable).resolve().parent
    DATA_ROOT = _with_legacy_fallback(_app_directory / _portable_folder, _app_directory / _legacy_portable)
elif FROZEN:
    DATA_ROOT = _local_data_root()
else:
    DATA_ROOT = SOURCE_ROOT / _source_folder


def resource_path(*parts: str) -> Path:
    """Locate bundled assets; this function never creates a resource directory."""
    return RESOURCE_ROOT.joinpath(*parts)


def data_path(*parts: str) -> Path:
    """Locate user-owned settings, image schemes, sessions and caches."""
    return DATA_ROOT.joinpath(*parts)


def ensure_user_data(root: Path | None = None) -> Path:
    """Create writable directories and seed only the two shipped example files.

    Do not copy any settings, user images, recorded sessions or model weights.
    Existing examples are retained, including when the application is upgraded.
    ``root`` is a small test/portable seam; normal callers use DATA_ROOT.
    """
    target = DATA_ROOT if root is None else Path(root).resolve()
    if APP_EDITION == 'processor':
        (target / '.cache').mkdir(parents=True, exist_ok=True)
        return target
    for name in ('schemes', 'sessions', '.cache'):
        (target / name).mkdir(parents=True, exist_ok=True)
    for name in ('example.png', 'example.json'):
        source = resource_path('schemes', name)
        destination = target / 'schemes' / name
        if not source.is_file() or source.resolve() == destination.resolve():
            continue
        try:
            # Exclusive creation protects an existing user's example and makes
            # simultaneous first launches harmless.
            with source.open('rb') as src, destination.open('xb') as dst:
                shutil.copyfileobj(src, dst)
        except FileExistsError:
            pass
    return target


def configure_cache_environment() -> Path:
    """Call before importing matplotlib/MediaPipe; logs belong to the user too."""
    ensure_user_data()
    os.environ['MPLCONFIGDIR'] = str(data_path('.cache', 'matplotlib'))
    return DATA_ROOT
