# Blockchain-Based Secure Smart Supply Chain Monitoring System

For individual reading proofs, role-separated wallets, local Hardhat/Sepolia deployment,
durable retries and the safe tamper demo, see [BLOCKCHAIN_SETUP.md](BLOCKCHAIN_SETUP.md).
Local writer account is index 1.

College IoT project: DHT11 + HC-SR04 + MPU6050 → Arduino UNO → NodeMCU ESP8266 → HTTP JSON → Flask → SQLite → blockchain → dashboard.

## Hardware preserved

The supplied workspace was empty. No existing Arduino firmware was modified or replaced. The ESP8266 adapter uses the working protocol supplied by the project owner:

```text
PKG001|seq=156|T_C=27.50|H_pct=61.00|Distance_cm=14.20|Accel_mps2=9.81|Gyro_radps=0.04|Status=NORMAL
ACK|156
```

Packets are newline terminated, approximately every 2500 ms. UNO `SoftwareSerial gateway(12, 11)` and ESP8266 D6 RX / D5 TX both use **9600 baud**. UNO D11 TX connects through the existing voltage divider to D6; D5 connects to UNO D12 RX; grounds are shared. USB debug output uses 115200 baud and never enters the UNO serial channel.

The current packet provides **magnitudes**, not individual accelerometer/gyroscope axes. Store acceleration in **m/s²**, rotation in **rad/s**, distance in **cm**, temperature in **°C**, humidity in **%**. No fake x/y/z values are generated. `source_status` preserves the UNO status while Flask computes its own status.

## Stage 1: HTTP and database pipeline

Use Python 3.11+ (tested with 3.13), and run commands from the project root. PowerShell commands below use executables directly, so activating scripts is unnecessary.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

Put the two generated tokens in `.env` as `DEVICE_API_KEY` and `ADMIN_API_KEY`. Keep the tokens different. Initially leave `BLOCKCHAIN_ENABLED=false` unless you have already started a local or Sepolia chain. SQLite is created automatically at `data/supply_chain.sqlite3`; changing `DATABASE_PATH` selects a different database. `.env`, firmware credentials and database files are ignored by Git.

Start the backend:

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5000 --call backend.app:create_app
```

In another terminal, register `PKG001` and send readings without hardware:

```powershell
.\.venv\Scripts\python.exe -m scripts.simulate --register --count 12
.\.venv\Scripts\python.exe -m scripts.simulate --scenario warning --count 3
.\.venv\Scripts\python.exe -m scripts.simulate --scenario tamper --count 3
```

`--url http://192.168.1.100:5000` selects another backend. `--interval` defaults to 2.5 seconds. The simulator parses the supplied UNO packet format and performs real HTTP requests. It does not simulate an electrical serial port or measure hardware ACK latency.

### Flash the ESP8266 only

```powershell
.\.venv\Scripts\python.exe -m pip install platformio==6.1.18
Copy-Item firmware/esp8266/include/config.example.h firmware/esp8266/include/config.h
```

Edit `config.h` with your Wi-Fi SSID/password, computer's **numeric LAN IPv4 address**, API port, and matching device token. The ESP8266 cannot reach the computer using `localhost`. Keep the configured D6/D5 pins and 9600 baud. Build and upload:

```powershell
.\.venv\Scripts\python.exe -m platformio run -d firmware/esp8266
.\.venv\Scripts\python.exe -m platformio run -d firmware/esp8266 -t upload
.\.venv\Scripts\python.exe -m platformio device monitor -d firmware/esp8266
```

Both devices must reach the same LAN backend. Allow TCP 5000 in the computer's firewall for the private network. Register PKG001 before sending data. NTP supplies UTC because the UNO packet contains no clock timestamp. Until time sync completes, the gateway buffers readings and estimates their capture times from elapsed milliseconds. The backend also records its independent `received_at` time.

The gateway ACKs a valid packet immediately after accepting it into its **24-packet RAM queue**, before HTTP completion. ACK therefore means local acceptance, not durable database storage. This preserves the existing serial exchange while HTTP runs separately. A full queue or malformed packet receives no ACK. Consecutive retransmissions are ACKed without creating another reading. Connection/response failures retain the queued JSON and retry with bounded backoff. Reboots lose RAM-buffered readings; a persistent spool is a future enhancement if power-loss durability is required. No claim of exactly-once delivery across device resets is made.

Each upload carries the original package ID and sequence, a boot/session-specific reading ID, sensor magnitudes, source status and UTC timestamp. Flask returns 201 for new readings, 200 for an identical retry, 409 for an ID conflict or a reading older than the latest accepted timestamp. Sequence resets are supported through new reading IDs. Correct a 4xx configuration error before continuing; the gateway retains the head packet rather than silently dropping it.

Example JSON:

```json
{
  "package_id": "PKG001",
  "reading_id": "esp-session-1-156",
  "seq": 156,
  "timestamp": "2026-09-21T18:30:00Z",
  "temperature": 27.5,
  "humidity": 61,
  "distance": 14.2,
  "accel_mps2": 9.81,
  "gyro_radps": 0.04,
  "source_status": "NORMAL"
}
```

### Backend API

Send `X-API-Key` with the device token for ingestion and the admin token for all package/read endpoints. `/api/health` is public. Invalid JSON/data returns 400, missing/wrong credentials 401, missing packages 404, conflicts 409, oversized bodies 413, and missing server credentials 503. Lists support `limit` (1–500) and `offset` (≥0), newest first.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Database health and configured blockchain mode |
| POST | `/api/packages` | Register shipment |
| GET | `/api/packages` | List shipments |
| GET | `/api/packages/<id>` | Registration and package status |
| POST | `/api/readings` | Validate and store sensor packet |
| GET | `/api/packages/<id>/live` | Latest reading, package status, server time |
| GET | `/api/packages/<id>/readings` | Sensor history |
| GET | `/api/packages/<id>/alerts` | Condition changes |

Registration requires `id`, `name`, `origin`, `destination`; optional numeric thresholds are `min_temp` (default 2), `max_temp` (30), `accel_threshold` (8 m/s² deviation from gravity) and `gyro_threshold` (3.14 rad/s magnitude).

Temperature outside the inclusive range produces WARNING. Acceleration magnitude differing from 9.80665 m/s² by more than the configured threshold, or excessive rotation, produces TAMPERED. TAMPERED remains latched at package level; subsequent normal samples retain their actual NORMAL sample status. Temperature warnings recover automatically. Alerts are recorded on condition changes to avoid one alert per repeated sample. These are configurable classroom heuristics; movement flags are evidence for inspection, not proof of malicious tampering. Calibrate thresholds for your actual shipment and MPU6050 mounting.

### Pipeline verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=test-results/pytest-local
.\.venv\Scripts\python.exe -m scripts.smoke_pipeline
g++.exe -std=c++11 -Wall -Wextra -Werror firmware/tests/protocol_test.cpp -o test-results/protocol_test.exe
.\test-results\protocol_test.exe
```

The smoke test starts a temporary real HTTP server, runs the simulator, verifies persisted readings through the API, and shuts down. Tests cover the exact packet, validation, malformed JSON, authentication, duplicate retries, concurrent ingestion, sequence restart, persistence and condition detection. The C++ test executes the same parser included by the firmware. On Linux/macOS use `.venv/bin/python`, `.venv/bin/waitress-serve`, `cp`, `g++` and forward slash executable paths.

Hardware acceptance: confirm `ACK|seq` on the UNO, `Stored seq=... HTTP=201` on NodeMCU USB logs, and matching `seq` through the live/history API. Disconnect Wi-Fi briefly and verify queued data uploads after reconnect. Actual serial timing, Wi-Fi behavior and flashing require the connected boards and are separate from software tests.

Plain HTTP and shared API tokens are intended for a trusted college LAN demo. Public deployment requires TLS, per-device identity, user authentication and operational hardening; never expose the development blockchain RPC to the internet.

## Stage 2: blockchain

Install Node.js 22 (tested with 22.14) and npm. This project pins Hardhat 2.26.3 and Solidity 0.8.28 for the installed toolchain; use `npm ci` to reproduce the checked dependency graph. The compiler comes from the pinned `solc` package. The official Hardhat documentation now recommends Hardhat 3 for new production projects; this college demo retains the tested Hardhat 2 toolchain.

```powershell
cd blockchain
npm.cmd ci
npm.cmd test
npm.cmd run node
```

Keep the node terminal open. In a second terminal:

```powershell
cd blockchain
npm.cmd run deploy
```

Deployment writes `blockchain/deployment.json` with the address, ABI, chain ID and deployment block. In the root `.env`, set `BLOCKCHAIN_ENABLED=true`, then restart Flask. In another root terminal run the durable queue worker:

```powershell
.\.venv\Scripts\python.exe -m flask --app backend.app:create_app sync-chain --watch
```

Run **one worker per signing account** to avoid nonce races. Without `--watch` it processes up to 100 queued events once. A failed event is marked FAILED and retried on the next pass. Registration always precedes later events. Blockchain failure never rolls back accepted sensor data. For local Hardhat, no development private key is embedded in source. For a signed account, use `BLOCKCHAIN_PRIVATE_KEY`; that account must match the deployed writer and have gas funds. Unlocked account mode is restricted to chain 31337.

Only WRITER_ROLE can anchor records. The administrator/deployer and writer are
separate. On-chain individual proof data contains hashed package ID, hashed reading
ID, SHA-256 payload hash, timestamp and alert flag. Full registration metadata,
readings, alerts and checkpoint payloads remain in SQLite. Legacy checkpoint events
are still submitted and displayed as secondary evidence.

The worker writes transaction hash and block number after successful receipts. The contract deduplicates stable event keys. If a worker crashes after the chain accepts a transaction, it retrieves the original event receipt on retry rather than creating duplicate evidence. Logs report event IDs and error types without printing keys or raw provider responses.

Additional authenticated APIs:

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/packages/<id>/blockchain` | PENDING / FAILED / CONFIRMED events and receipts |
| GET | `/api/packages/<id>/verify` | Recompute local hashes and query the actual chain |

Package verification reports OFFLINE, UNAVAILABLE, PENDING, PARTIAL, VERIFIED or INVALID based on the retained readings' individual proofs. Legacy checkpoints report their own state, coverage and integrity separately. Verification attests stored data against the configured ledger; it cannot prove the original sensor measurement was truthful or protect against an attacker replacing both the database and trusted blockchain/configuration. Local Hardhat state is ephemeral and not a production decentralized trust network.

Run the real contract/backend integration test with the node running and contract deployed:

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_blockchain
```

This creates a uniquely named test shipment on the local chain, uses a temporary database, verifies individual proofs and checkpoints, exercises crash recovery, and detects modified data. Unit tests also cover outages, pending tails, removed checkpoints and registration metadata changes.

If you restart/reset Hardhat, its chain history is lost. Existing database CONFIRMED rows will no longer verify. For a fresh demo, redeploy and point `DATABASE_PATH` to a **new** database before registration; retain the old database if you need its records. Do not present old transaction hashes as proof on the new chain.

## Stage 3: dashboard

### Open with VS Code Live Server

Live Server reloads frontend edits immediately. Its file watcher ignores database files, logs, build outputs and other runtime artifacts, so incoming sensor data does not reload the dashboard. The dashboard checks the compact live endpoint every second and refreshes history, alerts and blockchain details only after a new reading arrives. Restart Live Server once after pulling this change. Opening `http://localhost:5000` directly is also supported.

Start the Flask backend first (keep this terminal running):

```powershell
.\.venv\Scripts\waitress-serve.exe --listen=0.0.0.0:5000 --call backend.app:create_app
```

Right-click `dashboard/index.html` → **Open with Live Server**. CSS and JavaScript use relative paths, so they load at `http://127.0.0.1:5500/dashboard/index.html`. Under **Backend connection**, keep `http://127.0.0.1:5000`, then enter your `.env` `ADMIN_API_KEY` and open the workspace. Live Server serves the frontend; Flask still handles login validation, readings and database operations. If Flask is stopped, the page will explain how to reconnect.

Local Live Server origins on ports 5500 and 5501 are allowed by default. For another host/port, set `CORS_ALLOWED_ORIGINS` in `.env` to a comma-separated list of exact frontend origins (no path or trailing slash), then restart Flask. Existing `.env` files need no changes for the default ports. Never place API keys in frontend source files.

### Open directly with Flask

Open **http://localhost:5000** while Flask/Waitress is running. Enter the `ADMIN_API_KEY` from your `.env` in the workspace access screen. The browser stores the key and backend URL in this tab's sessionStorage so sign-in survives reloads for up to 8 hours. Restored credentials are checked with Flask before opening the workspace. Lock workspace clears the saved session; credentials are not written to localStorage. This is a local demo access gate, not a multi-user identity system.

The dashboard includes shipment registration with configurable thresholds, current sensor readings, package and sample status, temperature/humidity/distance/motion charts, condition alerts, paginated sensor history, transaction hashes and block numbers. Displayed acceleration is zeroed against the first live reading for the selected shipment, so a stationary MPU6050 shows movement near 0 even though the raw stored magnitude includes gravity. It polls the live endpoint every second; readings more than 15 seconds old are visibly marked stale. Click **Verify records** to check database history against the configured contract. Verification shows its coverage and check time, and resets when a new reading arrives. Data is never seeded into the dashboard automatically.

All frontend assets are local HTML/CSS/JavaScript, served by Flask. No frontend build, CDN, npm runtime dependency or internet connection is required to use the dashboard. Charts are SVG and use the actual capture timestamps, with a table offering the underlying values. Select a sensor to change the chart; it charts the current history page. A package selector loads up to 500 packages; the intended scope is a college demonstration.

Optional browser checks use Playwright with the already installed Microsoft Edge browser:

```powershell
npm.cmd ci
npm.cmd run check
npm.cmd run test:ui
```

The test starts its own temporary backend on loopback port 5089, opens a headless browser, verifies workflows and mobile sizing, saves screenshots in `test-results/`, then stops its server. Set `BROWSER_CHANNEL=chrome` to use installed Chrome instead. If neither browser is installed, install one or adapt the launch options to a Playwright-managed Chromium. The Python environment must already be installed. Test database files and screenshots are ignored by Git.

## Project layout and operations

```text
backend/                 Flask API, validation, SQLite, conditions, chain worker
firmware/esp8266/        NodeMCU adapter; working UNO code is not replaced
firmware/tests/          Native test of the exact firmware packet parser
blockchain/contracts/   Permissioned shipment/checkpoint Solidity contract
blockchain/test/        Hardhat contract tests
dashboard/              Responsive dashboard; no build step
scripts/                Simulator and real HTTP/chain/browser smoke tests
tests/                  Backend unit and integration tests
requirements-lock.txt   Full tested Python environment, including build tooling
```

For exact Python dependency reproduction, install `requirements-lock.txt` instead of `requirements.txt`; the lock includes PlatformIO and transitive dependencies used in verification. `pywin32` is Windows-only and should be skipped by its platform marker elsewhere. Hardhat dependencies are separately locked in `blockchain/package-lock.json`; browser test dependencies are in the root lockfile.

Keep the database, `.env` and deployment configuration together when backing up a demo. Schema creation is automatic, but this project does not contain a migration framework for future schema changes. The outbox remains durable in SQLite while the backend or chain worker is down. Stop services with Ctrl+C. Use Waitress for normal runs; Flask's debug reloader is unnecessary for the devices.

Troubleshooting:

- **401 on NodeMCU:** its `DEVICE_API_KEY` must equal the backend device key, not the admin key.
- **404 on ingestion:** register the exact packet ID (`PKG001`) first.
- **No upload / no valid timestamp:** check Wi-Fi, numeric LAN IP, firewall and NTP availability; inspect the USB logs at 115200 baud.
- **Queue fills:** restore the backend/connection or fix the head packet's logged HTTP error. The RAM queue covers roughly one minute at the current send interval.
- **No chain confirmations:** enable blockchain, deploy the contract, run the single queue worker, and check FAILED event types and terminal logs.
- **INVALID after Hardhat restart:** use the reset procedure above; do not relabel the old chain evidence as confirmed.
- **PowerShell blocks npm.ps1:** use `npm.cmd` as shown. No execution policy change is required.

## References

- [ESP8266 Wi-Fi station API](https://esp8266-arduino.readthedocs.io/en/latest/esp8266wifi/station-class.html)
- [PlatformIO NodeMCU board documentation](https://docs.platformio.org/en/latest/boards/espressif8266/nodemcuv2.html)
- [Web3.py transaction documentation](https://web3py.readthedocs.io/en/stable/transactions.html)
- [Hardhat development guide](https://v2.hardhat.org/hardhat-runner/docs/getting-started)

### Unexpected return to the login page in Live Server

Restart Live Server once after changing workspace settings. `.vscode/settings.json` excludes database files, SQLite WAL/SHM files, logs, build outputs and test artifacts from its file watcher. Backend writes should not reload the page. Frontend edits reload normally, and the tab session restores sign-in. Backend connection failures show an error instead of clearing an active workspace.
