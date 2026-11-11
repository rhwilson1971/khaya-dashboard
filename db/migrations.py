"""Named, ordered, idempotent schema migrations applied by `python -m db`.

The application never creates or changes schema at runtime; provisioning and
upgrades happen here so they can run standalone, from a Job in a cluster, or
against any supported backend.
"""
import json
import time
from db import Backend, open_backend
from db.schema import LEGACY_SQLITE_REBUILD, MIGRATIONS_TABLE, SCHEMAS, SETTINGS_DEFAULTS


class Migration:
    """One named schema change; apply() returns a short result note."""

    def __init__(self, name, description, apply):
        """Store the identity and callable that performs the change."""
        self.name = name
        self.description = description
        self.apply = apply


def _apply_initial_schema(backend):
    """Create every table and index for the backend's dialect."""
    backend.run_script(SCHEMAS[backend.scheme])
    return 'created tables and indexes'


def _rebuild_legacy_devices(backend):
    """Rebuild legacy SQLite devices tables that enforce unique addresses."""
    if backend.scheme != 'sqlite':
        return 'not applicable to this backend'
    with backend.connection() as db:
        row = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='devices'").fetchone()
    if not row or 'UNIQUE' not in (row['sql'] or ''):
        return 'already current'
    backend.run_script_unchecked(LEGACY_SQLITE_REBUILD)
    return 'rebuilt devices without unique target'


MIGRATIONS = (
    Migration('0001_initial_schema', 'Create devices, observations, events, settings, and schema_migrations.', _apply_initial_schema),
    Migration('0002_drop_unique_target', 'Rebuild legacy devices tables that still enforce unique addresses.', _rebuild_legacy_devices),
)


def _seed_settings(backend):
    """Insert default monitoring settings without overwriting saved values."""
    with backend.connection() as db:
        db.executemany('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING',
                       [(key, json.dumps(value)) for key, value in SETTINGS_DEFAULTS.items()])


def applied_names(backend):
    """Return recorded migration names; empty when nothing is applied yet."""
    if 'schema_migrations' not in backend.tables():
        return set()
    with backend.connection() as db:
        return {row['name'] for row in db.execute('SELECT name FROM schema_migrations')}


def pending(backend):
    """Return migrations that have not been recorded for this database."""
    recorded = applied_names(backend)
    return [migration for migration in MIGRATIONS if migration.name not in recorded]


def migrate(source):
    """Apply pending migrations, seed defaults, and return a name/note report."""
    backend = source if isinstance(source, Backend) else open_backend(source)
    with backend.connection() as db:
        db.execute(MIGRATIONS_TABLE[backend.scheme])
    report = []
    for migration in pending(backend):
        note = migration.apply(backend)
        with backend.connection() as db:
            db.execute('INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?) ON CONFLICT(name) DO NOTHING',
                       (migration.name, time.time()))
        report.append((migration.name, note))
    _seed_settings(backend)
    return report


def initialize(source):
    """Prepare a new or existing database; an explicit alias for migrate."""
    return migrate(source)
