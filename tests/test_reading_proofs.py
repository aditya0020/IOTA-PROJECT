import json
import pytest
from backend.db import get_db
from backend.reading_proofs import sensor_hash, expected_record, reading_key, sync_readings, verify_reading

class FakeReadingChain:
    def __init__(self):
        self.records = {}
        self.offline = False
    def lookup(self, key):
        if self.offline:
            raise ConnectionError('secret RPC URL must not escape')
        return self.records.get(key, (bytes(32), bytes(32), 0, False, False))
    def submit(self, row, persist):
        if self.offline:
            raise ConnectionError('private configuration')
        key = reading_key(row)
        expected = expected_record(row)
        if key in self.records:
            assert self.records[key] == expected
        self.records[key] = expected
        persist('0x' + '12' * 32, None)
        return '0x' + '12' * 32, 42

def test_hash_normalization_and_metadata(payload):
    assert sensor_hash(payload) == sensor_hash(dict(reversed(list(payload.items()))))
    assert sensor_hash(payload | {'temperature': 27}) == sensor_hash(payload | {'temperature': 27.0})
    assert sensor_hash(payload) == sensor_hash(payload | {'blockchain_status': 'CONFIRMED', 'blockchain_tx_hash': 'different'})
    for field in ('temperature', 'humidity', 'accel_mps2', 'gyro_radps', 'distance'):
        assert sensor_hash(payload) != sensor_hash(payload | {field: payload[field] + 1})

def test_disabled_and_api_protection(client, registered, device, admin, payload):
    response = client.post('/api/readings', json=payload, headers=device)
    assert response.status_code == 201
    row = response.json['reading']
    assert row['blockchain_status'] == 'DISABLED'
    assert 'blockchain_raw_tx' not in row
    url = f"/api/readings/{row['id']}"
    assert client.get(url + '/verify', headers=admin).json['state'] == 'DISABLED'
    assert client.get('/api/readings/999/verify', headers=admin).status_code == 404
    assert client.post(url + '/retry-blockchain').status_code == 401
    assert client.post(url + '/retry-blockchain', headers=admin).status_code == 409

def test_outage_retry_verification_tamper(app, client, registered, device, admin, payload, monkeypatch):
    app.config['BLOCKCHAIN_ENABLED'] = True
    row = client.post('/api/readings', json=payload, headers=device).json['reading']
    assert row['blockchain_status'] == 'PENDING'
    chain = FakeReadingChain()
    chain.offline = True
    with app.app_context():
        db = get_db()
        assert sync_readings(db, chain)['pending'] == 1
        assert db.execute('SELECT count(*) FROM readings').fetchone()[0] == 1
        assert db.execute('SELECT blockchain_status FROM readings').fetchone()[0] == 'FAILED'
    assert client.post(f"/api/readings/{row['id']}/retry-blockchain", headers=admin).status_code == 202
    chain.offline = False
    monkeypatch.setattr('backend.reading_proofs.ReadingChain', lambda: chain)
    with app.app_context():
        db = get_db()
        assert sync_readings(db, chain) == {'confirmed': 1, 'pending': 0}
        assert sync_readings(db, chain)['confirmed'] == 0
    assert client.get(f"/api/readings/{row['id']}/verify", headers=admin).json['verified'] is True
    with app.app_context():
        db = get_db()
        data = json.loads(db.execute('SELECT payload FROM readings').fetchone()[0])
        with db:
            db.execute('UPDATE readings SET payload=?', (json.dumps(data | {'temperature': 22.4}),))
    result = client.get(f"/api/readings/{row['id']}/verify", headers=admin).json
    assert result['state'] == 'TAMPERED' and result['verified'] is False

def test_sampling_alerts_and_duplicates(app, client, registered, device, payload):
    app.config.update(BLOCKCHAIN_ENABLED=True, BLOCKCHAIN_ANCHOR_EVERY_N_READINGS=2)
    for index, temperature, expected in [(1, 27, 'DISABLED'), (2, 27, 'PENDING'), (3, 90, 'PENDING')]:
        data = payload | {'reading_id': f'r{index}', 'temperature': temperature}
        response = client.post('/api/readings', json=data, headers=device)
        assert response.json['reading']['blockchain_status'] == expected
        assert client.post('/api/readings', json=data, headers=device).status_code == 200

def test_submitted_timeout_preserves_transaction(app, client, registered, device, payload):
    app.config['BLOCKCHAIN_ENABLED'] = True
    client.post('/api/readings', json=payload, headers=device)
    class TimeoutChain:
        def submit(self, row, persist):
            persist('0x' + 'ab' * 32, 'deadbeef')
            raise TimeoutError('secret')
    with app.app_context():
        db = get_db()
        sync_readings(db, TimeoutChain())
        row = db.execute('SELECT * FROM readings').fetchone()
        assert row['blockchain_status'] == 'SUBMITTED'
        assert row['blockchain_raw_tx'] == 'deadbeef'
        assert 'secret' not in row['blockchain_error']

def test_corruption_before_upload_is_not_anchored(app, client, registered, device, payload):
    app.config['BLOCKCHAIN_ENABLED'] = True
    client.post('/api/readings', json=payload, headers=device)
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE readings SET blockchain_data_hash='bad'")
        chain = FakeReadingChain()
        assert sync_readings(db, chain)['confirmed'] == 0
        assert not chain.records

def test_reverted_receipt_allows_fresh_retry(app, client, registered, device, payload):
    from backend.reading_proofs import RevertedTransaction
    app.config['BLOCKCHAIN_ENABLED'] = True
    client.post('/api/readings', json=payload, headers=device)
    class RevertingChain:
        def submit(self, row, persist):
            persist('0x' + 'ab' * 32, 'deadbeef')
            raise RevertedTransaction()
    with app.app_context():
        db = get_db()
        sync_readings(db, RevertingChain())
        row = db.execute('SELECT * FROM readings').fetchone()
        assert row['blockchain_status'] == 'FAILED'
        assert row['blockchain_tx_hash'] is None and row['blockchain_raw_tx'] is None
        assert sync_readings(db, FakeReadingChain())['confirmed'] == 1

def test_verification_unavailable_and_identity_tamper(app, client, registered, device, admin, payload, monkeypatch):
    app.config['BLOCKCHAIN_ENABLED'] = True
    row = client.post('/api/readings', json=payload, headers=device).json['reading']
    chain = FakeReadingChain()
    chain.offline = True
    monkeypatch.setattr('backend.reading_proofs.ReadingChain', lambda: chain)
    result = client.get(f"/api/readings/{row['id']}/verify", headers=admin)
    assert result.status_code == 503 and result.json['state'] == 'UNAVAILABLE'
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE readings SET reading_id='modified'")
        assert verify_reading(db.execute('SELECT * FROM readings').fetchone(), True, chain)['state'] == 'TAMPERED'

def test_additive_migration_preserves_legacy_records(tmp_path):
    import sqlite3
    from backend.db import SCHEMA
    from backend.app import create_app
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute("INSERT INTO packages VALUES('old','Old','A','B',2,30,8,3.14,'NORMAL','2026-01-01')")
        db.execute("INSERT INTO readings(package_id,reading_id,timestamp,received_at,payload,status,reasons,prev_hash,hash) VALUES('old','r','2026-01-01','2026-01-01','{}','NORMAL','[]','previous','original')")
    app = create_app({'TESTING': True, 'DATABASE_PATH': str(path)})
    with app.app_context():
        row = get_db().execute('SELECT * FROM readings').fetchone()
        assert row['hash'] == 'original' and row['payload'] == '{}'
        assert row['blockchain_status'] == 'DISABLED'

def test_chain_configuration_rejects_invalid_address(tmp_path, monkeypatch):
    from backend.blockchain import Chain
    manifest = tmp_path / 'deployment.json'
    manifest.write_text(json.dumps({'address': 'invalid'}))
    monkeypatch.setenv('BLOCKCHAIN_DEPLOYMENT', str(manifest))
    monkeypatch.setenv('BLOCKCHAIN_CONTRACT_ADDRESS', 'invalid')
    with pytest.raises(ValueError, match='Contract address'):
        Chain()
