"""Shared PostgreSQL test helpers.

Database tests run against the disposable database named by
KHAYA_TEST_POSTGRES_URL. They are skipped when it is unset or psycopg is not
installed, so the pure unit tests still run without a database.
"""
import contextlib
import os
import unittest

POSTGRES_URL = os.environ.get('KHAYA_TEST_POSTGRES_URL')
try:
    import psycopg  # noqa: F401
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False

requires_postgres = unittest.skipUnless(
    POSTGRES_URL and PSYCOPG_AVAILABLE,
    'set KHAYA_TEST_POSTGRES_URL (and install psycopg) to run database tests',
)


def reset_database(url):
    """Provision the schema, then empty every data table and reseed settings."""
    from db import open_backend
    from db.migrations import initialize
    initialize(url)
    with open_backend(url).connection() as db:
        db.execute('TRUNCATE observations, events, devices, settings RESTART IDENTITY CASCADE')
    initialize(url)


def drop_schema(url):
    """Remove every Khaya table so the database looks uninitialized."""
    from db import open_backend
    with open_backend(url).connection() as db:
        db.execute('DROP TABLE IF EXISTS observations, events, devices, settings, schema_migrations CASCADE')


@contextlib.contextmanager
def uninitialized_database(url):
    """Temporarily drop the schema and restore it when the block exits."""
    from db.migrations import initialize
    drop_schema(url)
    try:
        yield url
    finally:
        initialize(url)


@contextlib.contextmanager
def postgres_database():
    """Reset the shared test database and yield its URL."""
    reset_database(POSTGRES_URL)
    yield POSTGRES_URL
