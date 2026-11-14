import hashlib
import json
from pathlib import Path
import sqlite3
import uuid
from datetime import datetime, timezone


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def read_dataset(path):
    payload = Path(path).read_bytes()
    data = json.loads(payload)
    if not isinstance(data, dict) or not isinstance(data.get('tables'), dict):
        raise ValueError('资料必须包含 tables 对象')
    for name, rows in data['tables'].items():
        if not isinstance(rows, list):
            raise ValueError(f'资料表 {name} 必须为列表')
    return data, hashlib.sha256(payload).hexdigest()


class AnalysisStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, digest TEXT NOT NULL, options TEXT NOT NULL, result TEXT NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS designs (id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, digest TEXT NOT NULL, design TEXT NOT NULL, confirmatory INTEGER NOT NULL, note TEXT NOT NULL, superseded_by TEXT)')
            connection.execute('CREATE TABLE IF NOT EXISTS looks (id TEXT PRIMARY KEY, design_id TEXT NOT NULL, look_index INTEGER NOT NULL, cutoff INTEGER NOT NULL, "group" TEXT NOT NULL, digest TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(design_id, look_index))')

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def save(self, title, digest, options, result):
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError('分析名称须为一到一百个字符')
        identifier = uuid.uuid4().hex
        created = utcnow()
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('INSERT INTO analyses VALUES (?, ?, ?, ?, ?, ?)', (identifier, title.strip(), created, digest, encode(options), encode(result)))
            connection.execute('COMMIT')
        return self.get(identifier)

    def get(self, identifier):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM analyses WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise KeyError('分析版本不存在')
        return {**dict(row), 'options': json.loads(row['options']), 'result': json.loads(row['result'])}

    def list(self):
        with self.connect() as connection:
            return [dict(row) for row in connection.execute('SELECT id,title,created_at,digest FROM analyses ORDER BY created_at DESC,id')]

    # ---- 确认性分析计划与查看账本 -------------------------------------------

    def save_design(self, title, digest, design, confirmatory, note):
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError('计划名称须为一到一百个字符')
        identifier = uuid.uuid4().hex
        created = utcnow()
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute(
                'INSERT INTO designs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                (identifier, title.strip(), created, digest, encode(design), int(bool(confirmatory)), note),
            )
            connection.execute('COMMIT')
        return self.get_design(identifier)

    def _design_row(self, connection, identifier):
        return connection.execute('SELECT * FROM designs WHERE id=?', (identifier,)).fetchone()

    def _look_rows(self, connection, identifier):
        return connection.execute('SELECT * FROM looks WHERE design_id=? ORDER BY look_index', (identifier,)).fetchall()

    def _decorate(self, connection, row):
        uses = self._look_rows(connection, row['id'])
        design = json.loads(row['design'])
        return {
            'id': row['id'],
            'title': row['title'],
            'created_at': row['created_at'],
            'digest': row['digest'],
            'design': design,
            'confirmatory': bool(row['confirmatory']),
            'note': row['note'],
            'superseded_by': row['superseded_by'],
            'looks_used': len(uses),
            'looks_remaining': max(design['max_looks'] - len(uses), 0) if row['confirmatory'] else 0,
            'can_look': bool(row['confirmatory']) and row['superseded_by'] is None and len(uses) < design['max_looks'],
            'looks': [
                {
                    'look_index': look['look_index'],
                    'cutoff': look['cutoff'],
                    'group': look['group'],
                    'digest': look['digest'],
                    'created_at': look['created_at'],
                    'decision': json.loads(look['result'])['decision'],
                    'error_budget': json.loads(look['result'])['error_budget'],
                }
                for look in uses
            ],
        }

    def get_design(self, identifier):
        with self.connect() as connection:
            row = self._design_row(connection, identifier)
            if row is None:
                raise KeyError('分析计划不存在')
            return self._decorate(connection, row)

    def list_designs(self):
        with self.connect() as connection:
            return [
                self._decorate(connection, row)
                for row in connection.execute('SELECT * FROM designs ORDER BY created_at DESC,id')
            ]

    def designs_for_digest(self, digest):
        with self.connect() as connection:
            return [
                self._decorate(connection, row)
                for row in connection.execute('SELECT * FROM designs WHERE digest=? ORDER BY created_at,id', (digest,))
            ]

    def supersede(self, previous, replacement):
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._design_row(connection, previous)
            if row is None:
                connection.execute('COMMIT')
                raise KeyError('分析计划不存在')
            if self._look_rows(connection, previous):
                connection.execute('COMMIT')
                raise ValueError('该计划已经打开过结果，不能替换；改动只能另存为探索')
            connection.execute('UPDATE designs SET superseded_by=? WHERE id=?', (replacement, previous))
            connection.execute('COMMIT')

    def find_look(self, design_id, cutoff, group, digest):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM looks WHERE design_id=? AND cutoff=? AND digest=? ORDER BY look_index LIMIT 1',
                (design_id, cutoff, digest),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), 'result': json.loads(row['result']), 'reused': True}

    def get_look(self, design_id, look_index):
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM looks WHERE design_id=? AND look_index=?', (design_id, look_index)).fetchone()
        if row is None:
            raise KeyError('查看记录不存在')
        return {**dict(row), 'result': json.loads(row['result'])}

    def append_look(self, design_id, cutoff, group, digest, expected_index, compute):
        """在同一事务内确认查看次序与时点单调性、计算并冻结结果。

        ``compute`` 接收本次查看次序并返回结果；若期间出现并发写入导致
        次序不符，则抛出「查看次序已变化」，由调用方以新次序重算。
        """
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = self._design_row(connection, design_id)
            if row is None:
                connection.execute('ROLLBACK')
                raise KeyError('分析计划不存在')
            uses = self._look_rows(connection, design_id)
            if len(uses) != expected_index - 1:
                connection.execute('ROLLBACK')
                raise ValueError('查看次序已变化')
            if uses and cutoff <= max(look['cutoff'] for look in uses):
                connection.execute('ROLLBACK')
                raise ValueError('查看时点须晚于已打开过的时点，不能在已看过的资料上重新分配检验额度')
            if connection.execute('SELECT 1 FROM looks WHERE design_id=? AND cutoff=? AND digest=?', (design_id, cutoff, digest)).fetchone():
                connection.execute('ROLLBACK')
                raise ValueError('该时点与资料此前已打开，不能重复消耗额度')
            result = compute(expected_index)
            identifier = uuid.uuid4().hex
            connection.execute(
                'INSERT INTO looks VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (identifier, design_id, expected_index, cutoff, group, digest, utcnow(), encode(result)),
            )
            connection.execute('COMMIT')
        return {**self.get_look(design_id, expected_index), 'reused': False}
