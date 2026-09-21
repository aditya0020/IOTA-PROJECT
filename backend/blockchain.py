"""Durable outbox worker and independent chain-backed verification."""
import json
import logging
import os
from pathlib import Path
from web3 import Web3
from .service import canonical, digest, detect, ZERO_HASH

LOG = logging.getLogger(__name__)
KINDS = {'REGISTER': 0, 'CONDITION': 1, 'CHECKPOINT': 2}
ROOT = Path(__file__).resolve().parent.parent

class Chain:
    def __init__(self):
        self.web3 = Web3(Web3.HTTPProvider(os.getenv('BLOCKCHAIN_RPC_URL', 'http://127.0.0.1:8545'), request_kwargs={'timeout': 8}))
        deployment_path = Path(os.getenv('BLOCKCHAIN_DEPLOYMENT', 'blockchain/deployment.json'))
        if not deployment_path.is_absolute():
            deployment_path = ROOT / deployment_path
        deployment = json.loads(deployment_path.read_text())
        chain_id = int(os.getenv('BLOCKCHAIN_CHAIN_ID', '31337'))
        if self.web3.eth.chain_id != chain_id or deployment['chain_id'] != chain_id:
            raise RuntimeError('Configured chain ID does not match RPC/deployment')
        if not self.web3.eth.get_code(deployment['address']):
            raise RuntimeError('Contract not found; local chain may have been reset')
        self.deployment = deployment
        self.contract = self.web3.eth.contract(address=deployment['address'], abi=deployment['abi'])

    def record(self, key):
        value = self.contract.functions.records(bytes.fromhex(key)).call()
        if value[1].hex() == ZERO_HASH:
            return None
        return {'package_key': value[0].hex(), 'hash': value[1].hex(), 'kind': value[2]}

    def package_event_keys(self, package_id):
        logs = self.contract.events.Anchored().get_logs(from_block=self.deployment['block_number'], to_block='latest', argument_filters={'packageKey': bytes.fromhex(digest(package_id))})
        return {entry['args']['eventKey'].hex() for entry in logs}

    def anchor(self, event):
        existing = self.record(event['event_key'])
        expected = {'package_key': digest(event['package_id']), 'hash': event['hash'], 'kind': KINDS[event['kind']]}
        if existing and existing != expected:
            raise RuntimeError('Conflicting on-chain event')
        if not existing:
            fn = self.contract.functions.anchor(bytes.fromhex(event['event_key']), bytes.fromhex(expected['package_key']), expected['kind'], bytes.fromhex(event['hash']))
            private_key = os.getenv('BLOCKCHAIN_PRIVATE_KEY')
            if private_key:
                account = self.web3.eth.account.from_key(private_key)
                tx = fn.build_transaction({'from': account.address, 'nonce': self.web3.eth.get_transaction_count(account.address, 'pending'), 'chainId': self.web3.eth.chain_id})
                signed = account.sign_transaction(tx)
                tx_hash = self.web3.eth.send_raw_transaction(signed.raw_transaction)
            else:
                if self.web3.eth.chain_id != 31337:
                    raise RuntimeError('Unlocked account mode is restricted to local chain 31337')
                account = self.web3.eth.accounts[int(os.getenv('BLOCKCHAIN_ACCOUNT_INDEX', '0'))]
                tx_hash = fn.transact({'from': account})
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt.status != 1:
                raise RuntimeError('Transaction reverted')
            return Web3.to_hex(receipt.transactionHash), receipt.blockNumber
        # Recover evidence if a previous attempt committed on-chain but died before DB commit.
        logs = self.contract.events.Anchored().get_logs(from_block=self.deployment['block_number'], to_block='latest', argument_filters={'eventKey': bytes.fromhex(event['event_key'])})
        if not logs:
            raise RuntimeError('Record exists but transaction evidence was not found')
        return Web3.to_hex(logs[0]['transactionHash']), logs[0]['blockNumber']

def sync_outbox(db, chain=None, limit=100):
    processed = 0
    rows = db.execute("SELECT * FROM outbox WHERE state != 'CONFIRMED' ORDER BY id LIMIT ?", (limit,)).fetchall()
    if not rows:
        return {'confirmed': 0, 'pending': 0}
    for row in rows:
        event = dict(row)
        try:
            if digest(event['payload']) != event['hash']:
                raise RuntimeError('Outbox payload hash mismatch')
            if event['event_key'] != digest(f"{event['package_id']}:{event['kind']}:{event['hash']}"):
                raise RuntimeError('Outbox event key mismatch')
            chain = chain or Chain()
            tx_hash, block = chain.anchor(event)
            with db:
                db.execute("UPDATE outbox SET state='CONFIRMED',tx_hash=?,block_number=?,attempts=attempts+1,last_error=NULL WHERE id=?", (tx_hash, block, event['id']))
            processed += 1
        except Exception as error:
            LOG.warning('Chain sync failed for event %s (%s)', event['id'], type(error).__name__)
            with db:
                # No RPC URLs, private keys or raw provider responses in public error fields.
                db.execute("UPDATE outbox SET state='FAILED',attempts=attempts+1,last_error=? WHERE id=?", (type(error).__name__, event['id']))
            break # Preserve registration-before-event ordering; retry on next run.
    pending = db.execute("SELECT count(*) FROM outbox WHERE state != 'CONFIRMED'").fetchone()[0]
    return {'confirmed': processed, 'pending': pending}

def verify_package(db, package_id, enabled, chain=None):
    package = dict(db.execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone())
    rows = db.execute('SELECT * FROM readings WHERE package_id=? ORDER BY id', (package_id,)).fetchall()
    head = ZERO_HASH
    heads = {}
    local_valid = True
    package_status = 'NORMAL'
    for count, row in enumerate(rows, 1):
        recomputed = digest(head + row['payload'])
        try:
            data = json.loads(row['payload'])
            local_valid &= canonical(data) == row['payload'] and data['package_id'] == package_id
            local_valid &= data['reading_id'] == row['reading_id'] and data['timestamp'] == row['timestamp']
            status, reasons = detect(data, package)
            local_valid &= row['status'] == status and row['reasons'] == canonical(reasons)
            package_status = 'TAMPERED' if package_status == 'TAMPERED' else status
        except (ValueError, KeyError, TypeError):
            local_valid = False
        local_valid &= row['prev_hash'] == head and row['hash'] == recomputed
        head = recomputed
        heads[count] = head
    local_valid &= package['status'] == package_status
    events = db.execute('SELECT * FROM outbox WHERE package_id=? ORDER BY id', (package_id,)).fetchall()
    anchored_count, confirmed_events = 0, 0
    chain_valid = None
    error = None
    if enabled:
        try:
            chain = chain or Chain()
            chain_valid = True
            remote_keys = chain.package_event_keys(package_id)
            local_keys = {event['event_key'] for event in events}
            if remote_keys - local_keys:
                chain_valid = False # Deleted local event/checkpoint evidence.
            for event in events:
                payload = json.loads(event['payload'])
                event_valid = digest(event['payload']) == event['hash']
                event_valid &= event['event_key'] == digest(f"{package_id}:{event['kind']}:{event['hash']}")
                if event['kind'] == 'REGISTER':
                    package = dict(db.execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone())
                    event_valid &= all(package.get(k) == v for k, v in payload.items())
                else:
                    event_valid &= heads.get(payload['count']) == payload['head_hash']
                local_valid &= event_valid
                record = chain.record(event['event_key'])
                if record:
                    matches = record == {'package_key': digest(package_id), 'hash': event['hash'], 'kind': KINDS[event['kind']]}
                    chain_valid &= matches
                    confirmed_events += int(matches)
                    if matches and event_valid and event['kind'] != 'REGISTER':
                        anchored_count = max(anchored_count, payload['count'])
                elif event['state'] == 'CONFIRMED':
                    chain_valid = False
            if not any(e['kind'] == 'REGISTER' for e in events):
                local_valid = False
        except Exception as exc:
            chain_valid = None
            error = type(exc).__name__
    state = 'INVALID' if not local_valid or chain_valid is False else 'UNAVAILABLE' if error else 'OFFLINE' if not enabled else 'VERIFIED' if rows and anchored_count == len(rows) else 'PARTIAL' if anchored_count else 'PENDING'
    return {'state': state, 'local_integrity': bool(local_valid), 'chain_integrity': chain_valid,
            'reading_count': len(rows), 'anchored_readings': anchored_count, 'unanchored_readings': len(rows) - anchored_count,
            'confirmed_events': confirmed_events, 'head_hash': head, 'error': error}
