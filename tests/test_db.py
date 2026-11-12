"""Tests for the standalone db package: URLs, SQL helpers, migrations, and dumps."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from db import Backend, open_backend, redact_url, resolve_url
from db.backend_postgres import PostgresBackend
from db.core import split_sql
from db import dump as dump_module
from db.cli import main
from db.migrations import initialize, pending
from db.schema import REQUIRED_TABLES
from network.store import Store
from tests.support import POSTGRES_URL, requires_postgres, reset_database, uninitialized_database


class UrlTests(unittest.TestCase):
    """Resolve supported database URLs to the PostgreSQL backend."""

    def test_postgres_urls_open_postgres(self):
        for url in ('postgresql://u:p@khaya-db:5432/khaya', 'postgres://u:p@10.0.10.5:5432/khaya'):
            with self.subTest(url=url):
                self.assertIsInstance(open_backend(url), PostgresBackend)

    def test_non_postgres_urls_are_rejected(self):
        for url in ('/tmp/network.db', 'sqlite:///tmp/network.db', 'sqlite::memory:', 'mysql://user@host/db', '', '   '):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    open_backend(url)

    def test_postgres_url_requires_host(self):
        with self.assertRaises(ValueError):
            open_backend('postgresql:///khaya')

    def test_redact_url_hides_passwords(self):
        self.assertEqual(redact_url('postgresql://khaya:secret@khaya-db:5432/khaya'), 'postgresql://khaya@khaya-db:5432/khaya')

    def test_resolve_url_prefers_explicit_then_environment(self):
        env_url = 'postgresql://khaya:secret@khaya-db:5432/khaya'
        with patch.dict('os.environ', {'KHAYA_DATABASE_URL': env_url}):
            self.assertEqual(resolve_url(), env_url)
            self.assertEqual(resolve_url('postgresql://other@host/db'), 'postgresql://other@host/db')
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_url()


class SqlHelperTests(unittest.TestCase):
    """Statement splitting stays faithful to intent."""

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


@requires_postgres
class MigrationTests(unittest.TestCase):
    """Provisioning is idempotent, recorded, and separate from the application."""

    def setUp(self):
        """Start every test from an initialized, empty database."""
        self.url = POSTGRES_URL
        reset_database(self.url)

    def test_initialize_creates_schema_and_seeds_settings(self):
        with uninitialized_database(self.url):
            report = dict(initialize(self.url))
            self.assertEqual(report['0001_initial_schema'], 'created tables and indexes')
            tables = open_backend(self.url).tables()
            self.assertTrue(set(REQUIRED_TABLES) <= tables)
            store = Store(self.url)
            self.assertEqual(store.settings()['interval'], 30)

    def test_initialize_is_idempotent(self):
        self.assertEqual(initialize(self.url), [])
        self.assertEqual(pending(open_backend(self.url)), [])

    def test_store_refuses_uninitialized_databases(self):
        with uninitialized_database(self.url):
            with self.assertRaisesRegex(RuntimeError, 'python -m db init'):
                Store(self.url)


@requires_postgres
class DumpTests(unittest.TestCase):
    """Backups and restores move every table, link, and identifier intact."""

    def setUp(self):
        """Build a populated database for each test."""
        self.url = POSTGRES_URL
        reset_database(self.url)
        store = Store(self.url)
        router = store.save_device({'name': 'Router', 'target': '192.168.1.1', 'type': 'router'})
        child = store.save_device({'name': 'Desktop', 'target': '192.168.1.10', 'type': 'computer', 'parent_id': router['id']})
        store.record_probe(router['id'], True, 2.5, at=100)
        store.record_probe(child['id'], False, None, at=101)
        store.save_settings({'interval': 45})

    def test_dump_and_restore_round_trip(self):
        data = dump_module.dump(self.url)
        counts = dump_module.restore(self.url, data)
        self.assertEqual(counts['devices'], 2)
        restored = Store(self.url)
        router, desktop = restored.list_devices()
        self.assertEqual(desktop['parent_id'], router['id'])
        self.assertEqual([o['reachable'] for o in restored.history(router['id'])], [1])
        self.assertEqual(restored.settings()['interval'], 45)

    def test_restore_rejects_foreign_and_future_dumps(self):
        for data in ({'format': 'other'}, {'format': 'khaya-db-dump', 'version': 2, 'tables': {}}, 'not-json-object'):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    dump_module.restore(self.url, data)

    def test_restore_requires_initialized_target(self):
        data = dump_module.dump(self.url)
        with uninitialized_database(self.url):
            with self.assertRaisesRegex(RuntimeError, 'not initialized'):
                dump_module.restore(self.url, data)


@requires_postgres
class CliTests(unittest.TestCase):
    """Every command runs without the dashboard application."""

    def setUp(self):
        """Create an initialized database and output file for CLI runs."""
        self.url = POSTGRES_URL
        reset_database(self.url)
        self.tmp = tempfile.TemporaryDirectory()
        self.backup = Path(self.tmp.name) / 'backup.json'

    def tearDown(self):
        """Remove temporary CLI files."""
        self.tmp.cleanup()

    def run_cli(self, *argv):
        """Run one command and return its exit code and printed output."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*argv])
        return code, out.getvalue(), err.getvalue()

    def test_init_status_backup_restore_cycle(self):
        with uninitialized_database(self.url):
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
        self.assertEqual(json.loads(self.backup.read_text(encoding='utf-8'))['format'], 'khaya-db-dump')

        code, _, err = self.run_cli('restore', '--url', self.url, '--input', str(self.backup))
        self.assertEqual(code, 1)
        self.assertIn('--yes', err)
        code, out, _ = self.run_cli('restore', '--url', self.url, '--input', str(self.backup), '--yes')
        self.assertEqual(code, 0)
        self.assertEqual(Store(self.url).list_devices()[0]['name'], 'Router')

    def test_status_reports_uninitialized_databases(self):
        with uninitialized_database(self.url):
            code, out, _ = self.run_cli('status', '--url', self.url)
            self.assertEqual(code, 1)
            self.assertIn('not initialized', out)

    def test_url_precedence_follows_flag_then_environment(self):
        with patch.dict('os.environ', {'KHAYA_DATABASE_URL': self.url}):
            code, out, _ = self.run_cli('init')
            self.assertEqual(code, 0)
            self.assertIn('postgres', out)
        with patch.dict('os.environ', {'KHAYA_DATABASE_URL': 'postgresql://khaya:test@127.0.0.1:1/nope'}):
            code, out, _ = self.run_cli('init', '--url', self.url)
            self.assertEqual(code, 0)
            self.assertIn('postgres', out)

    def test_backend_instances_pass_through_unchanged(self):
        backend = open_backend(self.url)
        self.assertIsInstance(backend, Backend)
        self.assertIs(open_backend(backend), backend)
