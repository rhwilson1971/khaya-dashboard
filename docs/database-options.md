# Database options for Khaya on a Kubernetes home cluster

Khaya's storage needs are modest and specific: a handful of small relational
tables (inventory, topology, capped observations, events, settings),
transactional writes with foreign keys, and a write rate of a few dozen rows
per monitoring cycle. That profile narrows the field quickly — this document
compares the realistic free options for hosting it on a home cluster, records
why PostgreSQL was chosen, and lists what would change if you picked
differently.

## Requirements recap

| Requirement | Why it matters |
| --- | --- |
| Network-attached client/server | The database must be a separate service from the app pod; SQLite's file locking forbids network filesystems |
| Real transactions with foreign keys | Device edits, cycle checks, and probe recording rely on atomic multi-statement writes |
| Low resource footprint | Home clusters run on surplus hardware; idle memory matters more than throughput |
| Free and self-hostable | No per-node licensing, no mandatory telemetry, no paid tiers required |
| Boring to operate on 1–3 nodes | Single-writer home workloads do not need consensus quorums |

## Comparison

| Option | License / cost | Idle footprint | Kubernetes story | Fit for Khaya |
| --- | --- | --- | --- | --- |
| **PostgreSQL** (chosen) | PostgreSQL License (free, BSD-like) | ~50–150 MiB with tuned settings; official `postgres:17-alpine` runs in 256 MiB | Excellent: plain StatefulSet, or operators below | **Best.** Full SQL, identity columns, advisory locks to mirror SQLite's single-writer behavior, mature drivers and tooling |
| **MariaDB** | GPL (free) | Similar to PostgreSQL | Good: `mariadb-operator` (CNCF sandbox), Bitnami charts, or plain StatefulSet | Good, but weaker fit: no `ON CONFLICT` upserts (rewrites needed), advisory-lock equivalents clunkier, BOOLEAN handling differs |
| **SQLite on a PVC** | Public domain | Negligible | Trivial — but see below | **Not a real separation.** The file must live on the app pod's volume; only one pod can touch it, and moving the app means moving the volume |
| **SQLite + Litestream** | Free (Apache-2.0 sidecar) | Negligible | Adds replication to S3/NFS, not a networked database | Better durability for the embedded model, still coupled to the app pod |
| **rqlite** (Raft-replicated SQLite) | MIT (free) | ~30–80 MiB/node | Plain StatefulSet; niche community | Workable — JSON-over-HTTP speaks to a stdlib client — but small ecosystem, weaker tooling, and JOIN-heavy queries serialize through the Raft leader |
| **CockroachDB** | Free core; BSL for enterprise features | ~1 GiB+ per node | Official operator | Overkill: distributed SQL consensus for a 43-device inventory |
| **Valkey / Redis** | BSD (free) | Tiny | Excellent operators | **Wrong shape.** No relational schema, no multi-table transactions; would move integrity into app code |
| **MongoDB** | SSPL (not OSI-free) | Moderate | Official operator | Wrong shape (document store) and licensing friction for a home lab |
| Hosted free tiers (Neon, Supabase Postgres) | Free tiers exist | Zero (managed) | N/A — not on your cluster | Viable fallback for a lab, but the data leaves the house and availability depends on an external service |

## PostgreSQL deployment styles on a home cluster

All free; pick by how much machinery you want to own:

1. **Plain StatefulSet + PVC** *(what `k8s/` ships)* — every object lives in
   this repository, one pod, backups via `python -m db backup`. Nothing to
   install on the cluster; easiest to reason about and repair.
2. **CloudNativePG operator** — CRDs and a controller that add automated
   failover, rolling restarts, and scheduled backups to object storage. The
   natural upgrade path if the single instance ever feels fragile; requires
   `kubectl apply` of the operator bundle first.
3. **Zalando Postgres operator (Spilo)** — the veteran HA option; heavier and
   more opinionated (Patroni, leader election, sidecars).
4. **Bitnami Helm chart** — one-command install with sensible defaults; the
   database definition moves out of your repository into chart values.

Switching between these changes only the manifests — the application talks
plain PostgreSQL over `KHAYA_DATABASE_URL` regardless.

## Why PostgreSQL for this project

- **SQL compatibility.** The schema and queries port with one dialect file
  (`db/schema.py`): `ON CONFLICT` upserts, `REAL`/`DOUBLE PRECISION`,
  identity columns replace SQLite's implicit rowids, and
  `pg_advisory_xact_lock` reproduces `BEGIN IMMEDIATE`'s serialized writes
  so topology cycle checks stay airtight.
- **A single pip dependency.** `psycopg[binary]` is the only addition to a
  previously stdlib-only app, and SQLite deployments still need nothing.
- **Home-cluster sized.** Runs well within 256 MiB with the default
  configuration; the official Alpine image is ~80 MiB.
- **Escapability.** If requirements grow (HA, point-in-time recovery), the
  operator ecosystem takes over without application changes.

## What choosing a different engine would cost

- **MariaDB/MySQL:** rewrite upserts to `ON DUPLICATE KEY UPDATE`, replace
  the advisory lock with `GET_LOCK` semantics, change placeholder handling
  and identity columns. One new backend module plus dialect SQL — the
  `db/` package structure already anticipates this.
- **SQLite-on-PVC:** keep the current backend, drop PostgreSQL entirely; the
  app stays bound to one pod and its volume. The standalone scripts
  (`init`, `backup`, `restore`) remain useful as Jobs against the PVC.
- **rqlite:** write an HTTP-based backend speaking its JSON API (possible
  with the standard library), and give up `psycopg`-grade tooling.

The storage interface in `db/core.py` (connection scope, table listing,
script application) is the extension point for any of these.
