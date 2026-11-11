# Database Decoupling Implementation Plan

**Goal:** Separate the database from the application so it can be hosted on
a Kubernetes home cluster, with provisioning scripts that run independently.

**Architecture:** A standalone `db` package owns backends (SQLite via the
standard library, PostgreSQL via psycopg 3), dialect schemas, migrations,
portable dumps, and a CLI. The application depends on it; it never depends
on the application. Kubernetes manifests run PostgreSQL and the dashboard
as separate workloads with a schema-init Job.

**Tech stack:** Python 3.10+, SQLite (stdlib) or PostgreSQL (psycopg 3),
standard-library unittest, vanilla JS, plain Kubernetes objects.

## Tasks

- [x] Extract the storage contract (`db/core.py`): connection scope, table
  listing, script application, URL redaction, and quote/comment-aware SQL
  splitting, with tests in tests/test_db.py.
- [x] Implement the SQLite backend (`db/backend_sqlite.py`) preserving the
  previous connection behavior (foreign keys on, 15 s busy timeout, one
  connection per operation) plus the unchecked-script path for legacy
  rebuilds.
- [x] Implement the PostgreSQL backend (`db/backend_postgres.py`) with
  placeholder/dialect translation, `BEGIN IMMEDIATE` mapped to an advisory
  transaction lock, dict rows, and lazy psycopg import with a helpful
  install message.
- [x] Move schema DDL into `db/schema.py` with one dialect each, and
  `SETTINGS_DEFAULTS` as the single source for seeds and runtime defaults.
- [x] Add versioned idempotent migrations (`db/migrations.py`), including
  the legacy unique-address devices rebuild, recorded in
  `schema_migrations`.
- [x] Add portable dump/restore/import (`db/dump.py`): two-phase device
  inserts for self-referencing topology, identifier preservation, and
  PostgreSQL identity resynchronization.
- [x] Add the CLI (`db/cli.py`, `python -m db`) for init, migrate, status,
  backup, restore, and import-sqlite, with `--url` / `KHAYA_DATABASE_URL`
  resolution and connection retries for container starts.
- [x] Refactor `network/store.py` onto the backend: no schema creation,
  portable upserts, normalized observation values, initialization failure
  with actionable guidance. Update `network/server.py` for `--db` URLs,
  the environment variable, and the redacted URL in startup output.
- [x] Update existing tests (store, server, monitor) to initialize schema
  explicitly; port the legacy migration test to the db package; add
  tests/test_db.py and the PostgreSQL integration suite
  tests/test_db_postgres.py (skipped without `KHAYA_TEST_POSTGRES_URL`).
- [x] Update the Dockerfile and requirements.txt (psycopg optional) and
  verify the image against a real PostgreSQL container.
- [x] Write the Kubernetes manifests in `k8s/` (namespace, secret,
  headless service, StatefulSet, init Job, app Deployment with hostPort,
  app Service) and k8s/README.md.
- [x] Document the decision in docs/database-options.md, add the design
  spec, and update README.md with database setup, URLs, scripts, and
  cluster deployment.

## Execution

Implement inline in this session, with failing tests before behavioral
changes where practical. Do not modify any real inventory database; the
`data/` directory is gitignored and untouched.

## Validation results

- `python3 -m unittest discover -s tests` — 91 tests, all passing locally
  (6 PostgreSQL integration tests skipped without a configured URL).
- Full suite inside the `khaya:latest` image against PostgreSQL 17
  (`postgres:17-alpine` container, psycopg 3.2): 91/91 passing, including
  CRUD across connections, probe state transitions, heartbeat rotation and
  expiry, parent-link cycle rejection, eight concurrent writers under the
  advisory lock, and dump/restore with identity resynchronization.
- Live smoke test: the server ran against PostgreSQL, answered
  `/api/snapshot` with inventory JSON, and rejected startup against an
  uninitialized database with `python -m db init` guidance.
- `node --test tests/frontend.test.cjs` and `node --check static/app.js`
  pass unchanged (no frontend changes).
