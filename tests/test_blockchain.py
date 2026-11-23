import json
from backend.blockchain import sync_outbox, verify_legacy_package as verify_package, KINDS
from backend.db import get_db
from backend.service import digest

class FakeChain:
    def __init__(self):
        self.records = {}
        self.fail = False
    def anchor(self, event):
        if self.fail:
            raise ConnectionError('offline')
        self.records[event['event_key']] = {'package_key': digest(event['package_id']), 'hash': event['hash'], 'kind': KINDS[event['kind']]}
        return '0x' + '1' * 64, 42
    def record(self, key):
        return self.records.get(key)
    def package_event_keys(self, package_id):
        return {key for key, record in self.records.items() if record['package_key'] == digest(package_id)}

def two_readings(client, device, payload):
    for i in range(2):
        assert client.post('/api/readings', headers=device, json=payload | {'reading_id': f'r-{i}'}).status_code == 201

def test_chain_failure_does_not_lose_telemetry(app, client, device, registered, payload):
    two_readings(client, device, payload)
    chain = FakeChain()
    chain.fail = True
    with app.app_context():
        db = get_db()
        result = sync_outbox(db, chain)
        assert result == {'confirmed': 0, 'pending': 2}
        assert db.execute('SELECT count(*) FROM readings').fetchone()[0] == 2
        chain.fail = False
        assert sync_outbox(db, chain) == {'confirmed': 2, 'pending': 0}
        assert sync_outbox(db, chain)['confirmed'] == 0

def test_checkpoint_verification_and_pending_tail(app, client, device, registered, payload):
    two_readings(client, device, payload)
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        assert verify_package(db, 'PKG001', False)['state'] == 'OFFLINE'
        sync_outbox(db, chain)
        report = verify_package(db, 'PKG001', True, chain)
        assert report['state'] == 'VERIFIED' and report['anchored_readings'] == 2
    client.post('/api/readings', headers=device, json=payload | {'reading_id': 'tail'})
    with app.app_context():
        report = verify_package(get_db(), 'PKG001', True, chain)
        assert report['state'] == 'PARTIAL' and report['unanchored_readings'] == 1

def test_modified_data_detected(app, client, device, registered, payload):
    two_readings(client, device, payload)
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        sync_outbox(db, chain)
        row = db.execute('SELECT * FROM readings ORDER BY id LIMIT 1').fetchone()
        changed = json.loads(row['payload']) | {'temperature': 80}
        with db:
            db.execute('UPDATE readings SET payload=? WHERE id=?', (json.dumps(changed), row['id']))
        assert verify_package(db, 'PKG001', True, chain)['state'] == 'INVALID'

def test_deleted_checkpoint_or_chain_reset_detected(app, client, device, registered, payload):
    two_readings(client, device, payload)
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        sync_outbox(db, chain)
        assert verify_package(db, 'PKG001', True, FakeChain())['state'] == 'INVALID'
        with db:
            db.execute("DELETE FROM outbox WHERE kind='CHECKPOINT'")
        assert verify_package(db, 'PKG001', True, chain)['state'] == 'INVALID'

def test_registration_metadata_tamper_detected(app, registered):
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        sync_outbox(db, chain)
        with db:
            db.execute("UPDATE packages SET destination='Attacker'")
        assert verify_package(db, 'PKG001', True, chain)['state'] == 'INVALID'

def test_crash_recovery_is_idempotent(app, registered):
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        event = dict(db.execute('SELECT * FROM outbox').fetchone())
        chain.anchor(event) # Simulate chain success followed by process death before DB update.
        assert sync_outbox(db, chain)['confirmed'] == 1
        assert len(chain.records) == 1

def test_outbox_corruption_blocks_upload(app, registered):
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE outbox SET payload='{}'")
        assert sync_outbox(db, chain)['confirmed'] == 0
        assert not chain.records

def test_derived_status_tamper_detected(app, client, device, registered, payload):
    two_readings(client, device, payload)
    with app.app_context():
        db = get_db()
        with db:
            db.execute("UPDATE readings SET status='TAMPERED' WHERE id=1")
        assert verify_package(db, 'PKG001', False)['state'] == 'INVALID'

def test_individual_proof_reporting_788_readings(app, client, device, admin, registered, payload, monkeypatch):
    # Only test data is written; no transaction submission or historical backfill.
    from backend.service import canonical, ZERO_HASH
    with app.app_context():
        db = get_db()
        head = ZERO_HASH
        with db:
            for index in range(788):
                data = payload | {'reading_id': f'history-{index}'}
                encoded = canonical(data)
                hashed = digest(head + encoded)
                db.execute('INSERT INTO readings(package_id,reading_id,timestamp,received_at,payload,status,reasons,prev_hash,hash,blockchain_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           ('PKG001', data['reading_id'], data['timestamp'], data['timestamp'], encoded,
                            'NORMAL', '[]', head, hashed, 'DISABLED' if index < 784 else 'CONFIRMED'))
                head = hashed
    app.config['BLOCKCHAIN_ENABLED'] = True
    monkeypatch.setattr('backend.blockchain.Chain', FakeChain)
    from backend.reading_proofs import expected_record, reading_key
    class ProofChain:
        def lookup(self, key):
            return proofs.get(key, (bytes(32), bytes(32), 0, False, False))
    with app.app_context():
        proofs = {reading_key(row): expected_record(row) for row in get_db().execute("SELECT * FROM readings WHERE blockchain_status='CONFIRMED'")}
    monkeypatch.setattr('backend.reading_proofs.ReadingChain', ProofChain)
    response = client.get('/api/packages/PKG001/verify', headers=admin)
    assert response.status_code == 200
    report = response.json
    assert report['reading_count'] == 788
    assert report['individual_proofs_confirmed'] == 4
    assert report['individual_proofs_disabled'] == 784
    assert report['individual_proofs_pending'] == report['individual_proofs_failed'] == 0
    assert report['state'] == 'PARTIAL'
    assert report['legacy_checkpoint_state'] == 'PENDING'
    assert report['legacy_checkpoint_anchored_readings'] == report['anchored_readings'] == 0
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT count(*) FROM readings WHERE blockchain_status='DISABLED'").fetchone()[0] == 784
        # Pending includes submitted; another package must not affect this summary.
        with db:
            for row_id, status in [(1, 'PENDING'), (2, 'SUBMITTED'), (3, 'FAILED')]:
                db.execute('UPDATE readings SET blockchain_status=? WHERE id=?', (status, row_id))
    assert client.post('/api/packages', headers=admin, json={'id': 'OTHER', 'name': 'Other', 'origin': 'A', 'destination': 'B'}).status_code == 201
    assert client.post('/api/readings', headers=device, json=payload | {'package_id': 'OTHER'}).status_code == 201
    report = client.get('/api/packages/PKG001/verify', headers=admin).json
    assert report['reading_count'] == 788 and report['individual_proofs_confirmed'] == 4
    assert report['individual_proofs_pending'] == 2 and report['individual_proofs_failed'] == 1
    assert report['individual_proofs_disabled'] == 781
    # A legacy-only mismatch must no longer override individual proof coverage.
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE readings SET hash='corrupt' WHERE id=1")
    report = client.get('/api/packages/PKG001/verify', headers=admin).json
    assert report['state'] == 'PARTIAL' and report['legacy_checkpoint_state'] == 'INVALID'


def test_individual_proofs_primary_after_cleanup(app, client, admin, registered, payload, monkeypatch):
    from backend.reading_proofs import expected_record, reading_key
    from backend.service import canonical, ZERO_HASH
    chain = FakeChain()
    with app.app_context():
        db = get_db()
        head = ZERO_HASH
        with db:
            for index in range(246):
                data = payload | {'reading_id': f'proof-{index}'}
                encoded = canonical(data)
                hashed = digest(head + encoded)
                db.execute('INSERT INTO readings(package_id,reading_id,timestamp,received_at,payload,status,reasons,prev_hash,hash,blockchain_status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           ('PKG001', data['reading_id'], data['timestamp'], data['timestamp'], encoded,
                            'NORMAL', '[]', head, hashed, 'CONFIRMED'))
                head = hashed
        proofs = {reading_key(row): expected_record(row) for row in db.execute('SELECT * FROM readings')}
        from backend.service import enqueue
        with db:
            enqueue(db, 'PKG001', 'CHECKPOINT', {'count': 246, 'head_hash': head})
        sync_outbox(db, chain)
    class ProofChain:
        def lookup(self, key):
            return proofs.get(key, (bytes(32), bytes(32), 0, False, False))
    app.config['BLOCKCHAIN_ENABLED'] = True
    monkeypatch.setattr('backend.blockchain.Chain', lambda: chain)
    monkeypatch.setattr('backend.reading_proofs.ReadingChain', ProofChain)
    def report():
        response = client.get('/api/packages/PKG001/verify', headers=admin)
        assert response.status_code == 200
        return response.json
    initial = report()
    assert initial['state'] == initial['legacy_checkpoint_state'] == 'VERIFIED'
    assert initial['legacy_checkpoint_integrity'] is True
    with app.app_context():
        with get_db() as db:
            db.execute('DELETE FROM readings WHERE id=1')
    cleaned = report()
    assert cleaned['reading_count'] == cleaned['individual_proofs_confirmed'] == cleaned['individual_proofs_verified'] == 245
    assert cleaned['state'] == 'VERIFIED'
    assert cleaned['legacy_checkpoint_state'] == 'INVALID'
    assert cleaned['legacy_checkpoint_integrity'] is False
    with app.app_context():
        db = get_db()
        assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 2
        row = db.execute('SELECT * FROM readings WHERE id=2').fetchone()
        key = reading_key(row)
        original = row['payload']
    saved = proofs.pop(key)
    missing = report()
    assert missing['state'] == 'PARTIAL' and missing['individual_proofs_missing'] == 1
    assert missing['individual_proofs_verified'] == 244
    proofs[key] = saved
    with app.app_context():
        with get_db() as db:
            db.execute('UPDATE readings SET payload=? WHERE id=2', (canonical(json.loads(original) | {'temperature': 99.0}),))
    tampered = report()
    assert tampered['state'] == 'INVALID' and tampered['individual_proofs_tampered'] == 1
    assert tampered['individual_proofs_integrity'] is False
    with app.app_context():
        with get_db() as db:
            db.execute('UPDATE readings SET payload=? WHERE id=2', (original,))
    class OfflineProofChain:
        def lookup(self, key):
            raise ConnectionError('offline')
    monkeypatch.setattr('backend.reading_proofs.ReadingChain', OfflineProofChain)
    assert report()['state'] == 'UNAVAILABLE'
