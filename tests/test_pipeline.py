import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from backend.db import get_db
from backend.service import digest
from scripts.simulate import parse_packet, EXAMPLE

def test_exact_hardware_packet_is_stored_and_readable(app, client, admin, device, registered, payload):
    response = client.post('/api/readings', headers=device, json=payload)
    assert response.status_code == 201
    row = response.json['reading']
    assert row['package_id'] == 'PKG001' and row['seq'] == 156
    assert row['accel_mps2'] == 9.81 and row['gyro_radps'] == .04
    assert row['temperature'] == 27.5 and row['status'] == 'NORMAL'
    with app.app_context():
        stored = get_db().execute('SELECT * FROM readings').fetchone()
        assert json.loads(stored['payload']) == payload
        assert stored['hash'] == digest(stored['prev_hash'] + stored['payload'])
    assert client.get('/api/packages/PKG001/live', headers=admin).json['reading']['seq'] == 156
    assert len(client.get('/api/packages/PKG001/readings', headers=admin).json['items']) == 1

def test_authentication(client, payload, registered):
    assert client.post('/api/readings', json=payload).status_code == 401
    assert client.post('/api/readings', json=payload, headers={'X-API-Key': 'admin-test'}).status_code == 401
    assert client.get('/api/packages').status_code == 401

@pytest.mark.parametrize('patch', [
    {'temperature': '27'}, {'humidity': 101}, {'distance': -1}, {'temperature': True},
    {'gyro_radps': -1}, {'accel_mps2': float('nan')}, {'temperature': float('inf')},
    {'seq': -1}, {'seq': True}, {'seq': 4294967296}, {'timestamp': '2026-01-01'},
    {'timestamp': '2099-01-01T00:00:00Z'}, {'package_id': '../bad'}, {'extra': 1},
    {'source_status': ''}, {'gyro_radps': 62}, {'temperature': 10 ** 400}
])
def test_invalid_readings(client, device, registered, payload, patch):
    assert client.post('/api/readings', headers=device, json=payload | patch).status_code == 400

def test_duplicate_retry_and_conflict(client, admin, device, registered, payload):
    assert client.post('/api/readings', headers=device, json=payload).status_code == 201
    retry = client.post('/api/readings', headers=device, json=payload)
    assert retry.status_code == 200 and retry.json['duplicate']
    assert client.post('/api/readings', headers=device, json=payload | {'humidity': 50}).status_code == 409
    assert len(client.get('/api/packages/PKG001/readings', headers=admin).json['items']) == 1

def test_concurrent_retries(app, device, registered, payload):
    def submit(_):
        with app.test_client() as client:
            return client.post('/api/readings', headers=device, json=payload).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(submit, range(4))) == [200, 200, 200, 201]

def test_conditions_and_latched_tamper(client, device, admin, registered, payload):
    for i, patch in enumerate([{'temperature': 40}, {'temperature': 40}, {}, {'gyro_radps': 5}, {}]):
        result = client.post('/api/readings', headers=device, json=payload | patch | {'reading_id': f'r-{i}', 'seq': i})
        assert result.status_code == 201
    assert client.get('/api/packages/PKG001', headers=admin).json['status'] == 'TAMPERED'
    alerts = client.get('/api/packages/PKG001/alerts', headers=admin).json['items']
    assert len(alerts) == 2
    assert alerts[0]['reasons'] == ['UNUSUAL_ROTATION']
    assert alerts[1]['reasons'] == ['TEMPERATURE_VIOLATION']

def test_unusual_acceleration(client, device, registered, payload):
    result = client.post('/api/readings', headers=device, json=payload | {'accel_mps2': 25})
    assert result.json['reading']['status'] == 'TAMPERED'

def test_unknown_package(client, device, payload):
    assert client.post('/api/readings', headers=device, json=payload).status_code == 404

def test_old_data_and_sequence_restart(client, device, registered, payload):
    assert client.post('/api/readings', headers=device, json=payload).status_code == 201
    assert client.post('/api/readings', headers=device, json=payload | {'reading_id': 'old', 'timestamp': '2020-01-01T00:00:00Z'}).status_code == 409
    assert client.post('/api/readings', headers=device, json=payload | {'reading_id': 'new-boot-0', 'seq': 0}).status_code == 201

def test_registration_validation(client, admin, registered):
    data = {'id': 'PKG002', 'name': 'Two', 'origin': 'A', 'destination': 'B', 'min_temp': 30, 'max_temp': 2}
    assert client.post('/api/packages', headers=admin, json=data).status_code == 400
    data.update(id='PKG001', min_temp=2, max_temp=30)
    assert client.post('/api/packages', headers=admin, json=data).status_code == 409

def test_bad_json_and_pagination(client, admin, device, registered):
    assert client.post('/api/readings', headers=device, json=[]).status_code == 400
    assert client.post('/api/readings', headers=device, data='{', content_type='application/json').status_code == 400
    assert client.post('/api/readings', headers=device, data='x' * 9000, content_type='application/json').status_code == 413
    assert client.get('/api/packages/PKG001/readings?limit=9999', headers=admin).status_code == 400
    assert client.get('/api/packages/PKG001/alerts?offset=-1', headers=admin).status_code == 400

def test_database_survives_app_restart(app, client, admin, device, registered, payload):
    from backend.app import create_app
    client.post('/api/readings', headers=device, json=payload)
    restarted = create_app(dict(app.config)).test_client()
    assert restarted.get('/api/packages/PKG001/live', headers=admin).json['reading']['seq'] == 156

@pytest.mark.parametrize('packet', ['ACK|156', EXAMPLE.replace('seq=156', 'seq=-1'), EXAMPLE.replace('27.50', 'nan'), EXAMPLE + '|extra=1'])
def test_simulator_rejects_invalid_packets(packet):
    with pytest.raises(ValueError):
        parse_packet(packet, 'session')
