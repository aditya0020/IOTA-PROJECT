"""Versioned sensor proofs. The worker is the only transaction producer.

The existing payload column is the source of truth, never a cached hash or outbox copy.
"""
import json
import math
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from web3 import Web3
from web3.exceptions import TransactionNotFound
from .blockchain import Chain
from .service import canonical, digest, now

FIELDS = ('package_id', 'reading_id', 'seq', 'timestamp', 'temperature', 'humidity',
          'distance', 'accel_mps2', 'gyro_radps', 'source_status')
NUMBERS = ('temperature', 'humidity', 'distance', 'accel_mps2', 'gyro_radps')

class RevertedTransaction(RuntimeError):
    """A mined failure is safe to retry with a fresh transaction."""

def sensor_payload(data):
    result = {key: data[key] for key in FIELDS}
    for key in NUMBERS:
        value = float(result[key])
        if not math.isfinite(value):
            raise ValueError('Non-finite sensor value')
        result[key] = 0.0 if value == 0 else value
    return canonical({'schema': 1, **result})

def sensor_hash(data):
    return digest(sensor_payload(data))

def reading_key(row):
    return digest(canonical(['reading-v1', row['package_id'], row['reading_id']]))

def expected_record(row):
    data = json.loads(row['payload'])
    if data['package_id'] != row['package_id'] or data['reading_id'] != row['reading_id'] or data['timestamp'] != row['timestamp']:
        raise ValueError('Reading identity mismatch')
    return (bytes.fromhex(digest(row['package_id'])), bytes.fromhex(sensor_hash(data)),
            int(datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00')).timestamp()),
            row['status'] != 'NORMAL', True)

def explorer_url(tx_hash):
    base = os.getenv('BLOCKCHAIN_EXPLORER_URL', '').rstrip('/')
    return base + '/tx/' + tx_hash if tx_hash and urlparse(base).scheme in ('http', 'https') else None

@contextmanager
def worker_lock(database):
    """OS lock releases on crash; serialize CLI workers including legacy nonce use."""
    path = Path(str(database) + '.chain.lock')
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

class ReadingChain(Chain):
    def lookup(self, key):
        return tuple(self.contract.functions.getReading(bytes.fromhex(key)).call())

    def evidence(self, key):
        logs = self.contract.events.ReadingAnchored().get_logs(
            from_block=self.deployment['block_number'], to_block='latest',
            argument_filters={'readingIdHash': bytes.fromhex(key)})
        if not logs:
            raise RuntimeError('Missing transaction evidence')
        return Web3.to_hex(logs[0]['transactionHash']), logs[0]['blockNumber']

    def submit(self, row, persist):
        key = reading_key(row)
        expected = expected_record(row)
        remote = self.lookup(key)
        if remote[-1]:
            if remote != expected:
                raise ValueError('Conflicting immutable proof')
            return self.evidence(key)
        tx_hash = row['blockchain_tx_hash']
        raw = row['blockchain_raw_tx']
        if tx_hash:
            try:
                receipt = self.web3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                if raw:
                    try:
                        self.web3.eth.send_raw_transaction(bytes.fromhex(raw))
                    except ValueError:
                        pass # Already known transactions may be rejected by the RPC.
                return None
            if receipt.status != 1:
                raise RevertedTransaction('Transaction reverted')
            # A successful receipt must also have created the expected proof.
            raise RuntimeError('Receipt exists without matching contract proof')
        fn = self.contract.functions.anchorReading(expected[0], bytes.fromhex(key), expected[1], expected[2], expected[3])
        private_key = os.getenv('BLOCKCHAIN_PRIVATE_KEY')
        if private_key:
            account = self.web3.eth.account.from_key(private_key)
            options = {'from': account.address, 'nonce': self.web3.eth.get_transaction_count(account.address, 'pending'),
                       'chainId': self.web3.eth.chain_id}
            options['gas'] = int(fn.estimate_gas(options) * 1.2)
            signed = account.sign_transaction(fn.build_transaction(options))
            tx_hash = Web3.to_hex(signed.hash)
            persist(tx_hash, signed.raw_transaction.hex()) # Durable before broadcast, survives ambiguous RPC failures.
            self.web3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            if self.web3.eth.chain_id != 31337:
                raise ValueError('Missing signing key')
            account = self.web3.eth.accounts[int(os.getenv('BLOCKCHAIN_ACCOUNT_INDEX', '1'))]
            tx_hash = Web3.to_hex(fn.transact({'from': account}))
            persist(tx_hash, None)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=15)
        if receipt.status != 1:
            raise RevertedTransaction('Transaction reverted')
        return tx_hash, receipt.blockNumber

def sync_readings(db, chain=None, limit=100):
    confirmed = 0
    for row in db.execute("SELECT * FROM readings WHERE blockchain_status IN ('PENDING','FAILED','SUBMITTED') ORDER BY id LIMIT ?", (limit,)).fetchall():
        def persist(tx, raw):
            with db:
                db.execute("UPDATE readings SET blockchain_status='SUBMITTED',blockchain_tx_hash=?,blockchain_raw_tx=?,blockchain_error=NULL WHERE id=?", (tx, raw, row['id']))
        try:
            if expected_record(row)[1].hex() != row['blockchain_data_hash']:
                raise ValueError('Stored reading changed before anchoring')
            chain = chain or ReadingChain()
            result = chain.submit(row, persist)
            if result is None:
                break # Resolve the outstanding nonce before sending another transaction.
            tx, block = result
            with db:
                db.execute("UPDATE readings SET blockchain_status='CONFIRMED',blockchain_tx_hash=?,blockchain_block_number=?,blockchain_anchored_at=?,blockchain_error=NULL,blockchain_raw_tx=NULL WHERE id=?", (tx, block, now(), row['id']))
            confirmed += 1
        except Exception as error:
            with db:
                if isinstance(error, RevertedTransaction):
                    db.execute('UPDATE readings SET blockchain_tx_hash=NULL,blockchain_raw_tx=NULL WHERE id=?', (row['id'],))
                db.execute("UPDATE readings SET blockchain_status=CASE WHEN blockchain_tx_hash IS NULL THEN 'FAILED' ELSE 'SUBMITTED' END, blockchain_error=? WHERE id=?",
                           ('Proof submission unavailable (' + type(error).__name__ + '). Check worker configuration and retry.', row['id']))
            break
    return {'confirmed': confirmed, 'pending': db.execute("SELECT count(*) FROM readings WHERE blockchain_status IN ('PENDING','FAILED','SUBMITTED')").fetchone()[0]}

def verify_reading(row, enabled, chain=None):
    result = {'reading_id': row['reading_id'], 'id': row['id'], 'exists_on_chain': False, 'verified': False,
              'local_hash': None, 'blockchain_hash': None, 'transaction_hash': row['blockchain_tx_hash'],
              'block_number': row['blockchain_block_number'], 'status': row['blockchain_status'],
              'state': 'PENDING' if enabled else 'DISABLED', 'error': None}
    try:
        expected = expected_record(row)
        result['local_hash'] = '0x' + expected[1].hex()
    except (ValueError, KeyError, TypeError):
        return result | {'state': 'TAMPERED'}
    if not enabled:
        return result
    try:
        chain = chain or ReadingChain()
        remote = chain.lookup(reading_key(row))
        result['exists_on_chain'] = bool(remote[-1])
        if remote[-1]:
            result['blockchain_hash'] = '0x' + remote[1].hex()
            result['verified'] = remote == expected
            result['state'] = 'VERIFIED' if result['verified'] else 'TAMPERED'
        elif row['blockchain_status'] == 'CONFIRMED':
            result['state'] = 'MISSING_PROOF'
        elif row['blockchain_status'] == 'DISABLED':
            result['state'] = 'DISABLED'
    except Exception:
        result.update(state='UNAVAILABLE', error='Blockchain lookup unavailable. Check network and deployment configuration.')
    return result
