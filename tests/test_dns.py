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
    """Build a DNS reply with answer records, optional TTLs, and question override."""
    question = query[12:] if question is None else wire_name(question) + query[-4:]
    body = b''
    for record in records:
        owner, kind, value = record[:3]
        ttl = record[3] if len(record) > 3 else 60
        data = socket.inet_aton(value) if kind == 1 else wire_name(value)
        body += wire_name(owner) + struct.pack('!HHIH', kind, 1, ttl, len(data)) + data
    return query[:2] + struct.pack('!HHHHH', flags, 1, len(records), 0, 0) + question + body


class DNSTests(unittest.TestCase):
    """Verify Pi-hole transport, answer validation, and address safety."""

    def test_reverse_lookup_queries_pihole_for_full_local_name(self):
        """Use a PTR question for an IPv4 address and preserve the DNS TTL."""
        with patch('network.dns.socket.socket') as factory:
            sock = factory.return_value.__enter__.return_value
            sock.recv.side_effect = lambda size: answer(sock.send.call_args.args[0], [('53.10.0.10.in-addr.arpa', 12, 'JINA.BOMA.HOME.ARPA', 120)])
            self.assertEqual(dns.reverse_lookup('10.0.10.53'), (['jina.boma.home.arpa'], 120))
            sock.connect.assert_called_with(('10.0.10.53', 53))
            self.assertIn(wire_name('53.10.0.10.in-addr.arpa'), sock.send.call_args.args[0])
            self.assertEqual(sock.send.call_args.args[0][-4:], struct.pack('!HH', 12, 1))

    def test_reverse_alias_uses_smallest_related_ttl(self):
        """Follow related CNAME records and ignore names outside the configured domain."""
        records = [('53.10.0.10.in-addr.arpa', 5, 'alias.in-addr.arpa', 20), ('alias.in-addr.arpa', 12, 'jina.boma.home.arpa', 120), ('alias.in-addr.arpa', 12, 'outside.example', 1), ('other.in-addr.arpa', 12, 'wrong.boma.home.arpa', 1)]
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, records)):
            self.assertEqual(dns.reverse_lookup('10.0.10.53'), (['jina.boma.home.arpa'], 20))

    def test_reverse_short_name_expands_domain(self):
        """Expand a Pi-hole short PTR hostname to the full home-domain name."""
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, [('53.10.0.10.in-addr.arpa', 12, 'jina')])):
            self.assertEqual(dns.reverse_lookup('10.0.10.53'), (['jina.boma.home.arpa'], 60))

    def test_reverse_missing_and_nonlocal_records_are_not_names(self):
        """Distinguish absent usable PTR records from a broken DNS lookup."""
        for records, flags in [([], 0x8180), ([], 0x8183), ([('53.10.0.10.in-addr.arpa', 12, 'outside.example')], 0x8180), ([('53.10.0.10.in-addr.arpa', 12, 'localhost')], 0x8180)]:
            with self.subTest(records=records, flags=flags), patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, records, flags=flags)):
                self.assertEqual(dns.reverse_lookup('10.0.10.53'), ([], 30))

    def test_reverse_rejects_public_address_and_exposes_errors(self):
        """Do not query public IPs or hide malformed replies and transport failures."""
        with patch('network.dns._exchange') as exchange:
            with self.assertRaises(ValueError):
                dns.reverse_lookup('8.8.8.8')
            exchange.assert_not_called()
        for failure in [OSError('timeout'), ValueError('malformed reply')]:
            with self.subTest(failure=failure), patch('network.dns._exchange', side_effect=failure):
                with self.assertRaises(type(failure)):
                    dns.reverse_lookup('10.0.10.53')

    def test_reverse_alias_followup_retains_ttl_and_shared_deadline(self):
        """Complete an alias in a second PTR query without resetting its deadline or TTL."""
        replies = [[('53.10.0.10.in-addr.arpa', 5, 'alias.in-addr.arpa', 10)], [('alias.in-addr.arpa', 12, 'jina.boma.home.arpa', 60)]]
        with patch('network.dns._exchange', side_effect=lambda q, deadline: answer(q, replies.pop(0))) as exchange:
            self.assertEqual(dns.reverse_lookup('10.0.10.53'), (['jina.boma.home.arpa'], 10))
            self.assertIn(wire_name('alias.in-addr.arpa'), exchange.call_args.args[0])
            self.assertEqual(exchange.call_args_list[0].args[1], exchange.call_args_list[1].args[1])

    def test_reverse_rejects_bad_questions_malformed_names_and_alias_loops(self):
        """Actual invalid wire replies must not become missing-name results."""
        replies = [
            lambda q: answer(q, [], flags=0x8183, question='other.in-addr.arpa'),
            lambda q: answer(q, [], flags=0x8182),
            lambda q: answer(q[:-4] + struct.pack('!HH', 1, 1), []),
            lambda q: answer(q, [('53.10.0.10.in-addr.arpa', 12, 'bad_name.boma.home.arpa')]),
            lambda q: answer(q, [('53.10.0.10.in-addr.arpa', 5, '53.10.0.10.in-addr.arpa')]),
            lambda q: answer(q, [('53.10.0.10.in-addr.arpa', 12, 'jina.boma.home.arpa')])[:-1],
        ]
        for reply in replies:
            with self.subTest(reply=reply), patch('network.dns._exchange', side_effect=lambda q, deadline: reply(q)):
                with self.assertRaises(ValueError):
                    dns.reverse_lookup('10.0.10.53')

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
