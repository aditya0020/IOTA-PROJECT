import hashlib
import json
from datetime import datetime, timezone

ZERO_HASH = '0' * 64

def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def enqueue(db, package_id, kind, payload):
    encoded = canonical(payload)
    hashed = digest(encoded)
    key = digest(f'{package_id}:{kind}:{hashed}')
    db.execute('INSERT INTO outbox(package_id,kind,payload,hash,event_key,created_at) VALUES(?,?,?,?,?,?)',
               (package_id, kind, encoded, hashed, key, now()))

def detect(data, package):
    reasons = []
    if not package['min_temp'] <= data['temperature'] <= package['max_temp']:
        reasons.append('TEMPERATURE_VIOLATION')
    acceleration = data['accel_mps2']
    rotation = data['gyro_radps']
    if abs(acceleration - 9.80665) > package['accel_threshold']:
        reasons.append('UNUSUAL_ACCELERATION')
    if rotation > package['gyro_threshold']:
        reasons.append('UNUSUAL_ROTATION')
    status = 'TAMPERED' if any(r.startswith('UNUSUAL') for r in reasons) else 'WARNING' if reasons else 'NORMAL'
    return status, reasons

def reading_json(row):
    if row is None:
        return None
    return json.loads(row['payload']) | {k: row[k] for k in ('id', 'received_at', 'status', 'hash', 'prev_hash')} | {'reasons': json.loads(row['reasons'])}
