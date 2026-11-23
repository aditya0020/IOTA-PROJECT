import sqlite3
from pathlib import Path
from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS packages (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, origin TEXT NOT NULL,
 destination TEXT NOT NULL, min_temp REAL NOT NULL, max_temp REAL NOT NULL,
 accel_threshold REAL NOT NULL, gyro_threshold REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'NORMAL', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readings (
 id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL REFERENCES packages(id),
 reading_id TEXT NOT NULL, timestamp TEXT NOT NULL, received_at TEXT NOT NULL,
 payload TEXT NOT NULL, status TEXT NOT NULL, reasons TEXT NOT NULL,
 prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
 UNIQUE(package_id, reading_id)
);
CREATE INDEX IF NOT EXISTS reading_package ON readings(package_id, id);
CREATE TABLE IF NOT EXISTS alerts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL REFERENCES packages(id),
 reading_id INTEGER NOT NULL REFERENCES readings(id), timestamp TEXT NOT NULL,
 status TEXT NOT NULL, reasons TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT, package_id TEXT NOT NULL REFERENCES packages(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL,
 event_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL DEFAULT 'PENDING',
 tx_hash TEXT, block_number INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
 last_error TEXT, created_at TEXT NOT NULL
);
"""

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE_PATH'], timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db

def init_db(app):
    Path(app.config['DATABASE_PATH']).parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(lambda error: g.pop('db', None).close() if 'db' in g else None)
    with app.app_context():
        get_db().executescript(SCHEMA)
        # Additive migration: historical readings remain disabled until explicitly selected.
        db = get_db()
        columns = {row['name'] for row in db.execute('PRAGMA table_info(readings)')}
        additions = {'blockchain_status': "TEXT NOT NULL DEFAULT 'DISABLED'",
                     'blockchain_tx_hash': 'TEXT', 'blockchain_block_number': 'INTEGER',
                     'blockchain_data_hash': 'TEXT', 'blockchain_anchored_at': 'TEXT',
                     'blockchain_error': 'TEXT', 'blockchain_raw_tx': 'TEXT'}
        with db:
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f'ALTER TABLE readings ADD COLUMN {name} {declaration}')
