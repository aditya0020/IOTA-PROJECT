def test_dashboard_assets_resolve_from_flask(client):
    response = client.get('/', follow_redirects=True)
    assert response.status_code == 200
    assert b'href="./styles.css"' in response.data
    assert b'src="./app.js"' in response.data
    assert client.get('/static/styles.css').mimetype == 'text/css'
    assert client.get('/static/app.js').status_code == 200

def test_live_server_preflight_and_authenticated_api(client, admin):
    origin = 'http://127.0.0.1:5500'
    response = client.options('/api/packages', headers={
        'Origin': origin, 'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type,x-api-key'})
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == origin
    assert 'X-API-Key' in response.headers['Access-Control-Allow-Headers']
    assert client.get('/api/packages', headers={'Origin': origin}).status_code == 401
    response = client.get('/api/packages', headers=admin | {'Origin': origin})
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == origin

def test_unapproved_origins_are_not_allowed(client, admin):
    response = client.get('/api/packages', headers=admin | {'Origin': 'https://untrusted.example'})
    assert 'Access-Control-Allow-Origin' not in response.headers
