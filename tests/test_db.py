"""Tests for the standalone db package: URLs, dialects, migrations, and dumps."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from db import Backend, open_backend, redact_url
from db.backend_postgres import PostgresBackend, translate_sql
from db.core import split_sql
from db import dump as dump_module
from db.cli import main
from db.migrations import initialize, migrate, pending
from db.schema import REQUIRED_TABLES
from network.store import Store


class UrlTests(unittest.TestCase):
    """Resolve supported database URLs to the right backend."""

    def test_bare_paths_and_sqlite_urls_open_sqlite(self):
        for url in ('/tmp/network.db', 'sqlite:///tmp/network.db', 'sqlite:relative.db', str(Path('/tmp/network.db'))):
            with self.subTest(url=url):
                self.assertIsInstance(open_backend(url).scheme, str)
                self.assertEqual(open_backend(url).scheme, 'sqlite')

    def test_postgres_urls_open_postgres(self):
        for url in ('postgresql://u:p@khaya-db:5432/khaya', 'postgres://u:p@10.0.10.5:5432/khaya'):
            with self.subTest(url=url):
                self.assertIsInstance(open_backend(url), PostgresBackend)

    def test_unsupported_schemes_and_memory_are_rejected(self):
        for url in ('mysql://user@host/db', 'sqlite::memory:', '', '   '):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    open_backend(url)

    def test_postgres_url_requires_host(self):
        with self.assertRaises(ValueError):
            open_backend('postgresql:///khaya')

    def test_redact_url_hides_passwords(self):
        self.assertEqual(redact_url('postgresql://khaya:secret@khaya-db:5432/khaya'), 'postgresql://khaya@khaya-db:5432/khaya')
        self.assertEqual(redact_url('sqlite:///tmp/network.db'), 'sqlite:///tmp/network.db')

    def test_default_url_points_at_bundled_sqlite(self):
        from db import default_url
        self.assertTrue(default_url().startswith('sqlite:///'))
        self.assertIn('data/network.db', default_url())


class SqlHelperTests(unittest.TestCase):
    """Statement splitting and PostgreSQL translation stay faithful to intent."""

    def test_split_sql_respects_quotes_and_comments(self):
        script = """
        -- create things; not a statement
        CREATE TABLE a (x TEXT); -- trailing comment; ignored
        INSERT INTO a VALUES ('semi;colon');
        SELECT 'it''s' FROM a
        """
        self.assertEqual(split_sql(script), [
            'CREATE TABLE a (x TEXT)',
            "INSERT INTO a VALUES ('semi;colon')",
            "SELECT 'it''s' FROM a",
        ])

    def test_translate_sql_rewrites_placeholders_outside_strings(self):
        self.assertEqual(translate_sql('SELECT * FROM devices WHERE id=?'), 'SELECT * FROM devices WHERE id=%s')
        self.assertEqual(translate_sql("SELECT 'a?b' FROM devices"), "SELECT 'a?b' FROM devices")

    def test_translate_sql_passes_comments_through(self):
        self.assertEqual(translate_sql('SELECT 1 -- keep ? intact\n'), 'SELECT 1 -- keep ? intact\n')

    def test_translate_sql_maps_begin_immediate_to_advisory_lock(self):
        translated = translate_sql('BEGIN IMMEDIATE')
        self.assertIn('pg_advisory_xact_lock', translated)
        self.assertNotIn('BEGIN', translated)


class MigrationTests(unittest.TestCase):
    """Provisioning is idempotent, recorded, and separate from the application."""

    def setUp(self):
        """Create an empty private database file."""
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f'sqlite://{Path(self.tmp.name) / "network.db"}'

    def tearDown(self):
        """Remove the isolated database."""
        self.tmp.cleanup()

    def test_initialize_creates_schema_and_seeds_settings(self):
        report = dict(initialize(self.url))
        self.assertEqual(report['0001_initial_schema'], 'created tables and indexes')
        tables = open_backend(self.url).tables()
        self.assertTrue(set(REQUIRED_TABLES) <= tables)
        store = Store(self.url)
        self.assertEqual(store.settings()['interval'], 30)

    def test_initialize_is_idempotent(self):
        initialize(self.url)
        self.assertEqual(initialize(self.url), [])
        self.assertEqual(pending(open_backend(self.url)), [])

    def test_store_refuses_uninitialized_databases(self):
        with self.assertRaisesRegex(RuntimeError, 'python -m db init'):
            Store(self.url)

    def test_migration_records_legacy_rebuilds(self):
        legacy = Path(self.tmp.name) / 'legacy.db'
        db = sqlite3.connect(legacy)
        try:
            db.execute("CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, target TEXT NOT NULL UNIQUE,"
                       " type TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',"
                       " mac TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', connection TEXT NOT NULL DEFAULT 'wired',"
                       " parent_id TEXT, method TEXT NOT NULL DEFAULT 'icmp', status TEXT NOT NULL DEFAULT 'unknown',"
                       " last_check REAL, last_seen REAL, latency REAL, failures INTEGER NOT NULL DEFAULT 0,"
                       " last_error TEXT NOT NULL DEFAULT '', heartbeat_hash TEXT, created_at REAL NOT NULL)")
            db.commit()
        finally:
            db.close()
        report = dict(migrate(f'sqlite://{legacy}'))
        self.assertEqual(report['0002_drop_unique_target'], 'rebuilt devices without unique target')


class DumpTests(unittest.TestCase):
    """Backups and restores move every table, link, and identifier intact."""

    def setUp(self):
        """Build a populated source database and an empty target database."""
        self.tmp = tempfile.TemporaryDirectory()
        self.source_url = f'sqlite://{Path(self.tmp.name) / "source.db"}'
        self.target_url = f'sqlite://{Path(self.tmp.name) / "target.db"}'
        initialize(self.source_url)
        initialize(self.target_url)
        store = Store(self.source_url)
        router = store.save_device({'name': 'Router', 'target': '192.168.1.1', 'type': 'router'})
        child = store.save_device({'name': 'Desktop', 'target': '192.168.1.10', 'type': 'computer', 'parent_id': router['id']})
        store.record_probe(router['id'], True, 2.5, at=100)
        store.record_probe(child['id'], False, None, at=101)
        store.save_settings({'interval': 45})

    def tearDown(self):
        """Remove the isolated databases."""
        self.tmp.cleanup()

    def test_dump_and_restore_round_trip(self):
        data = dump_module.dump(self.source_url)
        counts = dump_module.restore(self.target_url, data)
        self.assertEqual(counts['devices'], 2)
        restored = Store(self.target_url)
        router, desktop = restored.list_devices()
        self.assertEqual(desktop['parent_id'], router['id'])
        self.assertEqual([o['reachable'] for o in restored.history(router['id'])], [1])
        self.assertEqual(restored.settings()['interval'], 45)

    def test_restore_rejects_foreign_and_future_dumps(self):
        for data in ({'format': 'other'}, {'format': 'khaya-db-dump', 'version': 2, 'tables': {}}, 'not-json-object'):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    dump_module.restore(self.target_url, data)

    def test_restore_requires_initialized_target(self):
        fresh_url = f'sqlite://{Path(self.tmp.name) / "fresh.db"}'
        data = dump_module.dump(self.source_url)
        with self.assertRaisesRegex(RuntimeError, 'not initialized'):
            dump_module.restore(fresh_url, data)

    def test_import_from_sqlite_accepts_legacy_sources(self):
        legacy = Path(self.tmp.name) / 'legacy.db'
        db = sqlite3.connect(legacy)
        try:
            db.execute("CREATE TABLE devices (id TEXT PRIMARY KEY, name TEXT NOT NULL, target TEXT NOT NULL,"
                       " type TEXT NOT NULL, location TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',"
                       " mac TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', connection TEXT NOT NULL DEFAULT 'wired',"
                       " parent_id TEXT, method TEXT NOT NULL DEFAULT 'icmp', status TEXT NOT NULL DEFAULT 'unknown',"
                       " last_check REAL, last_seen REAL, latency REAL, failures INTEGER NOT NULL DEFAULT 0,"
                       " last_error TEXT NOT NULL DEFAULT '', heartbeat_hash TEXT, created_at REAL NOT NULL)")
            db.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, device_id TEXT, at REAL NOT NULL,"
                       " reachable INTEGER, latency REAL, error TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, at REAL NOT NULL, device_id TEXT,"
                       " name TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL)")
            db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT INTO devices VALUES ('a', 'Desktop', '192.168.1.10', 'computer', '', '', '', '',"
                       " 'wired', NULL, 'icmp', 'unknown', NULL, NULL, NULL, 0, '', NULL, 1)")
            db.execute("INSERT INTO settings VALUES ('interval', '60')")
            db.commit()
        finally:
            db.close()
        counts = dump_module.import_from_sqlite(legacy, self.target_url)
        self.assertEqual(counts['devices'], 1)
        store = Store(self.target_url)
        self.assertEqual(store.list_devices()[0]['id'], 'a')
        self.assertEqual(store.settings()['interval'], 60)

    def test_import_from_sqlite_rejects_other_backends(self):
        with self.assertRaises(ValueError):
            dump_module.import_from_sqlite('postgresql://u:p@khaya-db:5432/khaya', self.target_url)


class CliTests(unittest.TestCase):
    """Every command runs without the dashboard application."""

    def setUp(self):
        """Create private databases and output files for CLI runs."""
        self.tmp = tempfile.TemporaryDirectory()
        self.url = f'sqlite://{Path(self.tmp.name) / "network.db"}'
        self.other_url = f'sqlite://{Path(self.tmp.name) / "other.db"}'
        self.backup = Path(self.tmp.name) / 'backup.json'

    def tearDown(self):
        """Remove the isolated files."""
        self.tmp.cleanup()

    def run_cli(self, *argv):
        """Run one command and return its exit code and printed output."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*argv])
        return code, out.getvalue(), err.getvalue()

    def test_init_status_backup_restore_cycle(self):
        code, out, _ = self.run_cli('init', '--url', self.url)
        self.assertEqual(code, 0)
        self.assertIn('created tables and indexes', out)

        code, out, _ = self.run_cli('status', '--url', self.url)
        self.assertEqual(code, 0)
        self.assertIn('applied migrations:', out)
        self.assertIn('rows: devices=0', out)

        store = Store(self.url)
        store.save_device({'name': 'Router', 'target': '192.168.1.1', 'type': 'router'})
        code, out, _ = self.run_cli('backup', '--url', self.url, '--output', str(self.backup))
        self.assertEqual(code, 0)
        self.assertIn('devices: 1', out)
        self.assertEqual(json.loads(self.backup.read_text())['format'], 'khaya-db-dump')

        self.run_cli('init', '--url', self.other_url)
        code, _, err = self.run_cli('restore', '--url', self.other_url, '--input', str(self.backup))
        self.assertEqual(code, 1)
        self.assertIn('--yes', err)
        code, out, _ = self.run_cli('restore', '--url', self.other_url, '--input', str(self.backup), '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(Store(self.other_url).list_devices()[0]['name'], 'Router')

    def test_status_reports_uninitialized_databases(self):
        code, out, _ = self.run_cli('status', '--url', self.url)
        self.assertEqual(code, 1)
        self.assertIn('not initialized', out)

    def test_url_precedence_follows_flag_then_environment(self):
        with patch.dict('os.environ', {'KHAYA_DATABASE_URL': self.other_url}):
            code, out, _ = self.run_cli('init')
            self.assertEqual(code, 0)
            self.assertIn('sqlite', out)
            code, out, _ = self.run_cli('init', '--url', self.url)
            self.assertEqual(code, 0)
        self.assertTrue(open_backend(self.url).tables())
        self.assertTrue(open_backend(self.other_url).tables())

    def test_import_sqlite_command(self):
        self.run_cli('init', '--url', self.url)
        self.run_cli('init', '--url', self.other_url)
        store = Store(self.url)
        store.save_device({'name': 'Laptop', 'target': '192.168.1.5', 'type': 'computer'})
        source = str(Path(self.tmp.name) / 'network.db')
        code, out, _ = self.run_cli('import-sqlite', '--sqlite', source, '--url', self.other_url, '--yes')
        self.assertEqual(code, 0)
        self.assertIn('devices: 1', out)

    def test_backend_instances_pass_through_unchanged(self):
        backend = open_backend(self.url)
        self.assertIsInstance(backend, Backend)
        self.assertIs(open_backend(backend), backend)
