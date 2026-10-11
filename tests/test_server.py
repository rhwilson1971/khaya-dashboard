"""HTTP integration tests for the local dashboard API."""
import io
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from network.server import Handler
from network.store import Store
from network.monitor import Collector


class MemoryConnection:
    """Exercise the real HTTP parser without binding a sandbox-restricted socket."""

    def __init__(self, wire):
        """Attach request bytes and a response buffer."""
        self.input = io.BytesIO(wire)
        self.output = io.BytesIO()

    def makefile(self, mode, buffering=None):
        """Return a request stream for BaseHTTPRequestHandler."""
        return self.input

    def sendall(self, body):
        """Capture response bytes sent by the real HTTP handler."""
        self.output.write(body)

    def settimeout(self, seconds):
        """Accept the handler's socket timeout for this deterministic memory transport."""
        self.timeout = seconds


class ServerTests(unittest.TestCase):
    """Exercise public routes over an isolated local HTTP server."""

    def setUp(self):
        """Create temporary services for in-memory HTTP wire requests."""
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'db')
        self.collector = Collector(self.store, probe_fn=lambda target: {'reachable': True, 'latency': 1, 'error': ''})
        self.server = SimpleNamespace(store=self.store, collector=self.collector, server_port=8765, server_address=('127.0.0.1', 8765))

    def tearDown(self):
        """Remove persisted test data."""
        self.tmp.cleanup()

    def request(self, method, path, data=None, headers=None):
        """Send one request and return status plus decoded JSON or text."""
        payload = json.dumps(data).encode() if data is not None else b''
        request_headers = {'Host': '127.0.0.1:8765', 'Content-Type': 'application/json', 'Content-Length': str(len(payload)), **(headers or {})}
        wire = (f'{method} {path} HTTP/1.0\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in request_headers.items()) + '\r\n').encode() + payload
        connection = MemoryConnection(wire)
        Handler(connection, ('127.0.0.1', 1234), self.server)
        head, body = connection.output.getvalue().split(b'\r\n\r\n', 1)
        status = int(head.split(b' ')[1])
        text = body.decode()
        return status, json.loads(text) if text.startswith('{') else text

    def test_device_crud_and_validation(self):
        """Create, edit, observe, and delete through the HTTP API."""
        status, d = self.request('POST', '/api/devices', {'name': 'Office PC', 'target': '192.168.1.10', 'type': 'computer'})
        self.assertEqual(status, 201)
        self.assertEqual(self.request('PATCH', '/api/devices/' + d['id'], {'location': 'Office'})[0], 200)
        self.assertEqual(self.request('GET', '/api/devices/' + d['id'])[1]['device']['location'], 'Office')
        self.assertEqual(self.request('POST', '/api/devices', {'name': 'Bad', 'target': ';whoami'})[0], 400)
        self.assertEqual(self.request('DELETE', '/api/devices/' + d['id'])[0], 200)
        self.assertEqual(self.request('GET', '/api/devices/' + d['id'])[0], 404)

    def test_sample_is_separate_and_static_page_is_served(self):
        """Deliver the dashboard and isolate fixtures from the real snapshot."""
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('html', body)
        self.assertTrue(self.request('GET', '/api/snapshot?sample=1')[1]['sample'])
        self.assertEqual(self.request('GET', '/api/snapshot')[1]['devices'], [])

    def test_host_and_origin_protection(self):
        """Reject foreign origins and rebinding hostnames before inventory mutations."""
        data = {'name': 'PC', 'target': '192.168.1.10'}
        self.assertEqual(self.request('POST', '/api/devices', data, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/snapshot', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.store.list_devices(), [])

    def test_lan_accepts_local_dns_domain_and_checks_origin(self):
        """Allow configured local hosts in LAN mode while enforcing the origin."""
        self.server.server_address = ('0.0.0.0', 8765)
        host = 'khaya.boma.home.arpa:8765'
        self.assertEqual(self.request('GET', '/api/snapshot', headers={'Host': host})[0], 200)
        self.assertEqual(self.request('PATCH', '/api/settings', {'interval': 20}, {'Host': host, 'Origin': 'http://' + host})[0], 200)
        self.assertEqual(self.request('PATCH', '/api/settings', {'interval': 30}, {'Host': host, 'Origin': 'http://evil.example'})[0], 403)
        for bad in ['evilboma.home.arpa:8765', 'khaya.boma.home.arpa.evil:8765', 'khaya.boma.home.arpa:8766']:
            self.assertEqual(self.request('GET', '/api/snapshot', headers={'Host': bad})[0], 403)

    def test_loopback_mode_still_rejects_lan_dns_hosts(self):
        """Keep a loopback-bound server restricted to loopback Host headers."""
        self.assertEqual(self.request('GET', '/api/snapshot', headers={'Host': 'khaya.boma.home.arpa:8765'})[0], 403)

    def test_heartbeat_requires_correct_bearer_token(self):
        """Do not accept unauthenticated heartbeat health claims."""
        _, d = self.request('POST', '/api/devices', {'name': 'PC', 'target': '192.168.1.10', 'method': 'heartbeat'})
        _, token = self.request('POST', '/api/devices/' + d['id'] + '/token', {})
        self.assertEqual(self.request('POST', '/api/heartbeat/' + d['id'], {}, {'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.request('POST', '/api/heartbeat/' + d['id'], {}, {'Authorization': 'Bearer ' + token['token']})[0], 200)
        self.assertEqual(self.store.get_device(d['id'])['status'], 'online')

    def test_discovery_import_only_accepts_discovered_targets(self):
        """Import responders once and reject addresses not in the job."""
        self.store.save_settings({'subnet': '192.168.1.0/30'})
        _, job = self.request('POST', '/api/discovery', {})
        self.collector.discovery_thread.join(5)
        path = '/api/discovery/' + job['id'] + '/import'
        self.assertEqual(self.request('POST', path, {'targets': ['192.168.1.200']})[0], 400)
        status, result = self.request('POST', path, {'targets': ['192.168.1.1']})
        self.assertEqual(status, 200)
        self.assertEqual(len(result['imported']), 1)
        self.assertEqual(len(self.request('POST', path, {'targets': ['192.168.1.1']})[1]['skipped']), 1)
