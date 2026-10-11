# Device DNS Display Implementation Plan

**Goal:** Show the home DNS domain on the overview and a full DNS-name
property on every device, sourced from its hostname target or Pi-hole PTR.

**Architecture:** Extend the existing DNS transport and validated packet
parser to query PTR records and preserve answer TTLs. Keep a bounded,
thread-safe property cache in a separate server-owned service. Enrich only
device-detail GET responses, keeping inventory, snapshots, and health
monitoring independent of DNS property lookups.

**Tech stack:** Python standard library, unittest, vanilla JavaScript,
Node test runner.

## Tasks

- [x] Extend tests/test_dns.py wire fixtures with configurable TTL values;
  add failing PTR tests for local names, short-name expansion, unrelated
  records, aliases, malformed names, NXDOMAIN, and transport errors.
- [x] In network/dns.py, allow question types A (1) and PTR (12), retain
  per-record TTL, and add reverse_lookup(address) returning (names, ttl).
  Query address.reverse_pointer only through Pi-hole, sharing the existing
  deadline and TCP fallback. Preserve existing forward-lookup checks.
- [x] Add tests/test_device_dns.py and network/device_dns.py with a
  DeviceDNS.describe(target) API returning dns_name and dns_status. Expand
  hostname targets without querying DNS; resolve IP targets with PTR.
  Cache at most 256 entries, successful entries for TTL capped at an hour,
  absent records for 30 seconds, and failures for five seconds. Use target
  as the cache key, copy results, and avoid holding the lock during lookup.
- [x] Test and enrich only GET /api/devices/:id in network/server.py using
  a server-owned DeviceDNS instance. Inject a no-network instance in the
  HTTP test fixture. Assert inventory and snapshots remain unchanged.
- [x] Add frontend assertions that renderOverview includes the configured
  domain, openDetail includes the entire returned name, unavailable and
  absent results have distinct labels, and sample details issue no fetch.
- [x] Update static/app.js using existing notice and detail-grid styling.
  Escape the server-supplied domain and name as all other inventory text.
- [x] Document DNS-name properties, Pi-hole reverse records, caching, and
  missing-record behavior in README.md. Add docstrings to touched functions.
- [x] Run python3 -m unittest discover -s tests -v, node --test
  tests/frontend.test.cjs, node --check static/app.js, and git diff --check.
- [x] Review implementation against the approved design and verify a live
  PTR query against Pi-hole if network access permits.

## Execution

Execute inline in this session with failing tests before code changes.
Use the existing sample network without DNS activity. Do not modify
the live inventory database, monitoring targets, or Pi-hole records.

## Validation results

- 61 Python tests and 20 frontend tests passed.
- JavaScript syntax and Git whitespace checks passed.
- Live Pi-hole PTR lookup for 10.0.10.53 completed with no usable home-domain
  reverse name; the dashboard correctly displays No DNS name found.
- Independent code review reported no material findings.
- All functions in touched Python files have docstrings.
- Existing inventory, targets, health observations, and Pi-hole records were
  left unchanged. Restart the collector and refresh the dashboard to apply.
