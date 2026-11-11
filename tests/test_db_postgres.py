"""PostgreSQL integration tests for the decoupled database backend.

Skipped unless KHAYA_TEST_POSTGRES_URL is set, for example:

    docker run --rm -d --name khaya-test-pg -e POSTGRES_PASSWORD=test \\
        -e POSTGRES_USER=khaya -e POSTGRES_DB=khaya_test -p 5432:5432 postgres:17-alpine
    KHAYA_TEST_POSTGRES_URL=postgresql://khaya:test@localhost:5432/khaya_test \\
        python3 -m unittest tests.test_db_postgres -v

The psycopg driver must also be installed. Tests wipe the data tables in
setUp, so point the URL at a disposable database.
"""
import os
import threading
import unittest
from db import open_backend
from db import dump as dump_module
from db.migrations import initialize
from network.store import Store

POSTGRES_URL = os.environ.get('KHAYA_TEST_POSTGRES_URL')
try:
    import psycopg  # noqa: F401
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False


@unittest.skipUnless(POSTGRES_URL and PSYCOPG_AVAILABLE, 'set KHAYA_TEST_POSTGRES_URL (and install psycopg) to run')
class PostgresStoreTests(unittest.TestCase):
    """Exercise the same application behavior as the SQLite suite on PostgreSQL."""

    @classmethod
    def setUpClass(cls):
        """Provision the schema once for the whole run."""
        initialize(POSTGRES_URL)

    def setUp(self):
        """Start every test from empty data tables and seeded settings."""
        with open_backend(POSTGRES_URL).connection() as db:
            for table in ('observations', 'events', 'devices', 'settings'):
                db.execute(f'DELETE FROM {table}')
        initialize(POSTGRES_URL)

    def device(self, **changes):
        """Create a real device with optional field overrides."""
        return Store(POSTGRES_URL).save_device({'name': 'Desktop', 'target': '192.168.1.10', 'type': 'computer', **changes})

    def test_crud_and_probes_across_connections(self):
        """Save, probe, edit, and delete through independent transactions."""
        store = Store(POSTGRES_URL)
        d = self.device()
        store.record_probe(d['id'], True, 2.5, at=100)
        self.assertEqual(Store(POSTGRES_URL).get_device(d['id'])['status'], 'online')
        store.record_probe(d['id'], False, None, at=110)
        self.assertEqual(Store(POSTGRES_URL).get_device(d['id'])['failures'], 1)
        store.record_probe(d['id'], None, None, 'ping missing', at=120)
        device = Store(POSTGRES_URL).get_device(d['id'])
        self.assertEqual(device['status'], 'unknown')
        self.assertEqual(device['last_error'], 'ping missing')
        history = Store(POSTGRES_URL).history(d['id'])
        self.assertEqual([row['reachable'] for row in history], [1, 0, None])
        Store(POSTGRES_URL).delete_device(d['id'])
        self.assertEqual(Store(POSTGRES_URL).list_devices(), [])

    def test_settings_upsert_without_duplicates(self):
        """Save settings through the portable upsert exactly once per key."""
        store = Store(POSTGRES_URL)
        self.assertEqual(store.settings()['interval'], 30)
        store.save_settings({'interval': 45})
        reopened = Store(POSTGRES_URL)
        self.assertEqual(reopened.settings()['interval'], 45)
        with open_backend(POSTGRES_URL).connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM settings').fetchone()['n'], 4)

    def test_heartbeat_rotation_authentication_and_expiry(self):
        """Heartbeat secrets work once rotated and expire into offline events."""
        store = Store(POSTGRES_URL)
        computer = self.device(method='heartbeat')
        token = store.rotate_heartbeat(computer['id'])
        self.assertTrue(store.heartbeat(computer['id'], token, at=100))
        self.assertFalse(store.heartbeat(computer['id'], 'wrong', at=101))
        store.save_settings({'heartbeat_timeout': 30})
        Store(POSTGRES_URL).expire_heartbeats(now=200)
        self.assertEqual(Store(POSTGRES_URL).get_device(computer['id'])['status'], 'offline')

    def test_parent_links_and_cycle_rejection(self):
        """Topology edits keep working under the advisory write lock."""
        store = Store(POSTGRES_URL)
        router = self.device(name='Router', target='192.168.1.1', type='router')
        child = self.device(name='Desktop', parent_id=router['id'])
        self.assertEqual(Store(POSTGRES_URL).get_device(child['id'])['parent_id'], router['id'])
        with self.assertRaises(ValueError):
            store.save_device({'parent_id': child['id']}, router['id'])

    def test_concurrent_probes_serialize(self):
        """Parallel writers take turns without losing observations."""
        store = Store(POSTGRES_URL)
        d = self.device()
        threads = [threading.Thread(target=store.record_probe, args=(d['id'], True, i, '', 100 + i)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(Store(POSTGRES_URL).history(d['id'])), 8)

    def test_dump_restore_and_identity_resync(self):
        """Backups restore fully and identity sequences continue after restored ids."""
        store = Store(POSTGRES_URL)
        router = self.device(name='Router', target='192.168.1.1', type='router')
        child = self.device(name='Desktop', parent_id=router['id'])
        store.record_probe(router['id'], True, 1.0, at=100)
        data = dump_module.dump(POSTGRES_URL)
        with open_backend(POSTGRES_URL).connection() as db:
            for table in ('observations', 'events', 'devices', 'settings'):
                db.execute(f'DELETE FROM {table}')
        counts = dump_module.restore(POSTGRES_URL, data)
        self.assertEqual(counts['devices'], 2)
        restored = Store(POSTGRES_URL)
        self.assertEqual(restored.get_device(child['id'])['parent_id'], router['id'])
        with open_backend(POSTGRES_URL).connection() as db:
            sequence = db.execute("SELECT pg_get_serial_sequence('observations', 'id') AS name").fetchone()['name']
            max_id = db.execute('SELECT MAX(id) AS m FROM observations').fetchone()['m']
            state = db.execute(f'SELECT last_value, is_called FROM {sequence}').fetchone()
        self.assertEqual(state['last_value'], max_id + 1)
        self.assertFalse(state['is_called'])
        restored.record_probe(router['id'], True, 2.0, at=200)
        with open_backend(POSTGRES_URL).connection() as db:
            ids = [row['id'] for row in db.execute('SELECT id FROM observations WHERE device_id=? ORDER BY id', (router['id'],))]
        self.assertEqual(len(ids), 2)
        self.assertEqual(ids, sorted(ids))
