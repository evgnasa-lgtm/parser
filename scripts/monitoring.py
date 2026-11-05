"""Local indexed audit archive and lightweight metrics; no external services."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from build import atomic_write


class Checkpoint:
    def __init__(self, state, publish, render, data, report):
        self.state, self.publish, self.render = state, publish, render
        self.data, self.report = data, report
        self.started = time.monotonic()
        self.last_render = None
        self.renders = 0

    def __call__(self):
        now = time.monotonic()
        finished = not self.report.get('running', False)
        self.report['durationSeconds'] = round(now - self.started, 3)
        processed = self.report.get('updated', 0) + self.report.get('failed', 0)
        self.report['rowsPerSecond'] = round(processed / max(now - self.started, .001), 3)
        self.report['renderCount'] = self.renders
        # Durable data on every card; expensive HTML at most once a minute + final.
        page = None
        if self.last_render is None or finished or now - self.last_render >= 60:
            page = self.render(self.data)
            self.renders += 1
            self.last_render = now
            self.report['renderCount'] = self.renders
        atomic_write(self.state / 'observations.json', json.dumps(self.data, ensure_ascii=False, allow_nan=False))
        if page is not None:
            atomic_write(self.publish / 'index.html', page)
        atomic_write(self.state / 'last-refresh.json', json.dumps(self.report, ensure_ascii=False, indent=2))
        atomic_write(self.state / 'metrics.prom', '\n'.join(
            f'np_refresh_{key} {float(self.report.get(field, 0))}'
            for key, field in [('updated', 'updated'), ('failed', 'failed'), ('skipped', 'skipped'),
                               ('duration_seconds', 'durationSeconds'), ('running', 'running')]) + '\n')
        if finished:
            archive(self.state / 'history.sqlite3', self.data)


def archive(path, data):
    """Rebuildable secondary archive. JSON remains the compatibility source of truth."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('''CREATE TABLE IF NOT EXISTS observations (
            digest TEXT PRIMARY KEY, city TEXT NOT NULL, kind TEXT NOT NULL,
            site TEXT, url TEXT, checked_at TEXT, payload TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS by_offer_time ON observations(city, kind, site, url, checked_at)')
        db.execute('CREATE INDEX IF NOT EXISTS by_time ON observations(checked_at)')
        def rows():
            for city, dataset in [('Москва', data), *data.get('cities', {}).items()]:
                for kind in ('own', 'analogs'):
                    for row in dataset.get(kind, []) + dataset.get('history', {}).get(kind, []):
                        payload = json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False)
                        digest = hashlib.sha256((city + '\0' + kind + '\0' + payload).encode()).hexdigest()
                        yield digest, city, kind, row.get('site'), row.get('url'), row.get('checkedAt'), payload
        db.executemany('INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?)', rows())
