"""Exercise a real loopback HTTP server, simulator and SQLite persistence."""
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from werkzeug.serving import make_server
from backend.app import create_app
from scripts.simulate import request

def main():
    temp_root = Path(__file__).resolve().parent.parent / 'test-results'
    temp_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as directory:
        app = create_app({'DATABASE_PATH': str(Path(directory) / 'smoke.sqlite3'),
                          'ADMIN_API_KEY': 'smoke-admin', 'DEVICE_API_KEY': 'smoke-device', 'BLOCKCHAIN_ENABLED': False})
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            env = os.environ | {'ADMIN_API_KEY': 'smoke-admin', 'DEVICE_API_KEY': 'smoke-device'}
            subprocess.run([sys.executable, '-m', 'scripts.simulate', '--url', base, '--register', '--count', '3', '--interval', '0'], env=env, check=True)
            status, body = request(base, '/api/packages/PKG001/readings', 'smoke-admin')
            assert status == 200 and len(body['items']) == 3
            assert body['items'][0]['seq'] == 158
            print('HTTP pipeline smoke passed: simulator -> Flask -> SQLite -> history API')
        finally:
            server.shutdown()
            thread.join()

if __name__ == '__main__':
    main()
