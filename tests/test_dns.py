"""Exercise local DNS using wire packets without contacting real devices."""
import socket
import struct
import unittest
from unittest.mock import patch
from network import dns


def wire_name(name):
    """Encode a fixture hostname in DNS label format."""
    return b''.join(bytes([len(label)]) + label.encode() for label in name.split('.')) + b'\0'


def answer(query, records, flags=0x8180, question=None):
    """Build a DNS reply with supplied answer records and optional question override."""
    question = query[12:] if question is None else wire_name(question) + struct.pack('!HH', 1, 1)
    body = b''
    for owner, kind, value in records:
        data = socket.inet_aton(value) if kind == 1 else wire_name(value)
        body += wire_name(owner) + struct.pack('!HHIH', kind, 1, 60, len(data)) + data
    return query[:2] + struct.pack('!HHHHH', flags, 1, len(records), 0, 0) + question + body


class DNSTests(unittest.TestCase):
    """Verify Pi-hole transport, answer validation, and address safety."""

    def test_short_name_queries_pihole_and_fqdn_is_preserved(self):
        """Expand short names and always connect directly to the configured DNS IP."""
        with patch('network.dns.socket.socket') as factory:
            sock = factory.return_value.__enter__.return_value
            sock.recv.side_effect = lambda size: answer(sock.send.call_args.args[0], [('jina.boma.home.arpa', 1, '10.0.10.53')])
            self.assertEqual(dns.resolve_target('jina'), '10.0.10.53')
            sock.connect.assert_called_with(('10.0.10.53', 53))
            self.assertIn(wire_name('jina.boma.home.arpa'), sock.send.call_args.args[0])
            self.assertEqual(dns.resolve_target('JINA.BOMA.HOME.ARPA'), '10.0.10.53')

    def test_literal_ips_and_localhost_do_not_need_dns(self):
        """Retain local IP targets and loopback without network lookups."""
        with patch('network.dns.socket.socket') as factory:
            self.assertEqual(dns.resolve_target('10.0.10.53'), '10.0.10.53')
            self.assertEqual(dns.resolve_target('localhost'), '127.0.0.1')
            with self.assertRaises(ValueError):
                dns.resolve_target('8.8.8.8')
            factory.assert_not_called()

    def test_rejects_public_answers_even_with_private_answer(self):
        """Reject a mixed answer set before choosing any address."""
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, [('pc.boma.home.arpa', 1, '10.0.10.20'), ('pc.boma.home.arpa', 1, '8.8.8.8')])):
            with self.assertRaisesRegex(ValueError, 'private IPv4'):
                dns.resolve_target('pc')

    def test_cname_chain_uses_only_related_records(self):
        """Follow a CNAME while ignoring unrelated answer addresses."""
        records = [('alias.boma.home.arpa', 5, 'pc.boma.home.arpa'), ('pc.boma.home.arpa', 1, '10.0.10.20'), ('unrelated.boma.home.arpa', 1, '8.8.8.8')]
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, records)):
            self.assertEqual(dns.resolve_target('alias'), '10.0.10.20')

    def test_cname_without_address_queries_canonical_name(self):
        """Use the same resolver for an alias that needs a second DNS question."""
        replies = [[('alias.boma.home.arpa', 5, 'pc.boma.home.arpa')], [('pc.boma.home.arpa', 1, '10.0.10.20')]]
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, replies.pop(0))) as exchange:
            self.assertEqual(dns.resolve_target('alias'), '10.0.10.20')
            self.assertIn(wire_name('pc.boma.home.arpa'), exchange.call_args.args[0])

    def test_bad_responses_and_missing_records_are_errors(self):
        """Reject missing records, forged questions, error codes, and malformed packets."""
        replies = [lambda q: b'bad', lambda q: answer(q, []), lambda q: answer(q, [], flags=0x8183), lambda q: answer(q, [('pc.boma.home.arpa', 1, '10.0.10.20')], question='other.boma.home.arpa'), lambda q: b'\0\0' + answer(q, [])[2:]]
        for reply in replies:
            with self.subTest(reply=reply), patch('network.dns._exchange', side_effect=lambda q, deadline: reply(q)):
                with self.assertRaises(ValueError):
                    dns.resolve_target('pc')

    def test_compressed_answer_name_and_pointer_loop(self):
        """Decode DNS name compression and reject cyclic pointers."""
        def compressed(query, deadline):
            """Return an A answer whose owner points to the question name."""
            return answer(query, [])[:6] + struct.pack('!H', 1) + b'\0' * 4 + query[12:] + b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + socket.inet_aton('10.0.10.20')
        with patch('network.dns._exchange', side_effect=compressed):
            self.assertEqual(dns.resolve_target('pc'), '10.0.10.20')
        with self.assertRaises(ValueError):
            dns._read_name(b'\xc0\x00', 0)

    def test_dns_timeout_does_not_fall_back_to_system_resolver(self):
        """Expose Pi-hole failure without silently querying another resolver."""
        with patch('network.dns.socket.socket', side_effect=TimeoutError), patch('socket.getaddrinfo') as system:
            with self.assertRaises(OSError):
                dns.resolve_target('pc')
            system.assert_not_called()

    def test_udp_operations_share_one_lookup_deadline(self):
        """A delayed send must not give receive a fresh three-second timeout."""
        with patch('network.dns.socket.socket') as factory, patch('network.dns.time.monotonic', side_effect=[10, 11, 12, 13]):
            sock = factory.return_value.__enter__.return_value
            sock.recv.side_effect = lambda size: answer(sock.send.call_args.args[0], [('pc.boma.home.arpa', 1, '10.0.10.20')])
            with self.assertRaises(OSError):
                dns.resolve_target('pc')
            sock.recv.assert_not_called()

    def test_local_hostname_requires_valid_domain_without_whitespace(self):
        """Do not treat malformed Host values as trusted domain names."""
        self.assertTrue(dns.local_hostname('KHAYA.BOMA.HOME.ARPA.'))
        for name in [' khaya.boma.home.arpa ', 'khaya.boma.home.arpa..', 'bad_label.boma.home.arpa', 'evilboma.home.arpa']:
            with self.subTest(name=name):
                self.assertFalse(dns.local_hostname(name))

    def test_truncated_udp_retries_over_tcp(self):
        """Read a complete DNS response over TCP when the UDP response is truncated."""
        with patch('network.dns.socket.socket') as factory:
            udp, tcp = factory.return_value.__enter__.return_value, factory.return_value.__enter__.return_value
            query = b'\x12\x34' + struct.pack('!HHHHH', 0x100, 1, 0, 0, 0) + wire_name('pc.boma.home.arpa') + struct.pack('!HH', 1, 1)
            reply = answer(query, [('pc.boma.home.arpa', 1, '10.0.10.20')])
            udp.recv.side_effect = [answer(query, [], flags=0x8380), struct.pack('!H', len(reply)), reply]
            with patch('network.dns.secrets.randbits', return_value=0x1234):
                self.assertEqual(dns.resolve_target('pc'), '10.0.10.20')
            self.assertEqual(factory.call_args_list[-1].args, (socket.AF_INET, socket.SOCK_STREAM))
            tcp.sendall.assert_called_once_with(struct.pack('!H', len(query)) + query)
