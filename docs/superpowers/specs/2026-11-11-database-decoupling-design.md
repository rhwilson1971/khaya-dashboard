# Database Decoupling for Khaya

Move the database out of the application process so it can be hosted
separately — on a Kubernetes home cluster — with its lifecycle (schema
creation, migrations, backups) owned by standalone scripts instead of app
startup.

## Proposed behavior

- Storage moves behind a `db` package: URL-selected backends for SQLite
  (standard library, unchanged for local use) and PostgreSQL (psycopg 3,
  for a cluster-hosted database).
- The application never creates or migrates schema. `Store` verifies the
  schema exists and fails with the exact command to run.
- `python -m db <command>` provisions and maintains databases without the
  dashboard: `init`, `migrate`, `status`, `backup`, `restore`, and
  `import-sqlite` (adopts existing SQLite files, including legacy
  unique-address databases).
- Backups are portable JSON dumps of every data table, restorable into
  either backend; restores preserve identifiers, topology links, and
  PostgreSQL identity sequences.
- The server takes `--db <url>` or `KHAYA_DATABASE_URL`; the default remains
  `data/network.db`. Startup prints the redacted URL it is using.
- Kubernetes manifests in `k8s/` run PostgreSQL as its own StatefulSet, an
  init Job for schema provisioning, and the dashboard Deployment — all
  plain objects, no operators.
- SQLite deployments keep the standard-library-only property; PostgreSQL
  adds exactly one dependency (`psycopg[binary]`).

## Approach

A dialect-translation boundary keeps one copy of the application's SQL.
Store writes portable SQL (`?` placeholders, `ON CONFLICT` upserts,
`BEGIN IMMEDIATE` for serialized writes); the PostgreSQL session translates
placeholders and maps `BEGIN IMMEDIATE` to `pg_advisory_xact_lock`, which
preserves the single-writer guarantee behind topology cycle checks. Schema
DDL exists once per dialect in `db/schema.py` and is applied by versioned,
idempotent migrations recorded in a `schema_migrations` table — including
the legacy devices-table rebuild that app startup used to perform silently.

An alternative — per-backend Store implementations — was rejected: it
duplicates every query and invites drift. Auto-initializing schema at
startup was also rejected: it is exactly the coupling this change removes,
and an init Job that can fail visibly beats a server that self-heals
invisibly.

## Validation

Unit-test URL resolution, SQL splitting, and dialect translation; test
init/migrate idempotency, the legacy rebuild, backup/restore round trips,
and every CLI command against SQLite. Run the full application suite
against real PostgreSQL (containerized, `KHAYA_TEST_POSTGRES_URL`) covering
CRUD, probes, heartbeats, parent links, concurrent writers, and identity
resync after restore. Verify the server boots against PostgreSQL, reports
uninitialized databases with actionable guidance, and serves `/api/snapshot`.
