"""Tests for bounded, safe network monitoring without scanning actual devices."""
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from network.store import Store
from network.monitor import probe, resolve_target, Collector


class MonitorTests(unittest.TestCase):
    """Check probe interpretation and explicit discovery jobs."""

    def test_rejects_public_dns_resolution(self):
        """A hostname resolving outside private networks must never be probed."""
        with patch('network.monitor.resolve_target', side_effect=ValueError('Hostname must resolve exclusively to private IPv4 addresses.')):
            self.assertIsNone(probe('example.com')['reachable'])
        with patch('network.dns._exchange', side_effect=ValueError('DNS returned a public address')):
            with self.assertRaises(ValueError):
                resolve_target('example.com')

    def test_probe_uses_resolved_pihole_address(self):
        """Pass only the validated DNS result to ping."""
        with patch('network.monitor.resolve_target', return_value='10.0.10.20') as resolve, patch('network.monitor.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'time=1 ms', '')) as run:
            self.assertTrue(probe('tembo')['reachable'])
            resolve.assert_called_once_with('tembo')
            self.assertEqual(run.call_args.args[0][-1], '10.0.10.20')

    def test_probe_parses_latency(self):
        """Parse real ping output and pass only an argv vector to subprocess."""
        with patch('network.monitor.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '64 bytes: time=2.45 ms', '')) as run:
            result = probe('192.168.1.10')
            self.assertTrue(result['reachable'])
            self.assertEqual(result['latency'], 2.45)
            self.assertIsInstance(run.call_args.args[0], list)
            self.assertLessEqual(run.call_args.kwargs['timeout'], 4)
            self.assertNotIn('shell', run.call_args.kwargs)

    def test_timeout_is_unreachable_and_permission_error_unknown(self):
        """Separate missing responses from collector execution errors."""
        with patch('network.monitor.subprocess.run', side_effect=subprocess.TimeoutExpired('ping', 3)):
            self.assertFalse(probe('192.168.1.10')['reachable'])
        with patch('network.monitor.subprocess.run', side_effect=FileNotFoundError()):
            self.assertIsNone(probe('192.168.1.10')['reachable'])
        with patch('network.monitor.subprocess.run', return_value=subprocess.CompletedProcess([], 2, '', 'Operation not permitted')):
            self.assertIsNone(probe('192.168.1.10')['reachable'])

    def test_collector_skips_unmonitored_and_heartbeat_devices(self):
        """Probe only ICMP devices and record actual results."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            a = store.save_device({'name': 'PC', 'target': '192.168.1.10', 'type': 'computer'})
            store.save_device({'name': 'Tablet', 'target': '192.168.1.11', 'method': 'none'})
            store.save_device({'name': 'Agent', 'target': '192.168.1.12', 'method': 'heartbeat'})
            collector = Collector(store, probe_fn=lambda target: {'reachable': True, 'latency': 2, 'error': ''})
            collector.run_cycle()
            self.assertEqual(store.get_device(a['id'])['status'], 'online')
            self.assertEqual(len(store.history(a['id'])), 1)
            self.assertIsNotNone(collector.status()['last_cycle'])

    def test_discovery_is_bounded_and_returns_only_responders(self):
        """Scan a configured /30 and keep candidates outside real inventory."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            store.save_settings({'subnet': '192.168.1.0/30'})
            collector = Collector(store, probe_fn=lambda target: {'reachable': target.endswith('.1'), 'latency': 1, 'error': ''})
            job = collector.start_discovery()
            collector.discovery_thread.join(timeout=5)
            result = collector.discovery_status(job['id'])
            self.assertEqual(result['state'], 'complete')
            self.assertEqual(result['total'], 2)
            self.assertEqual(result['checked'], 2)
            self.assertEqual([d['target'] for d in result['candidates']], ['192.168.1.1'])
            self.assertEqual(store.list_devices(), [])
            with self.assertRaises(ValueError):
                collector.start_discovery('10.0.0.0/8')

    def test_heartbeat_expires_while_icmp_batch_is_blocked(self):
        """A slow ICMP batch must not delay the independent heartbeat timer."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'db')
            store.save_settings({'heartbeat_timeout': 30})
            computer = store.save_device({'name': 'Agent', 'target': '192.168.1.11', 'method': 'heartbeat'})
            store.save_device({'name': 'Ping', 'target': '192.168.1.12'})
            token = store.rotate_heartbeat(computer['id'])
            entered, release = threading.Event(), threading.Event()

            def delayed_probe(target):
                """Hold the ICMP batch until the expiry assertion has completed."""
                entered.set()
                release.wait(5)
                return {'reachable': False, 'latency': None, 'error': 'Timeout'}

            collector = Collector(store, probe_fn=delayed_probe)
            collector.start()
            try:
                self.assertTrue(entered.wait(1))
                store.heartbeat(computer['id'], token, at=time.time() - 60)
                deadline = time.monotonic() + 3
                while store.get_device(computer['id'])['status'] == 'online' and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(store.get_device(computer['id'])['status'], 'offline')
            finally:
                release.set()
                collector.stop()
