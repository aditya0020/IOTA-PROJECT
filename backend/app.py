import hmac
import json
import logging
import os
import sqlite3
from functools import wraps
from pathlib import Path
import click
from dotenv import load_dotenv
from flask import Flask, jsonify, request, redirect
from werkzeug.exceptions import HTTPException
from .db import get_db, init_db
from . import validation
from .service import canonical, digest, detect, enqueue, now, reading_json, ZERO_HASH

ROOT = Path(__file__).resolve().parent.parent

def create_app(test_config=None):
    load_dotenv(ROOT / '.env')
    app = Flask(__name__, static_folder=str(ROOT / 'dashboard'), static_url_path='/static')
    app.config.update(DATABASE_PATH=os.getenv('DATABASE_PATH', str(ROOT / 'data/supply_chain.sqlite3')),
                      DEVICE_API_KEY=os.getenv('DEVICE_API_KEY', ''), ADMIN_API_KEY=os.getenv('ADMIN_API_KEY', ''),
                      CHECKPOINT_INTERVAL=int(os.getenv('CHECKPOINT_INTERVAL', '10')), MAX_CONTENT_LENGTH=8192,
                      BLOCKCHAIN_ENABLED=os.getenv('BLOCKCHAIN_ENABLED', 'false').lower() == 'true')
    app.config.update(BLOCKCHAIN_ANCHOR_EVERY_N_READINGS=int(os.getenv('BLOCKCHAIN_ANCHOR_EVERY_N_READINGS', '1')),
                      BLOCKCHAIN_ANCHOR_ALERTS_IMMEDIATELY=os.getenv('BLOCKCHAIN_ANCHOR_ALERTS_IMMEDIATELY', 'true').lower() == 'true')
    if test_config:
        app.config.update(test_config)
    app.config.setdefault('CORS_ALLOWED_ORIGINS', os.getenv('CORS_ALLOWED_ORIGINS',
        'http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:5501,http://localhost:5501'))
    allowed_origins = {origin.strip() for origin in app.config['CORS_ALLOWED_ORIGINS'].split(',') if origin.strip()}

    @app.after_request
    def live_server_cors(response):
        if request.path.startswith('/api/'):
            response.vary.add('Origin')
            origin = request.headers.get('Origin')
            if origin in allowed_origins:
                response.headers['Access-Control-Allow-Origin'] = origin
                response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
                response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-API-Key'
        return response
    if app.config['CHECKPOINT_INTERVAL'] < 1:
        raise ValueError('CHECKPOINT_INTERVAL must be positive')
    if app.config['BLOCKCHAIN_ANCHOR_EVERY_N_READINGS'] < 1:
        raise ValueError('BLOCKCHAIN_ANCHOR_EVERY_N_READINGS must be positive')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    init_db(app)

    def auth(role):
        def decorate(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                key = app.config[role + '_API_KEY']
                if not key:
                    return jsonify(error=f'{role} API key is not configured'), 503
                if not hmac.compare_digest(request.headers.get('X-API-Key', '').encode('utf-8'), key.encode('utf-8')):
                    return jsonify(error='Unauthorized'), 401
                return fn(*args, **kwargs)
            return wrapped
        return decorate

    def find_package(package_id):
        row = get_db().execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone()
        if row is None:
            from werkzeug.exceptions import NotFound
            raise NotFound('Package not found')
        return dict(row)

    def pagination():
        try:
            limit, offset = int(request.args.get('limit', 100)), int(request.args.get('offset', 0))
            if not 1 <= limit <= 500 or offset < 0:
                raise ValueError()
            return limit, offset
        except ValueError:
            raise validation.ValidationError('limit must be 1–500; offset must be nonnegative')

    @app.errorhandler(validation.ValidationError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception('Request failed')
        return jsonify(error='Internal server error'), 500

    @app.get('/')
    def dashboard():
        return redirect('/static/index.html')

    @app.get('/api/health')
    def health():
        get_db().execute('SELECT 1')
        return jsonify(status='ok', blockchain_enabled=app.config['BLOCKCHAIN_ENABLED'])

    @app.post('/api/packages')
    @auth('ADMIN')
    def register():
        package = validation.package(request.get_json()) | {'created_at': now()}
        db = get_db()
        try:
            with db:
                db.execute('INSERT INTO packages(id,name,origin,destination,min_temp,max_temp,accel_threshold,gyro_threshold,created_at) VALUES(:id,:name,:origin,:destination,:min_temp,:max_temp,:accel_threshold,:gyro_threshold,:created_at)', package)
                enqueue(db, package['id'], 'REGISTER', package)
        except sqlite3.IntegrityError:
            return jsonify(error='Package already exists'), 409
        app.logger.info('Registered package %s', package['id'])
        return jsonify(find_package(package['id'])), 201

    @app.get('/api/packages')
    @auth('ADMIN')
    def packages():
        limit, offset = pagination()
        return jsonify(items=[dict(r) for r in get_db().execute('SELECT * FROM packages ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset))])

    @app.get('/api/packages/<package_id>')
    @auth('ADMIN')
    def details(package_id):
        return jsonify(find_package(package_id))

    @app.post('/api/readings')
    @auth('DEVICE')
    def ingest():
        data = validation.reading(request.get_json())
        db = get_db()
        with db:
            db.execute('BEGIN IMMEDIATE')
            package = find_package(data['package_id'])
            encoded = canonical(data)
            existing = db.execute('SELECT * FROM readings WHERE package_id=? AND reading_id=?', (data['package_id'], data['reading_id'])).fetchone()
            if existing:
                if existing['payload'] != encoded:
                    return jsonify(error='reading_id already used for different data'), 409
                return jsonify(reading=reading_json(existing), duplicate=True), 200
            prev = db.execute('SELECT * FROM readings WHERE package_id=? ORDER BY id DESC LIMIT 1', (data['package_id'],)).fetchone()
            if prev and data['timestamp'] < prev['timestamp']:
                return jsonify(error='Reading predates latest accepted reading'), 409
            status, reasons = detect(data, package)
            previous_hash = prev['hash'] if prev else ZERO_HASH
            hashed = digest(previous_hash + encoded)
            cursor = db.execute('INSERT INTO readings(package_id,reading_id,timestamp,received_at,payload,status,reasons,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?)',
                                (data['package_id'], data['reading_id'], data['timestamp'], now(), encoded, status, canonical(reasons), previous_hash, hashed))
            row_id = cursor.lastrowid
            # Tampering remains latched; historical alerts remain even after temperature recovers.
            package_status = 'TAMPERED' if package['status'] == 'TAMPERED' else status
            db.execute('UPDATE packages SET status=? WHERE id=?', (package_status, data['package_id']))
            changed = prev is None or prev['reasons'] != canonical(reasons)
            if reasons and changed:
                db.execute('INSERT INTO alerts(package_id,reading_id,timestamp,status,reasons) VALUES(?,?,?,?,?)', (data['package_id'], row_id, data['timestamp'], status, canonical(reasons)))
            count = db.execute('SELECT count(*) FROM readings WHERE package_id=?', (data['package_id'],)).fetchone()[0]
            from .reading_proofs import sensor_hash
            selected = count % app.config['BLOCKCHAIN_ANCHOR_EVERY_N_READINGS'] == 0 or (reasons and app.config['BLOCKCHAIN_ANCHOR_ALERTS_IMMEDIATELY'])
            proof_status = 'PENDING' if app.config['BLOCKCHAIN_ENABLED'] and selected else 'DISABLED'
            db.execute('UPDATE readings SET blockchain_status=?,blockchain_data_hash=? WHERE id=?',
                       (proof_status, sensor_hash(data), row_id))
            checkpoint = {'package_id': data['package_id'], 'count': count, 'head_hash': hashed, 'reading_id': data['reading_id'], 'timestamp': data['timestamp']}
            if changed and (reasons or (prev and json.loads(prev['reasons']))):
                enqueue(db, data['package_id'], 'CONDITION', checkpoint | {'status': status, 'reasons': reasons})
            if count % app.config['CHECKPOINT_INTERVAL'] == 0:
                enqueue(db, data['package_id'], 'CHECKPOINT', checkpoint)
        return jsonify(reading=reading_json(db.execute('SELECT * FROM readings WHERE id=?', (row_id,)).fetchone()), duplicate=False), 201

    @app.get('/api/packages/<package_id>/live')
    @auth('ADMIN')
    def live(package_id):
        package = find_package(package_id)
        row = get_db().execute('SELECT * FROM readings WHERE package_id=? ORDER BY id DESC LIMIT 1', (package_id,)).fetchone()
        return jsonify(package=package, reading=reading_json(row), server_time=now())

    @app.get('/api/packages/<package_id>/readings')
    @auth('ADMIN')
    def history(package_id):
        find_package(package_id)
        limit, offset = pagination()
        rows = get_db().execute('SELECT * FROM readings WHERE package_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (package_id, limit, offset))
        return jsonify(items=[reading_json(r) for r in rows])

    @app.get('/api/packages/<package_id>/alerts')
    @auth('ADMIN')
    def alerts(package_id):
        find_package(package_id)
        limit, offset = pagination()
        return jsonify(items=[dict(r) | {'reasons': json.loads(r['reasons'])} for r in get_db().execute('SELECT * FROM alerts WHERE package_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (package_id, limit, offset))])

    @app.get('/api/packages/<package_id>/blockchain')
    @auth('ADMIN')
    def blockchain_history(package_id):
        find_package(package_id)
        limit, offset = pagination()
        rows = get_db().execute('SELECT * FROM outbox WHERE package_id=? ORDER BY id DESC LIMIT ? OFFSET ?', (package_id, limit, offset))
        return jsonify(enabled=app.config['BLOCKCHAIN_ENABLED'], items=[dict(r) | {'payload': json.loads(r['payload'])} for r in rows])

    @app.get('/api/packages/<package_id>/verify')
    @auth('ADMIN')
    def verify(package_id):
        find_package(package_id)
        from .blockchain import verify_package
        return jsonify(verify_package(get_db(), package_id, app.config['BLOCKCHAIN_ENABLED']))

    @app.cli.command('sync-chain')
    @click.option('--watch', is_flag=True, help='Poll the durable outbox every 3 seconds.')
    def sync_chain(watch):
        from .blockchain import sync_outbox
        from .reading_proofs import sync_readings, worker_lock
        import time
        if not app.config['BLOCKCHAIN_ENABLED']:
            raise click.ClickException('Set BLOCKCHAIN_ENABLED=true first')
        while True:
            try:
                with worker_lock(app.config['DATABASE_PATH']):
                    click.echo({'readings': sync_readings(get_db())})
                    # Never race a pending reading transaction for the wallet nonce.
                    if not get_db().execute("SELECT 1 FROM readings WHERE blockchain_status='SUBMITTED' LIMIT 1").fetchone():
                        click.echo({'checkpoints': sync_outbox(get_db())})
            except OSError:
                raise click.ClickException('Another chain worker is running for this database')
            if not watch:
                break
            time.sleep(3)

    def find_reading(row_id):
        row = get_db().execute('SELECT * FROM readings WHERE id=?', (row_id,)).fetchone()
        if row is None:
            from werkzeug.exceptions import NotFound
            raise NotFound('Reading not found')
        return row

    @app.get('/api/readings/<int:row_id>/verify')
    @auth('ADMIN')
    def verify_sensor(row_id):
        from .reading_proofs import verify_reading
        result = verify_reading(find_reading(row_id), app.config['BLOCKCHAIN_ENABLED'])
        return jsonify(result), 503 if result['state'] == 'UNAVAILABLE' else 200

    @app.post('/api/readings/<int:row_id>/retry-blockchain')
    @auth('ADMIN')
    def retry_sensor(row_id):
        row = find_reading(row_id)
        if not app.config['BLOCKCHAIN_ENABLED']:
            return jsonify(error='Blockchain is disabled'), 409
        if row['blockchain_status'] == 'DISABLED':
            return jsonify(error='Reading was not selected for anchoring'), 409
        if row['blockchain_status'] == 'FAILED':
            with get_db():
                get_db().execute("UPDATE readings SET blockchain_status='PENDING',blockchain_error=NULL WHERE id=? AND blockchain_status='FAILED'", (row_id,))
        return jsonify(reading=reading_json(find_reading(row_id))), 202

    return app
