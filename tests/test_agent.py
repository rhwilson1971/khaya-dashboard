"""Heartbeat agent request validation and credential handling."""
import unittest
from heartbeat_agent import make_request, validate_url


class AgentTests(unittest.TestCase):
    """Ensure the bundled agent sends credentials only to a local collector endpoint."""

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
