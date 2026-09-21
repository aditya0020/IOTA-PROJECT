# Validation record

Verified incrementally in the provided Windows workspace on 22 September 2026.

| Stage | Check | Result |
|---|---|---|
| Pipeline, before blockchain/dashboard implementation | Backend pytest suite | 31 passed |
| Pipeline | Exact firmware C++ parser with supplied `PKG001` packet and 10 rejection cases | Passed with `-Wall -Wextra -Werror` |
| Pipeline | Real loopback HTTP simulator → Flask → SQLite → history API | Passed |
| Pipeline | PlatformIO `nodemcuv2`, ESP8266 core 3.1.2, ArduinoJson 7.4.2 | Firmware built; RAM 36,436 / 81,920 bytes, flash 304,743 / 1,044,464 bytes |
| Blockchain, before dashboard implementation | Hardhat compile and contract tests | Solidity compiled; 7 passed |
| Blockchain | Real localhost deployment and Web3 integration | Registration, checkpoints, verification, crash recovery and tamper detection passed |
| Final backend | Extended pytest suite | 40 passed |
| Dashboard | JavaScript syntax check | Passed |
| Dashboard | Playwright against installed Edge and temporary Waitress backend | Authentication, registration, empty/live states, alerts, charts, pagination, offline verification, mobile sizing, outage recovery and logout passed |

Environment: Python 3.13.2, Node.js 22.14.0, npm 10.9.2, Hardhat 2.26.3, Solidity 0.8.28. Dependency lockfiles capture the tested environment. Third-party ESP8266 tooling emitted Python escape-sequence warnings; Web3's `websockets` dependency emitted a deprecation warning. These did not fail the checks.

Commands and setup are in [README.md](../README.md). Browser screenshots are generated locally at `test-results/dashboard-desktop.png` and `test-results/dashboard-mobile.png` and are not source assets. Simulators use temporary test databases and separate test credentials.

## Physical hardware still requires acceptance

The workspace initially contained no Arduino source. No UNO firmware was rewritten. The ESP8266 adapter was compiled against the supplied serial protocol and pin configuration, but no board was flashed or electrically exercised in this session. Confirm serial framing, matching ACK sequence and actual ACK timing, Wi-Fi reconnect, NTP availability, and persisted live packets using the README's hardware acceptance steps.

The serial ACK acknowledges the RAM queue. It does not promise power-loss durability. Backend-to-blockchain integration uses a real local development chain; public-network deployment, real-time guarantees and production security certification were not performed.
