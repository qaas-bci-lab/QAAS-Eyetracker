"""Processing-device choices independent of Qt and acquisition geometry."""
from __future__ import annotations

import copy
from functools import lru_cache
import os
import re
import json
from pathlib import Path
import tempfile


def _check_cuda_device(torch, index):
    value = torch.ones(1, device=f'cuda:{index}')
    if (value + 1).item() != 2.:
        raise RuntimeError('GPU 运算结果无效')
    torch.cuda.synchronize(index)


@lru_cache(maxsize=1)
def _probe():
    cores = max(1, os.cpu_count() or 1)
    result = dict(cpu_count=cores, cpu_default_jobs=min(8, max(1, cores // 4)),
                  cuda_available=False, cuda_devices=[], cuda_reason='',
                  torch_version='', cuda_runtime=None)
    try:
        import torch
        result.update(torch_version=str(torch.__version__), cuda_runtime=torch.version.cuda)
        if torch.version.cuda is None:
            result['cuda_reason'] = '当前环境安装的是 CPU 版 PyTorch'
            return result
        if not torch.cuda.is_available():
            result['cuda_reason'] = '未检测到可用的 NVIDIA CUDA 设备，请检查显卡驱动和运行环境'
            return result
        devices = []
        for index in range(torch.cuda.device_count()):
            entry = dict(index=index, device=f'cuda:{index}', name=f'GPU {index}',
                         memory_mb=0, capability='', available=False, error='')
            try:
                properties = torch.cuda.get_device_properties(index)
                entry.update(name=properties.name, memory_mb=round(properties.total_memory / (1024 * 1024)),
                             capability=f'{properties.major}.{properties.minor}')
                _check_cuda_device(torch, index)
                entry['available'] = True
            except Exception as error:
                entry['error'] = str(error)
            devices.append(entry)
        available = any(item['available'] for item in devices)
        result.update(cuda_available=available, cuda_devices=devices)
        if not available:
            result['cuda_reason'] = ('；'.join(f"{item['name']}：{item['error']}" for item in devices)
                                     or '没有可供处理的 CUDA 设备')
    except Exception as error:
        result['cuda_reason'] = '无法初始化 CUDA：' + str(error)
    return result


def probe_hardware(*, refresh=False):
    """Return a detached hardware summary; probing does not modify files."""
    if refresh:
        _probe.cache_clear()
    return copy.deepcopy(_probe())


def resolve_device(name='cpu'):
    """Explicit CUDA requests fail clearly; only auto may choose CPU fallback."""
    if not isinstance(name, str):
        raise ValueError('处理设备必须为 cpu、auto 或 cuda 编号')
    name = name.strip().lower()
    if name == 'cpu':
        return 'cpu'
    if name not in ('auto', 'cuda') and not re.fullmatch(r'cuda:\d+', name):
        raise ValueError('处理设备必须为 cpu、auto、cuda 或 cuda:0 等编号')
    hardware = probe_hardware()
    if not hardware['cuda_available']:
        if name == 'auto':
            return 'cpu'
        raise ValueError('无法使用 GPU：' + hardware['cuda_reason'])
    devices = hardware['cuda_devices']
    index = (int(name.split(':')[1]) if ':' in name
             else next(item['index'] for item in devices if item.get('available', True)))
    entry = next((item for item in devices if item['index'] == index), None)
    if entry is None:
        raise ValueError(f'找不到 CUDA 设备 {index}，当前可用 {len(hardware["cuda_devices"])} 张显卡')
    if not entry.get('available', True):
        raise ValueError(f"无法使用 {entry['name']}：{entry.get('error') or 'CUDA 运算未通过验证'}")
    return f'cuda:{index}'


def recommended_jobs(device='cpu', *, cpu_count=None):
    selected = resolve_device(device)
    if selected.startswith('cuda:'):
        return 1
    cores = max(1, (os.cpu_count() or 1) if cpu_count is None else int(cpu_count))
    return min(8, max(1, cores // 4))


def plan_execution(device='cpu', jobs=1, cpu_threads=None, *, cpu_count=None):
    """Plan a total PyTorch inference budget, not a cap on all system threads."""
    if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 8:
        raise ValueError('同时处理数量必须为 1 至 8 的整数')
    if cpu_threads is not None and (isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or cpu_threads < 1):
        raise ValueError('CPU 推理线程预算必须为正整数')
    selected = resolve_device(device)
    cores = max(1, (os.cpu_count() or 1) if cpu_count is None else int(cpu_count))
    requested_jobs = jobs
    if selected.startswith('cuda:'):
        jobs = 1
    requested_budget = jobs * 4 if cpu_threads is None else cpu_threads
    if selected.startswith('cuda:'):
        requested_budget = 4
    budget = min(cores, requested_budget)
    jobs = min(jobs, budget)
    threads = max(1, budget // jobs)
    return dict(device=selected, logical_cores=cores, requested_jobs=requested_jobs, jobs=jobs,
                cpu_threads_requested=requested_budget, cpu_threads_budget=budget,
                threads_per_job=threads, cpu_threads_used=jobs * threads)


def _preferences_root(root):
    if root is None:
        from app_paths import DATA_ROOT
        root = DATA_ROOT
    return Path(root)


def default_preferences(*, cpu_count=None):
    cores = max(1, (os.cpu_count() or 1) if cpu_count is None else int(cpu_count))
    return dict(version=1, device='auto', jobs=min(8, max(1, cores // 4)), cpu_threads=cores,
                retain_raw_rgb=True)


def _validated_preferences(value, *, cpu_count=None):
    out = default_preferences(cpu_count=cpu_count)
    if not isinstance(value, dict):
        return out
    device = value.get('device')
    if isinstance(device, str) and (device in ('auto', 'cpu', 'cuda') or re.fullmatch(r'cuda:\d+', device)):
        out['device'] = device
    jobs = value.get('jobs')
    if isinstance(jobs, int) and not isinstance(jobs, bool) and 1 <= jobs <= 8:
        out['jobs'] = jobs
    budget = value.get('cpu_threads')
    if isinstance(budget, int) and not isinstance(budget, bool) and budget >= 1:
        out['cpu_threads'] = min(budget, out['cpu_threads'])
    if isinstance(value.get('retain_raw_rgb'), bool):
        out['retain_raw_rgb'] = value['retain_raw_rgb']
    return out


def load_preferences(root=None, *, cpu_count=None):
    path = _preferences_root(root) / 'processor_preferences.json'
    try:
        if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
            return default_preferences(cpu_count=cpu_count)
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        value = {}
    return _validated_preferences(value, cpu_count=cpu_count)


def save_preferences(value, root=None, *, cpu_count=None):
    value = _validated_preferences(value, cpu_count=cpu_count)
    root = _preferences_root(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'processor_preferences.json'
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise ValueError('处理设置文件不能为链接')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=root,
                                         prefix='.processor-settings-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return value
