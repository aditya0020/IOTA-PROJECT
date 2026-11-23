# Blockchain setup

SQLite remains the source of truth for telemetry. Ethereum stores immutable
SHA-256 proof metadata. Individual `ReadingAnchored` proofs are the primary
integrity check; legacy checkpoints remain secondary information.

## Local Hardhat

```powershell
npm.cmd --prefix blockchain ci
npm.cmd --prefix blockchain run compile
npm.cmd --prefix blockchain test
npm.cmd --prefix blockchain run node
```

Keep that terminal open. In another terminal:

```powershell
npm.cmd --prefix blockchain run deploy
```

The deployment writes `blockchain/deployment.json`. Configure `.env` for local
mode:

```dotenv
DATABASE_PATH=data/supply_chain.sqlite3
BLOCKCHAIN_ENABLED=true
BLOCKCHAIN_RPC_URL=http://127.0.0.1:8545
BLOCKCHAIN_CHAIN_ID=31337
BLOCKCHAIN_DEPLOYMENT=blockchain/deployment.json
BLOCKCHAIN_ACCOUNT_INDEX=1
BLOCKCHAIN_PRIVATE_KEY=
BLOCKCHAIN_CONTRACT_ADDRESS=
BLOCKCHAIN_EXPLORER_URL=
BLOCKCHAIN_ANCHOR_EVERY_N_READINGS=1
BLOCKCHAIN_ANCHOR_ALERTS_IMMEDIATELY=true
```

Start Flask and the worker manually:

```powershell
.\.venv\Scripts\python.exe -m waitress --listen=0.0.0.0:5000 --call backend.app:create_app
```

```powershell
.\.venv\Scripts\python.exe -m flask --app backend.app:create_app sync-chain --watch
```

Hardhat state is temporary. If you restart/reset the node, old local transaction
hashes no longer prove anything on the new chain. Use a fresh database for a
fresh chain.

## Sepolia

Sepolia is configured manually. Use separate wallets for deployer/admin and
backend writer. Never commit private keys.

Set these values in `.env`:

```dotenv
BLOCKCHAIN_ENABLED=true
BLOCKCHAIN_RPC_URL=<your-sepolia-rpc-url>
SEPOLIA_RPC_URL=<your-sepolia-rpc-url>
BLOCKCHAIN_CHAIN_ID=11155111
BLOCKCHAIN_PRIVATE_KEY=<writer-private-key>
BLOCKCHAIN_WRITER_ADDRESS=<writer-public-address>
DEPLOYER_PRIVATE_KEY=<deployer-private-key>
BLOCKCHAIN_EXPLORER_URL=https://sepolia.etherscan.io
```

Deploy:

```powershell
npm.cmd --prefix blockchain run compile
npm.cmd --prefix blockchain run deploy:sepolia
```

The deployment writes `blockchain/deployment.json`. Use a separate database from
the local Hardhat database if you move to Sepolia. Do not copy local-chain
confirmed proof rows or transaction hashes into a Sepolia database.

## Verification

Package verification returns the primary state from individual reading proofs:

- `VERIFIED`: retained readings match their individual on-chain proofs
- `PARTIAL` or `PENDING`: some proofs are missing or still being submitted
- `INVALID`: an individual proof mismatch was detected
- `UNAVAILABLE`: the configured chain cannot be checked
- `OFFLINE`: blockchain is disabled

The per-reading **Verify reading** action checks one row against its on-chain
proof. Legacy checkpoint state remains informational and can be stale after
database cleanup without overriding valid individual proofs.

## Demo and tests

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_blockchain --signed
.\.venv\Scripts\python.exe -m scripts.demo_tamper
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix blockchain test
npm.cmd run check
npm.cmd run test:ui
```
