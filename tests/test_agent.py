"""Heartbeat agent request validation and credential handling."""
import io
import unittest
from unittest.mock import patch
from heartbeat_agent import main, make_request, validate_url


class AgentTests(unittest.TestCase):
    """Ensure the bundled agent sends credentials only to a local collector endpoint."""

    def test_local_domain_request_pins_ip_and_preserves_host(self):
        """Resolve the collector through Pi-hole before attaching the heartbeat secret."""
        url = 'http://khaya.boma.home.arpa:8765/api/heartbeat/abc123'
        self.assertEqual(validate_url(url), url)
        with patch('heartbeat_agent.resolve_target', return_value='10.0.10.20') as resolve:
            request = make_request(url, 'secret')
            resolve.assert_called_once_with('khaya.boma.home.arpa')
        self.assertEqual(request.full_url, 'http://10.0.10.20:8765/api/heartbeat/abc123')
        self.assertEqual(request.get_header('Host'), 'khaya.boma.home.arpa:8765')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')

    def test_rejects_local_domain_lookalikes(self):
        """Only the configured local DNS domain is trusted for heartbeat URLs."""
        for host in ['evilboma.home.arpa', 'boma.home.arpa.evil.example', 'example.com']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate_url('http://' + host + ':8765/api/heartbeat/abc')

    def test_each_send_resolves_collector_again(self):
        """Follow a changed local DNS record on the next heartbeat interval."""
        with patch('heartbeat_agent.sys.argv', ['heartbeat_agent.py', '--url', 'http://khaya.boma.home.arpa:8765/api/heartbeat/abc']), patch.dict('os.environ', {'KHAYA_HEARTBEAT_TOKEN': 'secret'}), patch('heartbeat_agent.resolve_target', side_effect=['10.0.10.20', '10.0.10.21']), patch('heartbeat_agent.urllib.request.build_opener') as build, patch('heartbeat_agent.time.sleep', side_effect=[None, KeyboardInterrupt]), patch('heartbeat_agent.sys.stdout', new_callable=io.StringIO):
            opener = build.return_value
            opener.open.return_value.__enter__.return_value.read.return_value = b'{"accepted":true}'
            self.assertEqual(main(), 0)
            requests = [call.args[0] for call in opener.open.call_args_list]
            self.assertEqual([request.full_url for request in requests], ['http://10.0.10.20:8765/api/heartbeat/abc', 'http://10.0.10.21:8765/api/heartbeat/abc'])

    def test_once_reports_dns_failure_without_sending_token(self):
        """A failed lookup produces a failed check without opening an HTTP connection."""
        with patch('heartbeat_agent.sys.argv', ['heartbeat_agent.py', '--url', 'http://khaya.boma.home.arpa:8765/api/heartbeat/abc', '--once']), patch.dict('os.environ', {'KHAYA_HEARTBEAT_TOKEN': 'secret'}), patch('heartbeat_agent.resolve_target', side_effect=OSError('DNS unavailable')), patch('heartbeat_agent.urllib.request.build_opener') as build, patch('heartbeat_agent.sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
            build.return_value.open.assert_not_called()
            self.assertNotIn('secret', errors.getvalue())

    def test_request_uses_authorization_header(self):
        """Place the heartbeat token in a header instead of the URL or payload."""
        request = make_request('http://192.168.1.2:8765/api/heartbeat/abc123', 'secret')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        self.assertNotIn('secret', request.full_url)
        self.assertEqual(request.data, b'{}')

    def test_rejects_public_urls_credentials_and_wrong_routes(self):
        """Reject targets that are not explicit local collector heartbeat endpoints."""
        for url in ['https://example.com/api/heartbeat/abc', 'http://8.8.8.8/api/heartbeat/abc', 'http://user:pass@127.0.0.1/api/heartbeat/abc', 'http://127.0.0.1/api/snapshot', 'file:///api/heartbeat/abc', 'http://127.0.0.1/api/heartbeat/abc?secret=1']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        self.assertEqual(validate_url('http://localhost:8765/api/heartbeat/abc'), 'http://localhost:8765/api/heartbeat/abc')
