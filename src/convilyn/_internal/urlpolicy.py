"""URL safety policy — reject URLs that resolve to private/internal addresses.

Applied to every externally-supplied URL the client dials directly (the
storage upload/download URLs handed back by the API). Even though those
URLs come from the service, treating them as untrusted means a malformed
or tampered response can never make the client open a connection to a
loopback, link-local, or private-range address — closing an SSRF /
file-exfiltration vector.

The check resolves the hostname and refuses if the name matches an
internal suffix, resolution fails, or ANY resolved address is private.
Standard-library only, so it adds no dependency to the published package.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_SUFFIXES = (".internal", ".local", ".cluster.local", ".svc")

_PRIVATE_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),  # "This" network (includes 0.0.0.0)
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # Shared address space (CGNAT)
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.2.0/24"),  # TEST-NET-1 (RFC 5737)
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),  # Benchmarking (RFC 2544)
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2 (RFC 5737)
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3 (RFC 5737)
    ipaddress.ip_network("224.0.0.0/4"),  # Multicast
    ipaddress.ip_network("240.0.0.0/4"),  # Reserved (Class E)
    ipaddress.ip_network("::/128"),  # IPv6 unspecified
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6 addresses
    ipaddress.ip_network("64:ff9b::/96"),  # NAT64 (can embed private IPv4)
    ipaddress.ip_network("2001:db8::/32"),  # Documentation (RFC 3849)
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _PRIVATE_NETWORKS)


def _validated_ips_for_hostname(hostname: str) -> list[str] | None:
    """Resolve *hostname*; return all IP strings iff every address is public.

    Returns ``None`` when the hostname matches a blocked suffix, DNS
    resolution fails, or ANY resolved address falls in a private range.
    """
    if any(hostname.endswith(s) for s in _BLOCKED_SUFFIXES):
        return None

    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None

    ips: list[str] = []
    for *_, sockaddr in addr_info:
        raw = str(sockaddr[0])
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return None
        if _is_private_ip(ip):
            return None
        ips.append(raw)

    return ips if ips else None


def is_safe_url(url: str) -> bool:
    """Return False if *url* resolves to a private/internal network address."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if not hostname:
        return False
    return _validated_ips_for_hostname(hostname) is not None
