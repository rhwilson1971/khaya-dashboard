"""Bounded IPv4 DNS lookups through the home Pi-hole, with no resolver fallback."""
import secrets
import socket
import struct
import time
from network.config import DNS_DOMAIN, DNS_SERVER
from network.store import local_ipv4, validate_target

LOOKUP_TIMEOUT = 3


def local_hostname(value):
    """Return whether value is a valid hostname within the configured local domain."""
    if not isinstance(value, str):
        return False
    normalized = value.lower().removesuffix('.')
    try:
        name = validate_target(normalized)
    except ValueError:
        return False
    return name == normalized and (name == DNS_DOMAIN or name.endswith('.' + DNS_DOMAIN))


def qualify_name(target):
    """Validate a DNS target and append the home domain to a single-label name."""
    name = validate_target(target.removesuffix('.') if isinstance(target, str) else target)
    return name + '.' + DNS_DOMAIN if '.' not in name else name


def _read_name(packet, offset):
    """Decode a possibly compressed DNS name; return the name and next wire offset."""
    labels, seen, end = [], set(), None
    while True:
        if offset >= len(packet) or offset in seen:
            raise ValueError('Malformed DNS name or compression loop.')
        seen.add(offset)
        size = packet[offset]
        if size & 0xc0 == 0xc0:
            if offset + 1 >= len(packet):
                raise ValueError('Truncated DNS compression pointer.')
            end = end if end is not None else offset + 2
            offset = ((size & 0x3f) << 8) | packet[offset + 1]
            continue
        if size & 0xc0 or offset + size + 1 > len(packet):
            raise ValueError('Malformed DNS label.')
        offset += 1
        if size == 0:
            break
        try:
            labels.append(packet[offset:offset + size].decode('ascii').lower())
        except UnicodeDecodeError as exc:
            raise ValueError('Invalid DNS hostname encoding.') from exc
        offset += size
        if sum(len(label) + 1 for label in labels) > 254:
            raise ValueError('DNS hostname is too long.')
    return '.'.join(labels), end if end is not None else offset


def _remaining(deadline):
    """Return seconds left in a lookup's shared deadline or raise a timeout."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('DNS lookup timed out.')
    return remaining


def _receive(sock, length, deadline):
    """Read exactly length TCP bytes within the overall lookup deadline."""
    result = bytearray()
    while len(result) < length:
        sock.settimeout(_remaining(deadline))
        chunk = sock.recv(length - len(result))
        if not chunk:
            raise ValueError('Truncated DNS TCP response.')
        result.extend(chunk)
    return bytes(result)


def _exchange(query, deadline):
    """Query only Pi-hole over UDP, retrying truncated replies over TCP at the same IP."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_remaining(deadline))
        sock.connect((DNS_SERVER, 53))
        sock.settimeout(_remaining(deadline))
        sock.send(query)
        sock.settimeout(_remaining(deadline))
        reply = sock.recv(65535)
    if len(reply) < 12 or reply[:2] != query[:2]:
        raise ValueError('Invalid DNS response transaction.')
    flags = struct.unpack_from('!H', reply, 2)[0]
    if flags & 0x0200:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_remaining(deadline))
            sock.connect((DNS_SERVER, 53))
            sock.settimeout(_remaining(deadline))
            sock.sendall(struct.pack('!H', len(query)) + query)
            size = struct.unpack('!H', _receive(sock, 2, deadline))[0]
            reply = _receive(sock, size, deadline)
    return reply


def _parse_response(packet, query, name):
    """Validate the DNS question and return A/CNAME records from the answer section."""
    if len(packet) < 12 or packet[:2] != query[:2]:
        raise ValueError('Invalid DNS response transaction.')
    _, flags, questions, answers, _, _ = struct.unpack_from('!6H', packet)
    if not flags & 0x8000 or flags & 0x7a00 or questions != 1:
        raise ValueError('Invalid or truncated DNS response.')
    if flags & 0x000f:
        raise ValueError(f'Pi-hole DNS lookup failed for {name} (code {flags & 0x000f}).')
    question, offset = _read_name(packet, 12)
    if question != name or packet[offset:offset + 4] != struct.pack('!HH', 1, 1):
        raise ValueError('DNS response question does not match the target.')
    offset += 4
    records = []
    for _ in range(answers):
        owner, offset = _read_name(packet, offset)
        if offset + 10 > len(packet):
            raise ValueError('Truncated DNS answer.')
        kind, cls, _, size = struct.unpack_from('!HHIH', packet, offset)
        offset += 10
        end = offset + size
        if end > len(packet):
            raise ValueError('Truncated DNS answer data.')
        if cls == 1 and kind == 1:
            if size != 4:
                raise ValueError('Invalid DNS IPv4 answer.')
            records.append((owner, kind, socket.inet_ntoa(packet[offset:end])))
        elif cls == 1 and kind == 5:
            canonical, consumed = _read_name(packet, offset)
            if consumed != end:
                raise ValueError('Invalid DNS CNAME answer.')
            records.append((owner, kind, validate_target(canonical)))
        offset = end
    return records


def resolve_target(target):
    """Return a private IPv4 for target using Pi-hole; short names use the home domain.

    Literal private IPs and localhost bypass DNS. Each lookup has a three-second
    deadline shared by UDP, TCP, and CNAME follow-ups. Public answers, malformed
    responses, and missing records raise ValueError; transport failures raise
    OSError. No public or operating-system resolver is queried.
    """
    target = validate_target(target.removesuffix('.') if isinstance(target, str) else target)
    if local_ipv4(target):
        return target
    if target == 'localhost':
        return '127.0.0.1'
    name = qualify_name(target)
    deadline = time.monotonic() + LOOKUP_TIMEOUT
    visited = set()
    try:
        for _ in range(8):
            if name in visited:
                raise ValueError('DNS CNAME loop.')
            visited.add(name)
            encoded = b''.join(bytes([len(label)]) + label.encode('ascii') for label in name.split('.')) + b'\0'
            query = struct.pack('!6H', secrets.randbits(16), 0x0100, 1, 0, 0, 0) + encoded + struct.pack('!HH', 1, 1)
            records = _parse_response(_exchange(query, deadline), query, name)
            chain = set()
            while name not in chain:
                chain.add(name)
                addresses = {value for owner, kind, value in records if owner == name and kind == 1}
                aliases = {value for owner, kind, value in records if owner == name and kind == 5}
                if addresses:
                    if aliases or not all(local_ipv4(a) for a in addresses):
                        raise ValueError('Hostname must resolve exclusively to private IPv4 addresses.')
                    return sorted(addresses)[0]
                if not aliases:
                    if len(chain) == 1:
                        raise ValueError(f'Pi-hole has no IPv4 record for {name}.')
                    break
                if len(aliases) != 1:
                    raise ValueError('Conflicting DNS CNAME records.')
                name = aliases.pop()
            else:
                raise ValueError('DNS CNAME loop.')
        raise ValueError('Too many DNS CNAME redirects.')
    except OSError as exc:
        raise OSError(f'Pi-hole DNS at {DNS_SERVER} could not resolve {target}: {exc}') from exc
