"""Replay the exact UNO protocol through the HTTP pipeline without hardware."""
import argparse
import json
import os
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from dotenv import load_dotenv
from backend.service import now
from backend.validation import reading

EXAMPLE = 'PKG001|seq=156|T_C=27.50|H_pct=61.00|Distance_cm=14.20|Accel_mps2=9.81|Gyro_radps=0.04|Status=NORMAL'

def parse_packet(packet, session, timestamp=None):
    parts = packet.strip().split('|')
    keys = ['seq', 'T_C', 'H_pct', 'Distance_cm', 'Accel_mps2', 'Gyro_radps', 'Status']
    if len(parts) != 8:
        raise ValueError('Expected 8 pipe-delimited fields')
    fields = {}
    for item, key in zip(parts[1:], keys):
        name, value = item.split('=', 1)
        if name != key:
            raise ValueError(f'Expected {key}')
        fields[name] = value
    if not fields['seq'].isascii() or not fields['seq'].isdigit():
        raise ValueError('Invalid sequence')
    seq = int(fields['seq'])
    return reading({'package_id': parts[0], 'reading_id': f'{session}-{seq}', 'seq': seq,
                    'timestamp': timestamp or now(), 'temperature': float(fields['T_C']),
                    'humidity': float(fields['H_pct']), 'distance': float(fields['Distance_cm']),
                    'accel_mps2': float(fields['Accel_mps2']), 'gyro_radps': float(fields['Gyro_radps']),
                    'source_status': fields['Status']})

def request(base, path, key, payload=None):
    req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                  headers={'Content-Type': 'application/json', 'X-API-Key': key})
    try:
        with urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)

def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:5000')
    parser.add_argument('--count', type=int, default=12)
    parser.add_argument('--interval', type=float, default=2.5)
    parser.add_argument('--scenario', choices=['normal', 'warning', 'tamper'], default='normal')
    parser.add_argument('--register', action='store_true')
    args = parser.parse_args()
    if args.register:
        status, result = request(args.url, '/api/packages', os.environ['ADMIN_API_KEY'],
                                 {'id': 'PKG001', 'name': 'College IoT shipment', 'origin': 'Campus lab', 'destination': 'Warehouse'})
        if status not in (201, 409):
            raise SystemExit(f'Registration failed: {status} {result}')
    session = uuid.uuid4().hex[:16]
    for index in range(args.count):
        temperature = 40 if args.scenario == 'warning' else 27.5
        rotation = 5 if args.scenario == 'tamper' else .04
        packet = f'PKG001|seq={156 + index}|T_C={temperature}|H_pct=61.00|Distance_cm=14.20|Accel_mps2=9.81|Gyro_radps={rotation}|Status=NORMAL'
        payload = parse_packet(packet, session)
        # Exact same payload on a retry, including timestamp and reading_id.
        for attempt in range(4):
            try:
                status, result = request(args.url, '/api/readings', os.environ['DEVICE_API_KEY'], payload)
                if status in (200, 201):
                    break
                if status < 500:
                    raise SystemExit(f'Ingestion failed: {status} {result}')
            except OSError:
                if attempt == 3:
                    raise
            time.sleep(min(2 ** attempt, 8))
        else:
            raise SystemExit('Backend unavailable after retries')
        print(f"seq={payload['seq']} HTTP={status} status={result['reading']['status']} id={result['reading']['id']}")
        if index + 1 < args.count:
            time.sleep(args.interval)

if __name__ == '__main__':
    main()
