"""Run against an already deployed localhost Hardhat node; uses a temporary DB."""
import tempfile
import uuid
from pathlib import Path
from backend.app import create_app
from backend.db import get_db
from backend.blockchain import Chain, sync_outbox, verify_package
from scripts.simulate import parse_packet, EXAMPLE

def main():
    temp_root = Path(__file__).resolve().parent.parent / 'test-results'
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as directory:
        app = create_app({'DATABASE_PATH': str(Path(directory) / 'chain.sqlite3'), 'ADMIN_API_KEY': 'smoke-admin',
                          'DEVICE_API_KEY': 'smoke-device', 'CHECKPOINT_INTERVAL': 2, 'BLOCKCHAIN_ENABLED': True})
        client = app.test_client()
        package_id = 'SMOKE-' + uuid.uuid4().hex[:10]
        result = client.post('/api/packages', headers={'X-API-Key': 'smoke-admin'}, json={'id': package_id, 'name': 'Chain integration', 'origin': 'A', 'destination': 'B'})
        assert result.status_code == 201
        for index in range(2):
            packet = EXAMPLE.replace('PKG001', package_id).replace('seq=156', f'seq={index}')
            result = client.post('/api/readings', headers={'X-API-Key': 'smoke-device'}, json=parse_packet(packet, 'smoke'))
            assert result.status_code == 201
        with app.app_context():
            chain = Chain()
            db = get_db()
            assert sync_outbox(db, chain) == {'confirmed': 2, 'pending': 0}
            assert verify_package(db, package_id, True, chain)['state'] == 'VERIFIED'
            # Recreate post-transaction crash: worker must recover the original evidence.
            with db:
                db.execute("UPDATE outbox SET state='PENDING',tx_hash=NULL,block_number=NULL")
            assert sync_outbox(db, chain) == {'confirmed': 2, 'pending': 0}
            assert all(row[0].startswith('0x') for row in db.execute('SELECT tx_hash FROM outbox'))
            with db:
                db.execute("UPDATE readings SET payload=replace(payload, '27.5', '99.0') WHERE id=1")
            assert verify_package(db, package_id, True, chain)['state'] == 'INVALID'
        print('Hardhat integration passed: registration, checkpoint, live chain verification, crash recovery, tamper detection')

if __name__ == '__main__':
    main()
