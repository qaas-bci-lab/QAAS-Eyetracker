"""Screen coordinates and verified exports with captured UTC for experiments.

The screen's physical coordinates start at its top-left. Export coordinates use
the task reference as origin, X right, Y up. Angles compare two screen rays from
one estimated eye origin, and therefore inherit the chosen geometry assumptions.
"""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from numbers import Integral
import csv
import hashlib
import json
import math
import os
import re
import tempfile
import numpy as np

TXT_FIELDS = ('time_s', 'x_cm', 'y_cm', 'distance_cm')
EXPERIMENT_TXT_FIELDS = ('time_s', 'timestamp_utc', 'x_cm', 'y_cm', 'distance_cm')
EXPERIMENT_SAMPLE_HZ = 20
_NS_PER_SECOND = 1_000_000_000
_EXPERIMENT_SLOT_NS = _NS_PER_SECOND // EXPERIMENT_SAMPLE_HZ
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_UTC_TEXT = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.([0-9]{9})Z')
_SOURCE_UTC_TEXT = re.compile(r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})')
CSV_COORDINATE_FIELDS = ['screen_x_cm', 'screen_y_cm', 'x_cm', 'y_cm', 'distance_cm',
                         'angular_error_deg', 'eye_origin_x_mm', 'eye_origin_y_mm', 'eye_origin_z_mm',
                         'export_origin_x_physical', 'export_origin_y_physical']
ANGULAR_METHOD = '两条屏幕视线夹角；眼位按所选距离模式。使用最终校准后坐标，不是独立眼动仪测得的三维角度。'

def number(row, key, fallback=float('nan')):
    try:return float(row.get(key, fallback))
    except (TypeError, ValueError):return fallback


def timestamp_utc_from_unix_ns(value):
    """Convert an exact captured integer nanosecond timestamp, never a grid clock.

    Floating-point timestamps have already lost nanosecond precision and are
    deliberately treated as unavailable, as are missing or out-of-range values.
    """
    if isinstance(value, bool):
        return 'NaN'
    if isinstance(value, Integral):
        ns = int(value)
    elif isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]{1,24}', value.strip()):
        ns = int(value.strip())
    else:
        return 'NaN'
    seconds, nanos = divmod(ns, _NS_PER_SECOND)
    try:
        instant = _UNIX_EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return 'NaN'
    return (f'{instant.year:04d}-{instant.month:02d}-{instant.day:02d}'
            f'T{instant.hour:02d}:{instant.minute:02d}:{instant.second:02d}.{nanos:09d}Z')


def validate_utc_text(value):
    """Accept exact nine-digit UTC text or the missing-source marker ``NaN``."""
    if value == 'NaN':
        return True
    if not isinstance(value, str):
        return False
    match = _UTC_TEXT.fullmatch(value)
    if match is None:
        return False
    try:
        datetime(*(int(part) for part in match.groups()[:6]), tzinfo=timezone.utc)
    except ValueError:
        return False
    return True


def captured_timestamp_utc(row):
    """Use the frame's precise clock, or its existing timezone-aware ISO clock."""
    exact = timestamp_utc_from_unix_ns(row.get('timestamp_unix_ns'))
    if exact != 'NaN':
        return exact
    value = row.get('timestamp_utc')
    match = _SOURCE_UTC_TEXT.fullmatch(value.strip()) if isinstance(value, str) else None
    if match is None:
        return 'NaN'
    parts = match.groups()
    try:
        zone = parts[7]
        offset = timedelta()
        if zone != 'Z':
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if hours > 23 or minutes > 59:
                return 'NaN'
            offset = timedelta(hours=hours, minutes=minutes)
            if zone[0] == '-':
                offset = -offset
        instant = datetime(*(int(part) for part in parts[:6]), tzinfo=timezone(offset))
        elapsed = instant - _UNIX_EPOCH
        ns = (elapsed.days * 86400 + elapsed.seconds) * _NS_PER_SECOND
        ns += int((parts[6] or '').ljust(9, '0'))
        return timestamp_utc_from_unix_ns(ns)
    except (ValueError, OverflowError):
        return 'NaN'

def screen_point_mm(x, y, cfg):
    return np.array([(x / float(cfg['physical_width']) - .5) * float(cfg['screen_width_mm']) - float(cfg.get('camera_x_mm', 0)),
                     y / float(cfg['physical_height']) * float(cfg['screen_height_mm']) + float(cfg.get('camera_y_mm', 0)), 0.], dtype=float)

def angular_error_deg(x, y, ox, oy, eye_origin, cfg):
    eye = np.asarray(eye_origin, dtype=float).reshape(3)
    if not np.isfinite([x,y,ox,oy,*eye]).all() or eye[2] <= 0:return float('nan')
    predicted = screen_point_mm(x,y,cfg) - eye
    reference = screen_point_mm(ox,oy,cfg) - eye
    denominator = np.linalg.norm(predicted) * np.linalg.norm(reference)
    if denominator <= 1e-12:return float('nan')
    return float(np.degrees(np.arccos(np.clip(np.dot(predicted,reference)/denominator,-1.,1.))))

def session_mode(meta):
    """Absent mode denotes an existing record, whose test reports stay compatible."""
    value=meta.get('mode',(meta.get('scheme') or {}).get('mode'))
    return value if value in ('experiment','test') else 'test'


def rows_for_protocol(meta, rows):
    """Apply event-driven direction exclusions or legacy fixed-timeline gates."""
    direction_enabled=meta.get('direction_task_enabled',(meta.get('scheme') or {}).get('direction_task_enabled',False)) is True
    if direction_enabled:
        task=meta.get('direction_task') or {}
        excluded={str(value) for value in task.get('excluded_target_ids',[])}
        return [row for row in rows
                if str(row.get('target_id','-1')).lstrip('-').isdigit()
                and int(row.get('target_id','-1'))>=0
                and str(row.get('target_id','-1')) not in excluded]
    if session_mode(meta)!='test' or not meta.get('transition_exclusion_s') or not (
            meta.get('protocol_version',0)>=3 or meta.get('recording_protocol_version',0)>=3):
        return list(rows)
    from capture import recording_target_state
    result=[]
    for row in rows:
        state=recording_target_state(meta,meta['config'],number(row,'elapsed_s'))
        if state is None or state['eligible']:result.append(row)
    return result


def enrich_session_row(row, meta, eye_origin=None):
    """Keep per-frame target labels for errors, and a fixed origin for new TXT."""
    out=dict(row)
    if meta.get('mode',(meta.get('scheme') or {}).get('mode')) in ('test','experiment'):
        origin=meta.get('origin',(meta.get('scheme') or {}).get('origin',dict(x=.5,y=.5)))
        cfg=meta['config']
        out.update(export_origin_x_physical=float(origin['x'])*cfg['physical_width'],
                   export_origin_y_physical=float(origin['y'])*cfg['physical_height'])
    return enrich_row(out,meta['config'],eye_origin,calculate_angle=session_mode(meta)=='test')


def enrich_row(row, cfg, eye_origin=None, *, calculate_angle=True):
    """Return a copy; missing origins/angles remain NaN rather than assumed labels."""
    out=dict(row);out.update({key:float('nan') for key in CSV_COORDINATE_FIELDS
                             if key not in ('export_origin_x_physical','export_origin_y_physical')})
    if str(row.get('valid')) != '1':return out
    x,y,ox,oy=[number(row,key) for key in ('x_physical','y_physical','origin_x_physical','origin_y_physical')]
    w,h=float(cfg['physical_width']),float(cfg['physical_height'])
    sw,sh=float(cfg['screen_width_mm'])/10,float(cfg['screen_height_mm'])/10
    if min(w,h,sw,sh)<=0 or not np.isfinite([w,h,sw,sh]).all():raise ValueError('屏幕物理尺寸与像素尺寸必须为有限正数')
    if np.isfinite([x,y]).all():out.update(screen_x_cm=x/w*sw,screen_y_cm=y/h*sh)
    ex=number(row,'export_origin_x_physical',ox);ey=number(row,'export_origin_y_physical',oy)
    if not np.isfinite([ex,ey]).all():ex,ey=ox,oy
    if np.isfinite([x,y,ex,ey]).all():
        rx=(x-ex)/w*sw;ry=(ey-y)/h*sh
        out.update(x_cm=rx,y_cm=ry,distance_cm=float(np.hypot(rx,ry)))
    if eye_origin is None:
        eye_origin=np.array([number(row,key) for key in ('eye_origin_x_mm','eye_origin_y_mm','eye_origin_z_mm')])
    eye=np.asarray(eye_origin,dtype=float).reshape(3)
    if np.isfinite(eye).all():
        for key,value in zip(('eye_origin_x_mm','eye_origin_y_mm','eye_origin_z_mm'),eye):out[key]=float(value)
        if calculate_angle:out['angular_error_deg']=angular_error_deg(x,y,ox,oy,eye,cfg)
    elif calculate_angle and np.isfinite(number(row,'angular_error_deg')):
        out['angular_error_deg']=number(row,'angular_error_deg')
    return out

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda:file.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def _values(row):
    return [number(row,'elapsed_s'),number(row,'x_cm'),number(row,'y_cm'),number(row,'distance_cm')]

def experiment_duration_s(meta, rows):
    """Use the recorded duration, capped at the end of the experiment protocol."""
    duration = number(meta, 'actual_duration_s', 0.)
    if not math.isfinite(duration) or duration <= 0:
        times = sorted({number(row, 'elapsed_s') for row in rows
                        if math.isfinite(number(row, 'elapsed_s'))})
        if not times:
            raise ValueError('本次记录没有可分析的采集时段。')
        step = float(np.median(np.diff(times))) if len(times) > 1 else 1 / EXPERIMENT_SAMPLE_HZ
        duration = times[-1] + step
    planned = number(meta, 'planned_duration_s', 0.)
    return min(duration, planned) if math.isfinite(planned) and planned > 0 else duration


def _ordered_nearest_indices(anchors, candidates):
    """Match ordered anchors to distinct candidates with minimum time deviation."""
    previous = [0] * (len(candidates) + 1)
    choices = []
    for anchor in anchors:
        current = [math.inf] * (len(candidates) + 1)
        take_at = [False] * (len(candidates) + 1)
        for position, candidate in enumerate(candidates, 1):
            take = previous[position - 1] + abs(anchor - candidate)
            skip = current[position - 1]
            # Equal cost keeps the earlier source frame or earlier nominal tick.
            if take < skip:
                current[position] = take
                take_at[position] = True
            else:
                current[position] = skip
        previous = current
        choices.append(take_at)
    selected = []
    anchor_index, candidate_index = len(anchors) - 1, len(candidates)
    while anchor_index >= 0:
        if choices[anchor_index][candidate_index]:
            selected.append(candidate_index - 1)
            anchor_index -= 1
        candidate_index -= 1
    return selected[::-1]


def experiment_sample_rows(rows, start_s, end_s):
    """Select at most 20 real UTC-backed frames in each original second.

    Nominal times lie on the 50 ms grid. Missing source frames produce no rows;
    underfilled seconds retain their place on the original session timeline.
    """
    start_ns = round(start_s * _NS_PER_SECOND)
    end_ns = round(end_s * _NS_PER_SECOND)
    first_slot = (start_ns + _EXPERIMENT_SLOT_NS - 1) // _EXPERIMENT_SLOT_NS
    stop_slot = (end_ns + _EXPERIMENT_SLOT_NS - 1) // _EXPERIMENT_SLOT_NS
    by_second = {}
    for row in rows:
        elapsed_s = number(row, 'elapsed_s')
        if not math.isfinite(elapsed_s):
            continue
        elapsed_ns = round(elapsed_s * _NS_PER_SECOND)
        if not start_ns <= elapsed_ns < end_ns:
            continue
        utc = captured_timestamp_utc(row)
        if utc == 'NaN':
            continue
        by_second.setdefault(elapsed_ns // _NS_PER_SECOND, []).append((elapsed_ns, row, utc))
    samples = []
    for second, sources in sorted(by_second.items()):
        slots = list(range(max(first_slot, second * EXPERIMENT_SAMPLE_HZ),
                           min(stop_slot, (second + 1) * EXPERIMENT_SAMPLE_HZ)))
        if not slots:
            continue
        sources.sort(key=lambda source: source[0])
        source_times = [source[0] for source in sources]
        slot_times = [slot * _EXPERIMENT_SLOT_NS for slot in slots]
        if len(sources) >= len(slots):
            source_indices = _ordered_nearest_indices(slot_times, source_times)
            selected = zip(slots, (sources[index] for index in source_indices))
        else:
            slot_indices = _ordered_nearest_indices(source_times, slot_times)
            selected = zip((slots[index] for index in slot_indices), sources)
        for slot, (_, row, utc) in selected:
            sample = {'elapsed_s': slot / EXPERIMENT_SAMPLE_HZ, 'timestamp_utc': utc}
            x, y = number(row, 'x_cm'), number(row, 'y_cm')
            if math.isfinite(x) and math.isfinite(y):
                sample.update(x_cm=x, y_cm=y, distance_cm=math.hypot(x, y))
            samples.append(sample)
    if not samples:
        raise ValueError('所选时段没有带有效原始UTC的真实采集帧，无法导出；原始数据已保留。')
    return samples


def validate_txt(path, rows, *, fields=TXT_FIELDS):
    fields = tuple(fields)
    if fields not in (TXT_FIELDS, EXPERIMENT_TXT_FIELDS):
        raise ValueError('坐标TXT字段不受支持')
    with Path(path).open(encoding='utf8',newline='') as file:
        reader=csv.reader(file,delimiter='\t')
        if tuple(next(reader,[])) != fields:raise ValueError('坐标TXT表头与指定字段不一致')
        lines=list(reader)
    if len(lines)!=len(rows):raise ValueError('坐标TXT行数与预期不一致')
    previous_time=-float('inf')
    for line,row in zip(lines,rows):
        if len(line)!=len(fields):raise ValueError('坐标TXT列数与指定字段不一致')
        values = dict(zip(fields, line))
        actual=[float(values[field]) for field in TXT_FIELDS];expected=_values(row)
        if 'timestamp_utc' in values:
            utc = values['timestamp_utc']
            if utc == 'NaN' or not validate_utc_text(utc):raise ValueError('坐标TXT每行必须具有真实有效的UTC时间戳')
            if utc != row.get('timestamp_utc', 'NaN'):
                raise ValueError('坐标TXT的UTC与选中的采集帧不一致')
        if not math.isfinite(actual[0]):raise ValueError('坐标TXT存在无效时间戳')
        if actual[0]<previous_time:raise ValueError('坐标TXT时间戳不是递增顺序')
        previous_time=actual[0]
        for value,truth in zip(actual,expected):
            if math.isnan(truth):
                if not math.isnan(value):raise ValueError('无效预测未按NaN保存')
            elif not math.isclose(value,truth,rel_tol=1e-8,abs_tol=1e-8):raise ValueError('坐标TXT回读校验失败')
        if np.isfinite(actual[1:]).all() and not math.isclose(math.hypot(*actual[1:3]),actual[3],rel_tol=1e-8,abs_tol=1e-8):
            raise ValueError('坐标TXT的距离与X、Y不一致')
    return dict(rows=len(lines),columns=list(fields),sha256=sha256(path),verified=True)

def export_txt(path, rows, *, time_decimals=10, fields=TXT_FIELDS):
    """Atomically write then read back every value, retaining invalid-frame times."""
    fields = tuple(fields)
    if fields not in (TXT_FIELDS, EXPERIMENT_TXT_FIELDS):
        raise ValueError('坐标TXT字段不受支持')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile('w',encoding='utf8',newline='',dir=path.parent,prefix='.coordinates_',suffix='.tmp',delete=False) as file:
            temporary=Path(file.name);writer=csv.writer(file,delimiter='\t',lineterminator='\n');writer.writerow(fields)
            for row in rows:
                timestamp, *values = _values(row)
                formatted = dict(zip(TXT_FIELDS, [f'{timestamp:.{time_decimals}f}',
                                     *['NaN' if not math.isfinite(v) else f'{v:.10f}' for v in values]]))
                formatted['timestamp_utc'] = row.get('timestamp_utc', 'NaN')
                writer.writerow([formatted[field] for field in fields])
        validate_txt(temporary,rows,fields=fields)
        os.replace(temporary,path);temporary=None
        return validate_txt(path,rows,fields=fields)
    finally:
        if temporary is not None and temporary.exists():temporary.unlink()

def experiment_txt_paths(session, output):
    record_id = Path(session).name
    safe_id = re.sub(r'[^\w.-]+', '_', record_id)[:64]
    if safe_id != record_id:
        safe_id += '_' + hashlib.sha256(record_id.encode('utf8')).hexdigest()[:8]
    output = Path(output)
    if output != Path(session):
        safe_id += '_' + output.name
    return output / f'gaze_coordinates_{safe_id}.txt', output / f'participant_{safe_id}.txt'


def export_experiment_txt(session, output, rows, meta, start_s, end_s):
    data_path, participant_path = experiment_txt_paths(session, output)
    samples = experiment_sample_rows(rows, start_s, end_s)
    result = export_txt(data_path, samples, time_decimals=2, fields=EXPERIMENT_TXT_FIELDS)
    participant = meta.get('participant') or {}
    fields = [('数据文件', data_path.name), ('姓名', participant.get('name', '')),
              ('年龄', participant.get('age')), ('性别', participant.get('sex', '')),
              ('编号', participant.get('participant_id', ''))]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf8', newline='', dir=participant_path.parent,
                                         prefix='.participant_', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            csv.writer(file, delimiter='\t', lineterminator='\n').writerows(fields)
        with temporary.open(encoding='utf8', newline='') as file:
            expected = [[label, '' if value is None else str(value)] for label, value in fields]
            if list(csv.reader(file, delimiter='\t')) != expected:
                raise ValueError('人员信息TXT回读校验失败')
        temporary.replace(participant_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    result.update(path=str(data_path.resolve()), participant_path=str(participant_path.resolve()),
                  participant_sha256=sha256(participant_path), sample_hz=EXPERIMENT_SAMPLE_HZ,
                  missing_samples=sum('x_cm' not in row for row in samples), origin='fixed experiment origin',
                  sampling_method='up to 20 unique real frames per original second, ordered nearest nominal 50 ms ticks; no padding',
                  timestamp_utc_source='selected captured frame timestamp_unix_ns or existing timezone-aware ISO timestamp_utc',
                  missing_timestamps=0,
                  discarded_timestamp_frames=sum(start_s <= number(row, 'elapsed_s') < end_s
                                                 and captured_timestamp_utc(row) == 'NaN' for row in rows),
                  actual_sample_hz=len(samples)/(end_s-start_s),
                  source_timestamp_required=True,
                  missing_value='NaN')
    return result


def export_session_txt(session, rows, cfg, *, meta=None, duration_s=None):
    session = Path(session).resolve()
    if meta is not None and session_mode(meta) == 'experiment':
        enriched = [enrich_session_row(row, meta) for row in rows]
        end_s = experiment_duration_s(meta, rows) if duration_s is None else duration_s
        result = export_experiment_txt(session, session, enriched, meta, 0., end_s)
    else:
        enriched = [enrich_row(row, cfg) for row in rows]
        path = session / 'gaze_coordinates.txt'
        result = export_txt(path, enriched)
        result.update(path=str(path), origin='per-frame recorded reference point')
    result.update(units=dict(time_s='seconds since recording started',x_cm='cm right',y_cm='cm up',distance_cm='cm'),
                  csv_sha256=sha256(session/'gaze.csv'))
    if 'timestamp_utc' in result['columns']:
        result['units']['timestamp_utc'] = 'captured UTC, ISO 8601 with nanoseconds and Z'
    (session/'coordinate_export.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return result
