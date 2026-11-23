"""Isolated local-chain demonstration. No application records are modified."""
import json
import os
import tempfile
import uuid
from pathlib import Path
from backend.app import create_app
from backend.db import get_db
from backend.reading_proofs import ReadingChain, sync_readings, verify_reading
from backend.service import canonical
from scripts.simulate import EXAMPLE, parse_packet

def main():
    root = Path(__file__).resolve().parent.parent
    # Load dotenv through the factory before checking the configured chain.
    with tempfile.TemporaryDirectory(prefix='trace-demo-') as directory:
        app = create_app({'DATABASE_PATH': str(Path(directory) / 'demo.sqlite3'),
                          'BLOCKCHAIN_ENABLED': True, 'BLOCKCHAIN_ANCHOR_EVERY_N_READINGS': 1,
                          'ADMIN_API_KEY': uuid.uuid4().hex, 'DEVICE_API_KEY': uuid.uuid4().hex})
        if os.getenv('BLOCKCHAIN_CHAIN_ID', '31337') != '31337':
            raise SystemExit('This demo only writes to local chain 31337.')
        client = app.test_client()
        package_id = 'DEMO-' + uuid.uuid4().hex[:12]
        assert client.post('/api/packages', headers={'X-API-Key': app.config['ADMIN_API_KEY']},
            json={'id': package_id, 'name': 'Disposable demo', 'origin': 'Lab', 'destination': 'Store'}).status_code == 201
        data = parse_packet(EXAMPLE.replace('PKG001', package_id), 'demo') | {'temperature': 27.4}
        assert client.post('/api/readings', headers={'X-API-Key': app.config['DEVICE_API_KEY']}, json=data).status_code == 201
        with app.app_context():
            db = get_db()
            chain = ReadingChain()
            assert sync_readings(db, chain)['confirmed'] == 1, 'Anchoring failed; check writer and deployment'
            def report():
                result = verify_reading(db.execute('SELECT * FROM readings').fetchone(), True, chain)
                print(json.dumps(result, indent=2))
                return result['state']
            assert report() == 'VERIFIED'
            original = db.execute('SELECT payload FROM readings').fetchone()[0]
            try:
                with db:
                    db.execute('UPDATE readings SET payload=?', (canonical(data | {'temperature': 22.4}),))
                assert report() == 'TAMPERED'
            finally:
                with db:
                    db.execute('UPDATE readings SET payload=?', (original,))
            assert report() == 'VERIFIED'
    print('Demo restored the original payload and removed its temporary database. Local proof remains on-chain.')

if __name__ == '__main__':
    main()
