# Home Network Implementation Plan

> Execute inline in this session, as authorized by the user's instruction to move forward.

**Goal:** Build a locally hosted home network inventory, monitor, and interactive topology dashboard.

**Architecture:** A standard-library Python API owns SQLite persistence. A bounded background collector checks ICMP targets and heartbeat expiry. A buildless frontend polls the API and renders inventory, history, settings, and a pan/zoom topology.

**Tech Stack:** Python 3.10+, SQLite, HTML, CSS, JavaScript, SVG, unittest.

## File responsibilities
- `network/store.py`: inventory validation, persistence, topology validation, observations, heartbeat secrets, settings.
- `network/monitor.py`: safe private-address resolution, ICMP probe, concurrent collection, explicit subnet discovery.
- `network/server.py`: local HTTP API, static files, origin protection, heartbeat endpoint, collector lifecycle.
- `static/index.html`, `static/styles.css`, `static/app.js`: accessible responsive dashboard and device dialogs.
- `tests/test_store.py`, `tests/test_monitor.py`, `tests/test_server.py`: meaningful backend and API verification.
- `README.md`: setup, collector limitations, heartbeat instructions, network exposure.

## Task 1: Persistent inventory and observations
- [x] Write tests for create/update/delete, restart persistence, invalid targets, unknown parents, cycles, failure thresholds, heartbeat expiry, history, settings.
- [x] Run `python3 -m unittest discover -s tests -v`; confirm missing implementation fails.
- [x] Implement Store with parameterized SQLite operations, explicit transaction boundaries and documented functions.
- [x] Re-run tests until all inventory and observation cases pass.

## Task 2: Collector and discovery
- [x] Write tests for private IPv4 validation, ICMP parsing, timeout/error distinctions, discovery bounds and duplicate import behavior.
- [x] Run tests to confirm missing collector fails.
- [x] Implement bounded probing with argument-based subprocess calls, independent periodic collection, and asynchronous discovery jobs.
- [x] Run collector tests and attempt a real localhost ICMP check; sandbox denied ICMP and the result was correctly unknown with a permission error.

## Task 3: API and browser dashboard
- [x] Write HTTP integration tests for persistence, validation, static delivery, heartbeat authentication, origin rejection, and explicit discovery.
- [x] Run tests before implementation.
- [x] Implement HTTP handlers and startup entry point; then build overview, inventory, graph, details, events, settings, sample mode, and manual entry.
- [x] Verify API with the real HTTP parser over an in-memory wire transport; verify frontend render, filters, escaping, and topology with Node. Live launch and browser visual verification are blocked by sandbox sockets and browser URL policy, as recorded below.

## Task 4: Review and delivery
- [x] Write complete run/heartbeat/discovery documentation.
- [x] Check docstrings, syntax, and full tests; request independent code review and address material findings.
- [x] Record verification and provide local URL and launch instructions.


## Verification result

Implemented the local inventory service, independent ICMP and heartbeat scheduling, explicit bounded discovery/import, overview, inventory, activity, settings, device dialogs, SVG topology/presentation, and a bundled heartbeat agent. All Python functions have docstrings. No external runtime packages or frontend build are required.

- 24 Python tests pass, including SQLite persistence, topology validation, authenticated heartbeats, independent expiry during a stalled ICMP batch, discovery/import, and HTTP wire parsing.
- 8 frontend tests pass, including sample/live mode races, settings reconnection, disabled offline settings, escaping, combined filters, and exact configured graph edges.
- Python compile, JavaScript syntax, CLI help, HTML IDs and bundled asset checks pass.
- Independent review found three issues and one reconnect edge; all were fixed with failing-then-passing regression tests, and the reviewer confirmed the bounded fixes.
- Live server binding is denied by the session sandbox. ICMP localhost echo was denied by permissions. Browser policy rejected the file-URL preview. Live browser/mobile visual QA and real LAN checks remain unverified; no real devices were scanned or configured.
- Graph generation 2026-10-06T01:34:33Z did not track newly created files; coverage was checked and direct-source fallback used for verification and review.

Launch outside the restricted session with `python3 -m network.server`, then open http://127.0.0.1:8765. See README for LAN access and heartbeat setup.
