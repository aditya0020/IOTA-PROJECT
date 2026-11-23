"""Run against an already deployed localhost Hardhat node; uses a temporary DB."""
import tempfile
import uuid
import os
import sys
from pathlib import Path
from backend.app import create_app
from backend.db import get_db
from backend.blockchain import Chain, sync_outbox, verify_package
from backend.reading_proofs import ReadingChain, sync_readings, verify_reading
from scripts.simulate import parse_packet, EXAMPLE

def signed_main():
    """Exercise local signing with an ephemeral wallet; never print or persist its key."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    chain = ReadingChain()
    if chain.web3.eth.chain_id != 31337:
        raise SystemExit('Smoke test requires local chain 31337')
    wallet = chain.web3.eth.account.create()
    admin = chain.web3.eth.accounts[0]
    role = chain.contract.functions.WRITER_ROLE().call()
    chain.web3.eth.wait_for_transaction_receipt(chain.web3.eth.send_transaction(
        {'from': admin, 'to': wallet.address, 'value': chain.web3.to_wei(1, 'ether')}))
    chain.web3.eth.wait_for_transaction_receipt(chain.contract.functions.grantRole(role, wallet.address).transact({'from': admin}))
    original = os.environ.get('BLOCKCHAIN_PRIVATE_KEY')
    try:
        os.environ['BLOCKCHAIN_PRIVATE_KEY'] = wallet.key.hex()
        main()
    finally:
        if original is None:
            os.environ.pop('BLOCKCHAIN_PRIVATE_KEY', None)
        else:
            os.environ['BLOCKCHAIN_PRIVATE_KEY'] = original
        chain.web3.eth.wait_for_transaction_receipt(chain.contract.functions.revokeRole(role, wallet.address).transact({'from': admin}))

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
            assert chain.web3.eth.chain_id == 31337, 'Smoke test only supports local Hardhat'
            db = get_db()
            reading_chain = ReadingChain()
            if os.getenv('BLOCKCHAIN_PRIVATE_KEY'):
                class InterruptedChain(ReadingChain):
                    def submit(self, row, persist):
                        def interrupted(tx, raw):
                            persist(tx, raw)
                            raise ConnectionError('Simulated interruption before broadcast')
                        return super().submit(row, interrupted)
                assert sync_readings(db, InterruptedChain())['confirmed'] == 0
                assert db.execute('SELECT blockchain_status FROM readings WHERE id=1').fetchone()[0] == 'SUBMITTED'
                # First retry rebroadcasts the exact signed transaction, next recovers its proof.
                assert sync_readings(db, reading_chain)['confirmed'] == 0
            assert sync_readings(db, reading_chain) == {'confirmed': 2, 'pending': 0}
            row = db.execute('SELECT * FROM readings WHERE id=1').fetchone()
            assert verify_reading(row, True, reading_chain)['verified'] is True
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
            assert verify_reading(db.execute('SELECT * FROM readings WHERE id=1').fetchone(), True, reading_chain)['state'] == 'TAMPERED'
        print('Hardhat integration passed: registration, checkpoint, live chain verification, crash recovery, tamper detection')

if __name__ == '__main__':
    signed_main() if '--signed' in sys.argv else main()
