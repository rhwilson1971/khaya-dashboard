"""Inventory and monitoring behavior tests using an isolated persistent database."""
import tempfile
import unittest
from pathlib import Path
from network.store import Store


class StoreTests(unittest.TestCase):
    """Verify inventory invariants and deterministic monitoring state transitions."""

    def setUp(self):
        """Create a private database for each test."""
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'network.db'
        self.store = Store(self.path)

    def tearDown(self):
        """Remove the isolated database."""
        self.tmp.cleanup()

    def device(self, **changes):
        """Create a real device with optional field overrides."""
        return self.store.save_device({'name': 'Desktop', 'target': '192.168.1.10', 'type': 'computer', **changes})

    def test_inventory_survives_restart(self):
        """Persist a device and its edited fields across Store instances."""
        d = self.device()
        self.store.save_device({'name': 'Studio', 'location': 'Office'}, d['id'])
        restored = Store(self.path).get_device(d['id'])
        self.assertEqual(restored['name'], 'Studio')
        self.assertEqual(restored['location'], 'Office')
        self.assertEqual(restored['status'], 'unknown')

    def test_invalid_targets_and_names_are_rejected(self):
        """Reject injection-shaped targets and missing names."""
        for target in [';whoami', '-c', 'hello world', '8.8.8.8', '192.168.1.999']:
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.device(target=target)
        with self.assertRaises(ValueError):
            self.device(name=' ')

    def test_duplicate_address_is_allowed_during_reassignment(self):
        """Permit a shared address while DHCP leases are being reassigned."""
        first = self.device(target='192.168.1.20')
        second = self.device(name='Replacement', target='192.168.1.20')
        self.assertEqual(second['target'], '192.168.1.20')
        moved = self.store.save_device({'target': '192.168.1.21'}, first['id'])
        self.assertEqual(moved['target'], '192.168.1.21')
        back = self.store.save_device({'target': '192.168.1.20'}, first['id'])
        self.assertEqual(back['target'], '192.168.1.20')
        self.assertEqual(len(self.store.list_devices()), 2)

    def test_existing_database_migrates_away_from_unique_addresses(self):
        """Open a database created while addresses were unique, then allow sharing."""
        legacy = Path(self.tmp.name) / 'legacy.db'
        db = __import__('sqlite3').connect(legacy)
        try:
            db.execute('''CREATE TABLE devices (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, target TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
                manufacturer TEXT NOT NULL DEFAULT '', mac TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '', connection TEXT NOT NULL DEFAULT 'wired',
                parent_id TEXT REFERENCES devices(id) ON DELETE SET NULL,
                method TEXT NOT NULL DEFAULT 'icmp', status TEXT NOT NULL DEFAULT 'unknown',
                last_check REAL, last_seen REAL, latency REAL, failures INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', heartbeat_hash TEXT,
                created_at REAL NOT NULL)''')
            db.execute("INSERT INTO devices (id, name, target, type, created_at) VALUES ('a', 'Desktop', '192.168.1.20', 'computer', 1)")
            db.commit()
        finally:
            db.close()
        store = Store(legacy)
        self.assertEqual(store.get_device('a')['target'], '192.168.1.20')
        store.save_device({'name': 'Replacement', 'target': '192.168.1.20', 'type': 'computer'})
        self.assertEqual(len(store.list_devices()), 2)

    def test_parent_cycles_and_missing_parents_are_rejected(self):
        """Keep the editable topology acyclic with valid references."""
        a = self.device(target='192.168.1.1', type='router')
        b = self.device(parent_id=a['id'])
        with self.assertRaises(ValueError):
            self.store.save_device({'parent_id': b['id']}, a['id'])
        with self.assertRaises(ValueError):
            self.device(target='192.168.1.12', parent_id='missing')
        self.store.delete_device(a['id'])
        self.assertIsNone(self.store.get_device(b['id'])['parent_id'])

    def test_failure_threshold_and_recovery(self):
        """Use consecutive failures, preserve last seen, and recover on success."""
        d = self.device()
        self.store.record_probe(d['id'], True, 2.4, at=100)
        self.store.record_probe(d['id'], False, None, at=110)
        self.assertEqual(self.store.get_device(d['id'])['status'], 'online')
        self.store.record_probe(d['id'], False, None, at=120)
        self.store.record_probe(d['id'], False, None, at=130)
        offline = self.store.get_device(d['id'])
        self.assertEqual(offline['status'], 'offline')
        self.assertEqual(offline['last_seen'], 100)
        self.store.record_probe(d['id'], True, 4.1, at=140)
        self.assertEqual(self.store.get_device(d['id'])['status'], 'online')
        self.assertEqual(len(self.store.history(d['id'])), 5)

    def test_probe_errors_are_unknown_not_offline(self):
        """Expose collector errors rather than declaring a target down."""
        d = self.device()
        self.store.record_probe(d['id'], None, None, error='ping not available', at=100)
        self.assertEqual(self.store.get_device(d['id'])['status'], 'unknown')
        self.assertEqual(self.store.get_device(d['id'])['last_error'], 'ping not available')

    def test_heartbeat_authentication_and_expiry(self):
        """Authenticate heartbeat secrets and expire stale observations."""
        d = self.device(method='heartbeat')
        token = self.store.rotate_heartbeat(d['id'])
        self.assertFalse(self.store.heartbeat(d['id'], 'wrong', at=100))
        self.assertTrue(self.store.heartbeat(d['id'], token, at=100))
        self.store.expire_heartbeats(now=301)
        self.assertEqual(self.store.get_device(d['id'])['status'], 'offline')
        self.assertNotIn('heartbeat_hash', self.store.get_device(d['id']))
        replacement = self.store.rotate_heartbeat(d['id'])
        self.assertNotEqual(token, replacement)
        self.assertFalse(self.store.heartbeat(d['id'], token, at=302))
        self.assertTrue(self.store.heartbeat(d['id'], replacement, at=302))

    def test_target_change_resets_observations(self):
        """Do not reuse health observations after changing the monitored target."""
        d = self.device()
        self.store.record_probe(d['id'], True, 1, at=100)
        updated = self.store.save_device({'target': '192.168.1.20'}, d['id'])
        self.assertEqual(updated['status'], 'unknown')
        self.assertIsNone(updated['last_seen'])
        self.assertEqual(self.store.history(d['id']), [])

    def test_settings_validate_bounds(self):
        """Persist usable monitoring settings and reject unbounded scans."""
        self.store.save_settings({'interval': 20, 'subnet': '192.168.10.0/24'})
        self.assertEqual(self.store.settings()['interval'], 20)
        for changes in [{'interval': 0}, {'failure_threshold': 0}, {'subnet': '10.0.0.0/8'}, {'subnet': '8.8.8.0/24'}]:
            with self.assertRaises(ValueError):
                self.store.save_settings(changes)

    def test_dns_settings_are_visible_read_only_and_preserve_inventory(self):
        """Expose fixed Pi-hole configuration without changing stored device targets."""
        d = self.device()
        settings = self.store.settings()
        self.assertEqual(settings['dns_domain'], 'boma.home.arpa')
        self.assertEqual(settings['dns_server'], '10.0.10.53')
        self.assertEqual(settings['dns_hostname'], 'jina.boma.home.arpa')
        self.store.save_settings({'interval': 20})
        self.assertEqual(self.store.settings()['dns_server'], '10.0.10.53')
        with self.assertRaises(ValueError):
            self.store.save_settings({'dns_server': '8.8.8.8'})
        self.assertEqual(self.store.get_device(d['id'])['target'], d['target'])

    def test_sample_devices_never_enter_real_inventory(self):
        """Return illustrative fixtures separately from real monitored records."""
        self.assertGreater(len(self.store.snapshot(sample=True)['devices']), 10)
        self.assertEqual(self.store.snapshot()['devices'], [])
        self.assertTrue(self.store.snapshot(sample=True)['sample'])

    def test_sample_uses_real_monitoring_settings(self):
        """Prevent illustrative inventory from overwriting real settings with fixture defaults."""
        self.store.save_settings({'subnet': '192.168.10.0/24', 'interval': 50, 'failure_threshold': 5})
        self.assertEqual(self.store.snapshot(sample=True)['settings'], self.store.settings())

    def test_import_can_preserve_unknown_connection(self):
        """An imported device must not invent wired or wireless connectivity."""
        d = self.device(connection='unknown')
        self.assertEqual(d['connection'], 'unknown')
