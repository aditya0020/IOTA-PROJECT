import json
from backend.blockchain import sync_outbox, verify_package, KINDS
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
