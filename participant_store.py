"""A durable index of participants and their recordings; source records stay intact."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from app_paths import DATA_ROOT


def _text(value):
    return '' if value is None else str(value).strip()


def _path(value):
    return str(Path(value).expanduser().resolve())


class Catalog:
    """Paths are remembered even if an external drive is temporarily absent."""

    def __init__(self, root=DATA_ROOT):
        self.root = Path(root).resolve()
        self.path = self.root / 'participant_history.json'
        self.data = {'version': 1, 'people': {}, 'records': {}, 'roots': []}
        self._invalid_file = False
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8-sig'))
                if not isinstance(data, dict) or not isinstance(data.get('people'), dict) or not isinstance(data.get('records'), dict) or not isinstance(data.get('roots'), list):
                    raise ValueError('Invalid history index')
                self.data = data
            except (OSError, ValueError, UnicodeError):
                self._invalid_file = True

    def _save(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self._invalid_file and self.path.exists():
            # Preserve a damaged index for recovery before rebuilding from records.
            backup = self.path.with_name('participant_history.invalid.' + uuid.uuid4().hex + '.json')
            shutil.copy2(self.path, backup)
            self._invalid_file = False
        descriptor, name = tempfile.mkstemp(prefix='.participant_history.', suffix='.tmp', dir=self.root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf8') as file:
                json.dump(self.data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _person(self, values, *, record_path=None):
        values = values if isinstance(values, dict) else {}
        identifier = _text(values.get('participant_id'))
        supplied = _text(values.get('person_key') or values.get('key'))
        if not record_path and supplied in self.data['people'] and self.data['people'][supplied].get('deleted') is True:
            raise ValueError('此人员已删除，请通过“新增人员”重新建立档案。')
        if record_path:
            remembered = self.data['records'].get(_path(record_path), {}).get('person_key')
            stable = remembered if remembered in self.data['people'] else supplied
            if stable in self.data['people']:
                # Recording snapshots keep their original owner when an editable
                # display identifier is changed or subsequently reused.
                return self.data['people'][stable]
        matched = next((key for key, person in self.data['people'].items()
                        if identifier and person.get('participant_id') == identifier
                        and (person.get('deleted') is not True or (record_path and not supplied))), None)
        if supplied in self.data['people'] and matched and matched != supplied:
            raise ValueError('此编号已属于另一位人员，请使用其他编号。')
        key = matched or supplied
        if not key:
            if identifier:
                key = 'id:' + identifier
                if key in self.data['people']:
                    key = 'person:' + uuid.uuid4().hex
            elif record_path:
                key = 'record:' + hashlib.sha256(_path(record_path).encode('utf8')).hexdigest()[:24]
            else:
                key = 'person:' + uuid.uuid4().hex
        old = self.data['people'].get(key, {})
        person = dict(old)
        person.update(key=key, person_key=key, name=_text(values.get('name')),
                      participant_id=identifier, age=values.get('age'), sex=_text(values.get('sex')))
        if isinstance(person['age'], bool) or not isinstance(person['age'], int) or not 0 <= person['age'] <= 120:
            person['age'] = None
        # A saved profile is authoritative; old recording snapshots must not undo edits.
        if record_path and old:
            return old
        self.data['people'][key] = person
        return person

    def upsert_person(self, values):
        person = self._person(values)
        self._save()
        return copy.deepcopy(person)

    def delete_person(self, key):
        """Hide one stable identity while preserving its original recording ownership."""
        person = self.data['people'].get(key)
        if person is None or person.get('deleted') is True:
            return False
        previous = copy.deepcopy(person)
        person.update(deleted=True, deleted_at=datetime.now().astimezone().isoformat())
        try:
            self._save()
        except (OSError, ValueError):
            self.data['people'][key] = previous
            raise
        return True

    def _read_record(self, folder):
        folder = Path(folder).resolve()
        meta_path = folder / 'session.json'
        meta = json.loads(meta_path.read_text(encoding='utf-8-sig'))
        if not isinstance(meta, dict):
            raise ValueError('记录信息格式不正确。')
        from coordinates import session_mode
        mode = session_mode(meta)
        person = self._person(meta.get('participant', {}), record_path=folder)
        scheme = meta.get('scheme') if isinstance(meta.get('scheme'), dict) else {}
        created = _text(meta.get('created_local'))
        try:
            date = datetime.fromisoformat(created)
        except ValueError:
            date = datetime.fromtimestamp(meta_path.stat().st_mtime)
        analyzed = False
        if mode == 'experiment' and (isinstance(meta.get('result'), dict)
                                     or (folder / 'coordinate_result.json').is_file()):
            from record_layout import read_result
            try:
                stored = read_result(folder)
                analyzed = (isinstance(stored, dict) and
                            (stored.get('mode') == 'experiment' or bool(stored.get('txt_path') or stored.get('output_dir'))))
            except (OSError, ValueError, UnicodeError):
                pass
        elif mode != 'experiment':
            result = folder / 'report.json'
            if result.is_file():
                try:
                    analyzed = isinstance(json.loads(result.read_text(encoding='utf-8-sig')), dict)
                except (OSError, ValueError, UnicodeError):
                    pass
        raw = (folder / 'camera.mp4').is_file()
        from record_layout import has_capture_data
        captured = has_capture_data(folder, meta)
        coordinates = (folder / 'gaze.csv').is_file()
        scheme_name = _text(scheme.get('name')) or ('实验' if mode == 'experiment' else '测试')
        segment = meta.get('segment_index')
        if mode == 'experiment' and meta.get('segment_count') == 2 and segment in (1, 2):
            scheme_name = f'{scheme_name}（第 {segment} 段）'
        return {
            'path': str(folder), 'person_key': person['key'], 'mode': mode,
            'created': created or date.isoformat(), 'timestamp': date.timestamp(),
            'date': date.strftime('%Y-%m-%d %H:%M:%S'),
            'scheme_name': scheme_name,
            'model_id': _text(meta.get('model_id') or scheme.get('model_id')),
            'available': True, 'has_raw': raw, 'analyzed': analyzed,
            'status': '已分析' if analyzed else '待处理' if captured and (raw or coordinates) else '已保存',
        }

    def remember_record(self, path):
        record = self._read_record(path)
        self.data['records'][record['path']] = record
        parent = str(Path(record['path']).parent)
        if parent not in self.data['roots']:
            self.data['roots'].append(parent)
        self._save()
        return copy.deepcopy(record)

    def refresh(self, record_roots=()):
        before = copy.deepcopy(self.data)
        roots = {_path(self.root / 'sessions'), *self.data['roots']}
        roots.update(_path(root) for root in record_roots if root)
        self.data['roots'] = sorted(roots)
        candidates = set(self.data['records'])
        for root in roots:
            try:
                base = Path(root)
                if (base / 'session.json').is_file():
                    candidates.add(str(base))
                candidates.update(str(path.parent.resolve()) for path in base.glob('*/session.json'))
            except (OSError, ValueError):
                continue
        for folder in sorted(candidates):
            try:
                self.data['records'][folder] = self._read_record(folder)
            except (OSError, ValueError, UnicodeError, TypeError):
                if folder in self.data['records']:
                    record = self.data['records'][folder]
                    record['available'] = False
                    record['status'] = '位置不可用' if not (Path(folder) / 'session.json').is_file() else '记录信息损坏'
        if before != self.data or self._invalid_file:
            self._save()
        return self

    def people(self):
        people = []
        for key, value in self.data['people'].items():
            if value.get('deleted') is True:
                continue
            person = copy.deepcopy(value)
            rows = [row for row in self.data['records'].values() if row['person_key'] == key]
            person['count'] = len(rows)
            person['latest'] = max((row['timestamp'] for row in rows), default=0)
            people.append(person)
        return sorted(people, key=lambda person: (-person['latest'], person['name'], person['participant_id'], person['key']))

    def records(self, person_key, mode=None):
        rows = [copy.deepcopy(row) for row in self.data['records'].values()
                if row['person_key'] == person_key and (mode is None or row['mode'] == mode)]
        return sorted(rows, key=lambda row: (row['timestamp'], row['path']), reverse=True)
