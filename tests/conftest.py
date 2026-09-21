import pytest
from backend.app import create_app
from scripts.simulate import EXAMPLE, parse_packet

@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'test.sqlite3'),
                       'ADMIN_API_KEY': 'admin-test', 'DEVICE_API_KEY': 'device-test',
                       'CHECKPOINT_INTERVAL': 2, 'BLOCKCHAIN_ENABLED': False})

@pytest.fixture
def client(app):
    return app.test_client()

@pytest.fixture
def admin():
    return {'X-API-Key': 'admin-test'}

@pytest.fixture
def device():
    return {'X-API-Key': 'device-test'}

@pytest.fixture
def registered(client, admin):
    response = client.post('/api/packages', headers=admin, json={'id': 'PKG001', 'name': 'Test shipment', 'origin': 'Lab', 'destination': 'Store'})
    assert response.status_code == 201
    return response.json

@pytest.fixture
def payload():
    return parse_packet(EXAMPLE, 'test-session')
