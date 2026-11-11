"""Portable logical dumps: JSON backups, restores, and SQLite imports.

The dump format is backend-neutral JSON, so a backup taken from SQLite can be
restored into PostgreSQL and vice versa. Devices are inserted in two phases
(parent links applied after every row exists) so self-referencing topology
restores without ordering tricks.
"""
import json
import time
from pathlib import Path
from db import Backend, open_backend
from db.schema import DATA_TABLES, REQUIRED_TABLES

DUMP_FORMAT = 'khaya-db-dump'
DUMP_VERSION = 1
# Child tables first so foreign keys never block the wipe.
WIPE_ORDER = ('observations', 'events', 'devices', 'settings')
# Tables whose identity sequence needs resyncing after explicit-id restores.
IDENTITY_TABLES = ('observations', 'events')


def _read_tables(source, required):
    """Read every data table into a portable mapping."""
    backend = source if isinstance(source, Backend) else open_backend(source)
    missing = [table for table in required if table not in backend.tables()]
    if missing:
        raise RuntimeError(f'Database is not initialized (missing {", ".join(missing)}). Run `python -m db init` first.')
    data = {'format': DUMP_FORMAT, 'version': DUMP_VERSION, 'created_at': time.time(),
            'backend': backend.scheme, 'tables': {}}
    with backend.connection() as db:
        for table in DATA_TABLES:
            data['tables'][table] = [dict(row) for row in db.execute(f'SELECT * FROM {table}')]
    return data


def dump(source):
    """Return a portable dump of an initialized database."""
    return _read_tables(source, REQUIRED_TABLES)


def _validate(data):
    """Check dump structure before anything is written."""
    if not isinstance(data, dict) or data.get('format') != DUMP_FORMAT:
        raise ValueError('Not a Khaya database dump; create one with `python -m db backup`.')
    if data.get('version', 0) > DUMP_VERSION:
        raise ValueError(f'Dump version {data["version"]} is newer than this tool understands ({DUMP_VERSION}).')
    tables = data.get('tables')
    if not isinstance(tables, dict) or any(not isinstance(tables.get(t, []), list) for t in DATA_TABLES):
        raise ValueError('Dump is missing its table data.')
    for table in DATA_TABLES:
        for row in tables[table]:
            if not isinstance(row, dict) or not row:
                raise ValueError(f'Dump table {table} contains an invalid row.')
    return tables


def _insert_rows(db, table, rows, skip_column=None):
    """Insert dump rows with explicit column lists; optionally defer one column."""
    inserted = 0
    for row in rows:
        columns = [column for column in row if column != skip_column]
        placeholders = ','.join('?' for _ in columns)
        db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({placeholders})',
                   [row[column] for column in columns])
        inserted += 1
    return inserted


def restore(target, data):
    """Replace the target database's data with dump contents in one transaction."""
    backend = target if isinstance(target, Backend) else open_backend(target)
    tables = _validate(data)
    missing = [table for table in REQUIRED_TABLES if table not in backend.tables()]
    if missing:
        raise RuntimeError(f'Target database is not initialized (missing {", ".join(missing)}). Run `python -m db init` first.')
    counts = {}
    with backend.connection() as db:
        # One serialized transaction: readers see the old or the new data.
        db.execute('BEGIN IMMEDIATE')
        for table in WIPE_ORDER:
            db.execute(f'DELETE FROM {table}')
        devices = tables['devices']
        counts['devices'] = _insert_rows(db, 'devices', devices, skip_column='parent_id')
        for row in devices:
            if row.get('parent_id') is not None:
                db.execute('UPDATE devices SET parent_id=? WHERE id=?', (row['parent_id'], row['id']))
        for table in ('observations', 'events', 'settings'):
            counts[table] = _insert_rows(db, table, tables[table])
        if backend.scheme == 'postgres':
            from db.backend_postgres import resync_identity
            for table in IDENTITY_TABLES:
                resync_identity(db, table)
    return counts


def import_from_sqlite(path, target):
    """Copy an existing SQLite database file into the target backend.

    Accepts databases created by older application versions (they lack the
    schema_migrations table) as long as the data tables are present.
    """
    source = open_backend(path if isinstance(path, (str, Path)) else str(path))
    if source.scheme != 'sqlite':
        raise ValueError('import-sqlite expects a SQLite file path; use backup/restore between other backends.')
    data = _read_tables(source, DATA_TABLES)
    return restore(target, data)
