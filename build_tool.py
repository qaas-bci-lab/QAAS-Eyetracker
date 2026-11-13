"""Repeatable, verified Windows installers. This module uses only the stdlib.

The GUI may itself be frozen. Builds always use the project's Python environment,
not sys.executable of the frozen tool. User recordings/settings are never bundled.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

APP_NAME = 'QAAS-Eyetracker'
APP_EXE_NAME = APP_NAME
INSTALLER_NAME = APP_NAME + '_安装包'
APP_ID = '{971AAC36-AD4D-4752-B386-746221CD9711}'
EDITIONS = {
    'capture': dict(name=APP_NAME, display_name=APP_NAME, exe_name=APP_EXE_NAME,
                    app_id=APP_ID, entry='app.py', directory=APP_NAME,
                    data_name='眼动追踪数据', data_env='EYE_TRACKING_DATA_DIR',
                    legacy_title='ADHD 眼动实验', legacy_directory='ADHDEyeTracker',
                    legacy_exe='ADHD EyeTracker'),
    'capture_cpu': dict(name=APP_NAME + '_模型采集版', display_name=APP_NAME + ' 模型采集版',
                        exe_name=APP_NAME + ' Model', app_id='{E27AE1E0-795B-5209-BA02-CA31CADAE85E}',
                        entry='app.py', directory=APP_NAME + 'Model',
                        data_name='眼动追踪采集数据', data_env='EYE_TRACKING_DATA_DIR',
                        legacy_title='ADHD 眼动实验 CPU 特殊版', legacy_directory='ADHDEyeTrackerCPU',
                        legacy_exe='ADHD EyeTracker CPU'),
    'capture_raw': dict(name=APP_NAME + '_纯采集版', display_name=APP_NAME + ' 纯采集版',
                        exe_name=APP_NAME + ' Raw', app_id='{80972204-EE5F-4119-9906-60E5B66E03B0}',
                        entry='raw_capture_app.py', directory=APP_NAME + 'Raw',
                        data_name='眼动追踪原始采集数据', data_env='EYE_TRACKING_RAW_DATA_DIR',
                        legacy_title='ADHD 眼动实验 纯采集', legacy_directory='ADHDEyeCaptureRaw',
                        legacy_exe='ADHD EyeCapture Raw'),
    'processor': dict(name=APP_NAME + '_数据处理端', display_name=APP_NAME + ' 数据处理端',
                      exe_name=APP_NAME + ' Processor', app_id='{4B3D5B5F-EC9E-4B05-B2B9-0BFA939B7162}',
                      entry='processor_app.py', directory=APP_NAME + 'Processor',
                      data_name='眼动追踪处理数据', data_env='EYE_TRACKING_PROCESSOR_DATA_DIR',
                      legacy_title='ADHD眼动数据处理', legacy_directory='ADHDEyeProcessor',
                      legacy_exe='ADHD EyeProcessor'),
}
OWNER = 'EyeTracker installer workspace v1'
LEGACY_OWNER = 'ADHD single-file builder workspace v1'
EXCLUDE_DIRS = {'.git', '.venv', '__pycache__', '.cache', '.tmp', '.idea',
                'sessions', 'calibrations', 'schemes', 'backups', 'build', 'dist',
                'release', 'release_single', 'release_installer', '安装包', 'data',
                'processing_results', '.packager'}
NON_APP_MODULES = {'packager_app', 'build_tool', 'download_models', 'selftest',
                   'tracker', 'provision_bundle', 'provision_affnet'}


@dataclass(frozen=True)
class BuildConfig:
    project_dir: Path
    output_dir: Path
    python_exe: Path | None = None
    edition: str = 'capture'


@dataclass(frozen=True)
class BuildResult:
    exe_path: Path
    manifest_path: Path
    log_path: Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def edition_info(edition: str) -> dict:
    if edition not in EDITIONS:
        raise ValueError('请选择采集版或数据处理版')
    return EDITIONS[edition]


def verify_source_hashes(snapshot: Path, expected: dict[str, str]):
    for relative, digest in expected.items():
        path = snapshot / relative
        if not path.is_file() or sha256(path) != digest:
            raise RuntimeError('构建源文件已改变：' + relative)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(pending, path)


@contextmanager
def build_lock(workspace: Path):
    if workspace.is_symlink() or (hasattr(workspace, 'is_junction') and workspace.is_junction()):
        raise RuntimeError('打包工作目录不能是链接或联接。')
    workspace.mkdir(parents=True, exist_ok=True)
    owner = workspace / 'owner.txt'
    if owner.exists():
        if owner.read_text(encoding='utf8') not in (OWNER, LEGACY_OWNER):
            raise RuntimeError('打包工作目录标记不匹配，请选择其他输出目录。')
    else:
        # Do not claim a pre-existing directory containing somebody else's files.
        if any(workspace.iterdir()):
            raise RuntimeError('输出目录中的.packager已有其他文件，请选择其他输出目录。')
        owner.write_text(OWNER, encoding='utf8')
    with (workspace / 'build.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('此输出目录已有打包任务，请等待它完成。') from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def reset_owned_work(workspace: Path) -> Path:
    """Delete only this tool's previous build directory after verifying ownership."""
    workspace = workspace.resolve()
    if (workspace / 'owner.txt').read_text(encoding='utf8') not in (OWNER, LEGACY_OWNER):
        raise RuntimeError('工作目录归属检查失败。')
    current = workspace / 'current'
    if current.exists():
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()) or current.resolve().parent != workspace:
            raise RuntimeError('工作目录指向了其他位置，已停止清理。')
        marker = current / 'owner.txt'
        if not marker.is_file() or marker.read_text(encoding='utf8') not in (OWNER, LEGACY_OWNER):
            raise RuntimeError('旧打包目录缺少归属标记，已保留其中全部文件。')
        shutil.rmtree(current)
    current.mkdir()
    (current / 'owner.txt').write_text(OWNER, encoding='utf8')
    return current


def child_environment(temp_dir: Path) -> dict:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('_PYI_') or key in {
            'PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH',
            'EYE_TRACKING_EDITION', 'EYE_TRACKING_DATA_DIR', 'EYE_TRACKING_RAW_DATA_DIR',
            'EYE_TRACKING_PROCESSOR_DATA_DIR',
            'QML2_IMPORT_PATH', 'QML_IMPORT_PATH', 'ADHD_EYETRACK_DATA_DIR',
            'ADHD_EYEPROCESSOR_DATA_DIR', 'ADHD_EYETRACK_RAW_DATA_DIR', 'ADHD_EYETRACK_EDITION',
        }:
            env.pop(key, None)
    bundle = str(getattr(sys, '_MEIPASS', ''))
    if bundle:
        env['PATH'] = os.pathsep.join(p for p in env.get('PATH', '').split(os.pathsep)
                                      if not p.lower().startswith(bundle.lower()))
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               PYTHONNOUSERSITE='1', PYINSTALLER_RESET_ENVIRONMENT='1',
               TEMP=str(temp_dir), TMP=str(temp_dir), MPLCONFIGDIR=str(temp_dir / 'matplotlib'))
    return env


def spawn(args, **kwargs):
    """Prevent the frozen tool's Qt/DLL search directory leaking to Python."""
    reset_dll = os.name == 'nt' and bool(getattr(sys, 'frozen', False))
    if reset_dll:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        return subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                **kwargs)
    finally:
        if reset_dll:
            ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))


def select_python(project: Path, requested: Path | None, env: dict) -> Path:
    candidates = [requested] if requested else [project / '.venv/Scripts/python.exe',
                    project.with_name(project.name + '_开发') / '.venv/Scripts/python.exe']
    if not getattr(sys, 'frozen', False):
        candidates.append(Path(sys.executable))
    for candidate in dict.fromkeys(p for p in candidates if p):
        candidate = Path(candidate).resolve()
        if not candidate.is_file():
            continue
        proc = spawn([str(candidate), '-B', '-c',
                      "import importlib.util; assert importlib.util.find_spec('PyInstaller')"],
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.communicate(); continue
        if proc.returncode == 0:
            return candidate
    raise RuntimeError('未找到含PyInstaller的Python环境。请保留项目的.venv及开发副本，'
                       '或在项目环境安装PyInstaller后重试。')


def select_iscc(project: Path) -> Path:
    candidates = [project / 'packaging/tools/inno/ISCC.exe',
                  project.with_name(project.name + '_开发') / 'packaging/tools/inno/ISCC.exe']
    if getattr(sys, '_MEIPASS', None):
        candidates.insert(0, Path(sys._MEIPASS) / 'inno/ISCC.exe')
    for key in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]) / 'Inno Setup 6/ISCC.exe')
    found = shutil.which('ISCC.exe')
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise RuntimeError('未找到Inno Setup安装包编译器，请保留打包工具EXE或开发副本中的编译器。')


def project_logo(project: Path) -> Path | None:
    """The editable logo has one preferred location; older projects still work."""
    for path in (project / 'logo/logo.png', project / 'assets/logo.png'):
        if path.is_file():
            return path
    return None


def prepare_brand_assets(resources: Path, python: Path, env: dict,
                         logo: Path | None = None) -> dict[str, str]:
    """Generate the icon with the build interpreter, keeping this module stdlib-only.

    Only the resource snapshot is changed. Source PNGs and any manually supplied
    source ICO are left intact; a logo change is reflected in both Setup and app.
    """
    logo = Path(logo) if logo is not None else resources / 'assets/logo.png'
    if not logo.is_file():
        return {}
    assets = resources / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    destination = assets / 'logo.png'
    if logo.resolve() != destination.resolve():
        shutil.copy2(logo, destination)
    icon = assets / 'app.ico'
    script = '''from pathlib import Path
import sys
from PIL import Image, ImageOps
source, target = map(Path, sys.argv[1:])
with Image.open(source) as opened:
    image = ImageOps.exif_transpose(opened).convert('RGBA')
image.thumbnail((256, 256), Image.Resampling.LANCZOS)
canvas = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
canvas.alpha_composite(image, ((256-image.width)//2, (256-image.height)//2))
canvas.save(target, format='ICO', sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
'''
    proc = spawn([str(python), '-B', '-c', script, str(destination), str(icon)],
                 env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                 text=True, encoding='utf8', errors='replace')
    try:
        details, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired as exc:
        proc.kill(); proc.communicate()
        raise RuntimeError('生成logo图标超时，请检查PNG文件。') from exc
    if proc.returncode != 0 or not icon.is_file():
        raise RuntimeError('无法生成logo图标，请确认logo/logo.png为有效图片，'
                           '且构建Python环境已安装Pillow。\n' + details.strip())
    return {'assets/logo.png': sha256(destination), 'assets/app.ico': sha256(icon)}


def snapshot_project(project: Path, target: Path, edition: str = 'capture') -> dict[str, str]:
    """Take a consistent, small code/resource snapshot; exclude all personal data."""
    target.mkdir(parents=True)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo', '.git', '.cache')
    for source in project.iterdir():
        if source.is_file() and source.suffix.lower() in {'.py', '.pyw'}:
            if source.stem.startswith('test_') or source.stem in NON_APP_MODULES:
                continue
            shutil.copy2(source, target / source.name)
        elif source.is_dir() and source.name not in EXCLUDE_DIRS:
            if source.name == 'models':
                if edition == 'capture_raw':
                    verify_models(project)
                    model, = json.loads((source / 'manifest.json').read_text(encoding='utf-8-sig'))
                    write_json(target / 'capture_model_identity.json', dict(
                        model_id=model['id'], model_sha256=model['files']['backbone']['sha256'],
                        runtime_sha256=model['files']['torchscript']['sha256']))
                    continue
                destination = target / 'models'
                destination.mkdir()
                if (source / 'manifest.json').is_file():
                    entries = json.loads((source / 'manifest.json').read_text(encoding='utf-8-sig'))
                    write_json(destination / 'manifest.json', [entry for entry in entries if entry.get('id') == 'affnet_mixed'])
                for relative in ('face_landmarker.task', 'affnet_mixed/backbone.pt', 'affnet_mixed/model_cpu.ts'):
                    original = source / relative
                    if original.is_file():
                        copied = destination / relative
                        copied.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(original, copied)
            elif source.name == 'vendor':
                # Retain attribution but omit the retired FAZE runtime package.
                if (source / 'LICENSE').is_file():
                    (target / 'vendor').mkdir()
                    shutil.copy2(source / 'LICENSE', target / 'vendor/LICENSE')
            elif source.name in {'assets', 'licenses'} or (source / '__init__.py').is_file():
                shutil.copytree(source, target / source.name, ignore=ignore)
    logo = project_logo(project)
    if logo is not None:
        (target / 'assets').mkdir(exist_ok=True)
        shutil.copy2(logo, target / 'assets/logo.png')
    for filename in ('README.md', 'requirements-lock.txt', 'requirements.txt'):
        if (project / filename).is_file():
            shutil.copy2(project / filename, target / filename)
    (target / 'schemes').mkdir()
    for filename in ('example.png', 'example.json'):
        source = project / 'schemes' / filename
        if not source.is_file():
            raise RuntimeError(f'缺少默认资源：schemes/{filename}')
        shutil.copy2(source, target / 'schemes' / filename)
    (target / 'packaging').mkdir()
    for name in ('installer_template.iss', 'ChineseSimplified.isl'):
        source = project / 'packaging' / name
        if not source.is_file() and getattr(sys, '_MEIPASS', None):
            source = Path(sys._MEIPASS) / 'installer_resources' / name
        if source.is_file():
            shutil.copy2(source, target / 'packaging' / name)
    write_json(target / 'installer_mode.json', {'mode': 'installed', 'edition': edition,
                                               'app_id': edition_info(edition)['app_id']})
    return {path.relative_to(target).as_posix(): sha256(path)
            for path in sorted(target.rglob('*')) if path.is_file()}


def verify_models(snapshot: Path):
    manifest = json.loads((snapshot / 'models/manifest.json').read_text(encoding='utf8'))
    ids={m['id'] for m in manifest}
    if ids != {'affnet_mixed'} or len(manifest) != 1:
        raise RuntimeError('安装包只能包含 AFF-Net 均衡混合模型。')
    if set(manifest[0].get('files', {})) != {'backbone', 'torchscript'}:
        raise RuntimeError('AFF-Net 模型清单必须包含权重和推理文件。')
    expected_paths = {'models/affnet_mixed/backbone.pt', 'models/affnet_mixed/model_cpu.ts'}
    if {item.get('path') for item in manifest[0]['files'].values()} != expected_paths:
        raise RuntimeError('AFF-Net 模型资源路径不正确。')
    allowed = expected_paths | {'models/manifest.json', 'models/face_landmarker.task'}
    if any(path.relative_to(snapshot).as_posix() not in allowed for path in (snapshot / 'models').rglob('*') if path.is_file()):
        raise RuntimeError('安装包中包含不支持的模型资源。')
    for model in manifest:
        for item in model['files'].values():
            path = (snapshot / item['path']).resolve()
            if not path.is_relative_to(snapshot.resolve()) or not path.is_file():
                raise RuntimeError('模型路径无效：' + item['path'])
            if sha256(path) != item['sha256']:
                raise RuntimeError('模型校验失败：' + item['path'])
    if not (snapshot / 'models/face_landmarker.task').is_file():
        raise RuntimeError('缺少人脸关键点模型。')


def make_spec(snapshot: Path, version: str, edition: str = 'capture') -> Path:
    app = edition_info(edition)
    raw = edition == 'capture_raw'
    lsl = edition != 'processor'
    hidden = []
    if not raw:
        hidden += [p.stem for p in snapshot.glob('*.py') if p.stem not in
                   ('app', 'processor_app', 'raw_capture_app', 'raw_capture_check')]
    raw_excludes = ['torch', 'torchvision', 'torchaudio', 'mediapipe', 'matplotlib', 'scipy',
                    'analysis_ui', 'model_runtime', 'affnet_tracker', 'offline', 'reporting',
                    'processor_app', 'processor_service', 'packaged_check'] if raw else []
    # Hidden-import every local package too, so future new Python packages work.
    packages = [p.name for p in snapshot.iterdir() if p.is_dir() and (p / '__init__.py').is_file()]
    script = f'''from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata, collect_submodules
project=Path({str(snapshot)!r})
datas=[]
for name in ['models','assets','vendor','licenses']:
    if (project/name).is_dir(): datas.append((str(project/name),name))
for name in ['example.png','example.json']:
    datas.append((str(project/'schemes'/name),'schemes'))
for name in ['README.md','installer_mode.json','capture_model_identity.json']:
    if (project/name).is_file(): datas.append((str(project/name),'.'))
if not {raw!r}:
    datas+=collect_data_files('mediapipe',includes=['**/*.tflite','**/*.binarypb','**/*.pbtxt'])
for package in {(['numpy', 'PyQt6'] if raw else ['torch','mediapipe','numpy','matplotlib','PyQt6'])!r}:
    datas+=copy_metadata(package)
lsl_binaries=collect_dynamic_libs('pylsl') if {lsl!r} else []
if {lsl!r}:
    if not any(Path(path).name.lower() == 'lsl.dll' for path, _ in lsl_binaries):
        raise RuntimeError('The Windows pylsl wheel must provide pylsl/lib/lsl.dll')
    datas+=copy_metadata('pylsl')
hidden={hidden!r}
for package in {packages!r}: hidden+=collect_submodules(package)
a=Analysis([str(project/{app['entry']!r})],pathex=[str(project),str(project/'vendor')],
    binaries=lsl_binaries+([] if {raw!r} else collect_dynamic_libs('mediapipe')),datas=datas,hiddenimports=hidden,
    hookspath=[],hooksconfig={{'matplotlib':{{'backends':['Agg']}}}},
    excludes=['PySide6','PySide2','PyQt5','tkinter','IPython','pytest','notebook','jupyter','tensorboard','tensorflow']+{raw_excludes!r}+([] if {lsl!r} else ['pylsl']),
    noarchive=False)
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in {{'icuuc.dll','icudt78.dll'}}]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name={app['exe_name']!r},
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    icon=str(project/'assets/app.ico'),version=str(project/'version_info.txt'),
    runtime_tmpdir=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name={app['exe_name']!r})
'''
    parts = tuple(int(part) for part in version.split('.'))
    info = f'''VSVersionInfo(ffi=FixedFileInfo(filevers={parts!r},prodvers={parts!r},mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('080404b0',[StringStruct('FileDescription',{app['display_name']!r}),StringStruct('FileVersion',{version!r}),StringStruct('ProductName',{app['display_name']!r}),StringStruct('ProductVersion',{version!r})])]),VarFileInfo([VarStruct('Translation',[2052,1200])])])'''
    (snapshot / 'version_info.txt').write_text(info, encoding='utf8')
    spec = snapshot / (app['directory'] + 'Installed.spec')
    spec.write_text(script, encoding='utf8')
    return spec


def installer_command(iscc: Path, snapshot: Path, bundle: Path, output: Path,
                      version: str, *, app_id=None, output_name=None, edition='capture') -> list[str]:
    app = edition_info(edition)
    app_id = app_id or app['app_id']
    output_name = output_name or app['name'] + '_安装包'
    template = snapshot / 'packaging/installer_template.iss'
    language = snapshot / 'packaging/ChineseSimplified.isl'
    if not template.is_file() or not language.is_file():
        raise RuntimeError('缺少安装向导模板或中文语言文件，请使用项目内最新打包工具。')
    return [str(iscc), '/DBuildSourceDir=' + str(bundle), '/DBuildOutputDir=' + str(output),
            '/DBuildVersion=' + version, '/DBuildAppId={' + app_id,
            '/DBuildOutputName=' + output_name, '/DBuildLanguageFile=' + str(language),
            '/DBuildAppName=' + app['display_name'], '/DBuildExeName=' + app['exe_name'],
            '/DBuildDirectory=' + app['directory'], '/DBuildDataName=' + app['data_name'],
            '/DBuildLegacyAppName=' + app['legacy_title'],
            '/DBuildLegacyDirectory=' + app['legacy_directory'],
            '/DBuildLegacyExeName=' + app['legacy_exe'],
            '/DBuildIcon=' + str(snapshot / 'assets/app.ico'), str(template)]


def publish_verified(candidate: Path, output: Path, manifest: dict, edition='capture') -> tuple[Path, Path]:
    """Replace the last release only after verification; keep one previous EXE."""
    name = edition_info(edition)['name'] + '_安装包'
    destination = output / (name + '.exe')
    pending = output / (name + '.exe.pending')
    shutil.copy2(candidate, pending)
    if sha256(pending) != manifest['exe_sha256']:
        raise RuntimeError('发布文件校验失败，旧EXE已保留。')
    if destination.exists():
        previous = output / 'previous'
        previous.mkdir(exist_ok=True)
        backup = previous / destination.name
        backup_pending = backup.with_suffix('.exe.pending')
        shutil.copy2(destination, backup_pending)
        os.replace(backup_pending, backup)
        old_manifest = output / 'build_manifest.json'
        if old_manifest.is_file():
            shutil.copy2(old_manifest, previous / old_manifest.name)
    try:
        os.replace(pending, destination)
    except PermissionError as exc:
        raise RuntimeError('旧安装包正在使用，请关闭安装向导后重新打包；旧文件保持原样。') from exc
    manifest_path = output / 'build_manifest.json'
    write_json(manifest_path, manifest)
    return destination, manifest_path


def run_build(config: BuildConfig, on_log=None, on_progress=None) -> BuildResult:
    app = edition_info(config.edition)
    project = Path(config.project_dir).expanduser().resolve()
    output = Path(config.output_dir).expanduser().resolve()
    if not (project / app['entry']).is_file() or not (project / 'models/manifest.json').is_file():
        raise RuntimeError('请选择包含' + app['entry'] + '和models目录的项目。')
    # Never use a data/resource directory as a release directory.
    for name in ('models', 'assets', 'logo', 'vendor', 'schemes', 'sessions', 'calibrations', '.venv'):
        if output.is_relative_to((project / name).resolve()):
            raise RuntimeError('输出目录不能放在项目的数据、模型或Python环境中。')
    output.mkdir(parents=True, exist_ok=True)
    existing_manifest = output / 'build_manifest.json'
    if existing_manifest.is_file():
        existing = json.loads(existing_manifest.read_text(encoding='utf8'))
        old_edition = existing.get('edition', 'capture')
        if old_edition != config.edition and not (config.edition == 'capture_cpu' and old_edition == 'capture'
                                                  and existing.get('app_id') == app['app_id']):
            raise RuntimeError('此输出目录属于另一版本，请选择独立输出目录。')
    workspace = output / '.packager'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    version = datetime.now().strftime('%Y.%m.%d.%H%M')
    with build_lock(workspace):
        work = reset_owned_work(workspace)
        temp = work / 'temp'; temp.mkdir()
        logs = workspace / 'logs'; logs.mkdir(exist_ok=True)
        log_path = logs / (stamp + '.log')
        with log_path.open('w', encoding='utf8', buffering=1) as logfile:
            def log(message):
                message = str(message).rstrip()
                logfile.write(message + '\n')
                if on_log: on_log(message)
            def progress(value, message):
                log(message)
                if on_progress: on_progress(value, message)
            try:
                progress(2, '检查项目与打包环境')
                env = child_environment(temp)
                env['PYINSTALLER_CONFIG_DIR'] = str(workspace / 'cache')
                python = select_python(project, config.python_exe, env)
                iscc = select_iscc(project)
                log('项目：' + str(project)); log('Python：' + str(python))
                log('安装包编译器：' + str(iscc))
                if shutil.disk_usage(output).free < 4 * 1024 ** 3:
                    raise RuntimeError('输出磁盘剩余空间不足4GB，请清理空间或选择其他磁盘。')
                progress(8, '复制本次代码和模型，不包含个人数据')
                snapshot = work / 'source'
                sources = snapshot_project(project, snapshot, config.edition)
                progress(10, '读取当前logo并生成应用与安装图标')
                sources.update(prepare_brand_assets(snapshot, python, env))
                if config.edition != 'capture_raw':
                    verify_models(snapshot)
                spec = make_spec(snapshot, version, config.edition)
                progress(15, '分析依赖并生成安装所需程序文件')
                command = [str(python), '-B', '-m', 'PyInstaller', '--noconfirm',
                           '--distpath', str(work / 'dist'), '--workpath', str(work / 'build'), str(spec)]
                log(subprocess.list2cmdline(command))
                proc = spawn(command, cwd=snapshot, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf8', errors='replace')
                stage = 15
                stages = [('Building PYZ', 35, '依赖分析完成，整理Python模块'),
                          ('Building EXE', 45, '正在生成应用主程序'),
                          ('Building COLLECT', 55, '正在收集模型和运行依赖')]
                for line in proc.stdout:
                    log(line)
                    for marker, value, message in stages:
                        if marker in line and stage < value:
                            stage = value; progress(value, message)
                if proc.wait() != 0:
                    raise RuntimeError('PyInstaller打包失败，请查看本次日志；旧EXE已保留。')
                bundle = work / 'dist' / app['exe_name']
                app_exe = bundle / (app['exe_name'] + '.exe')
                if not app_exe.is_file():
                    raise RuntimeError('未找到生成的应用主程序；旧安装包已保留。')
                progress(62, '运行应用自检')
                check = work / 'verification.json'
                check_env = dict(env, QT_QPA_PLATFORM='offscreen')
                check_env[app['data_env']] = str(work / 'verification_data')
                if config.edition != 'processor':
                    # Keep the installer diagnostic on its own discovery ports.
                    port = 17671 if config.edition == 'capture_raw' else 17771
                    lsl_config = work / 'lsl_self_check.cfg'
                    lsl_config.write_text(
                        f'[ports]\nMulticastPort = {port}\nBasePort = {port + 1}\n'
                        '[lab]\nKnownPeers = {127.0.0.1}\n', encoding='ascii')
                    check_env['LSLAPICFG'] = str(lsl_config)
                # A dedicated child log avoids simultaneous writes to the build log
                # from Python and native Windows Qt/MediaPipe components.
                diagnostic_log = work / 'self-check.log'
                with diagnostic_log.open('wb') as diagnostic:
                    proc = spawn([str(app_exe), '--self-check', str(check)], cwd=work,
                                 env=check_env, stdout=diagnostic, stderr=subprocess.STDOUT)
                    try:
                        returncode = proc.wait(timeout=300)
                    except subprocess.TimeoutExpired as exc:
                        proc.kill(); proc.wait()
                        raise RuntimeError('EXE自检超时，旧EXE已保留。详情见日志。') from exc
                if diagnostic_log.stat().st_size:
                    log(diagnostic_log.read_text(encoding='utf8', errors='replace'))
                result = json.loads(check.read_text(encoding='utf8')) if check.is_file() else {}
                if returncode != 0 or not result.get('ok'):
                    log(f'自检退出码 {returncode}: ' + json.dumps(result, ensure_ascii=False))
                    raise RuntimeError('EXE自检未通过，旧EXE已保留。详情见日志。')
                if not result.get('installer_mode') or result.get('portable_mode'):
                    raise RuntimeError('安装版数据目录检查未通过，旧安装包已保留。')
                progress(75, '自检通过，编译中文安装向导和卸载程序')
                setup_output = work / 'installer'; setup_output.mkdir()
                command = installer_command(iscc, snapshot, bundle, setup_output, version, edition=config.edition)
                log(subprocess.list2cmdline(command))
                proc = spawn(command, cwd=snapshot, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding='utf8', errors='replace')
                compression_started = False
                for line in proc.stdout:
                    log(line)
                    if not compression_started and 'Creating setup files' in line:
                        compression_started = True; progress(85, '正在压缩模型和依赖，生成安装包')
                if proc.wait() != 0:
                    raise RuntimeError('安装包编译失败；原安装包已保留，请查看日志。')
                candidate = setup_output / (app['name'] + '_安装包.exe')
                if not candidate.is_file():
                    raise RuntimeError('安装包编译器未生成预期文件。')
                progress(95, '安装包已生成，更新发布文件并备份上一版')
                verify_source_hashes(snapshot, sources)
                metadata = {'built_at': datetime.now().isoformat(timespec='seconds'),
                            'version': version, 'project': str(project), 'python': str(python),
                            'artifact_type': 'windows_installer', 'app_id': app['app_id'], 'edition': config.edition,
                            'tool_is_single_file': True, 'includes_personal_data': False,
                            'data_directory': 'user_selected_separate_directory', 'exe_sha256': sha256(candidate),
                            'exe_bytes': candidate.stat().st_size, 'source_sha256': sources,
                            'verification': result, 'log_path': str(log_path)}
                exe_path, manifest_path = publish_verified(candidate, output, metadata, config.edition)
                progress(100, '打包完成：' + str(exe_path))
                return BuildResult(exe_path, manifest_path, log_path)
            except Exception as exc:
                log('失败：' + str(exc))
                raise RuntimeError(f'{exc}\n日志：{log_path}') from exc


def main():
    parser = argparse.ArgumentParser(description='生成支持覆盖升级的眼动程序安装包')
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path)
    parser.add_argument('--edition', choices=tuple(EDITIONS), default='capture')
    args = parser.parse_args()
    result = run_build(BuildConfig(args.project, args.output, args.python, args.edition), on_log=print)
    print(result.exe_path)


if __name__ == '__main__':
    main()
