"""URL safety policy — SSRF / file-exfiltration guard (offline).

DNS resolution is stubbed via ``socket.getaddrinfo`` so these run with no
network. The contract: a URL is safe iff its host resolves ONLY to public
addresses and does not match an internal suffix.
"""

from __future__ import annotations

import socket

import pytest

from convilyn._internal import urlpolicy


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    """Return a getaddrinfo stub resolving hostnames per *mapping*."""

    def _stub(host, *_args, **_kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unresolved: {host}")
        return [(socket.AF_INET, None, None, "", (ip, 0)) for ip in mapping[host]]

    return _stub


class TestIsSafeUrlLogic:
    def test_public_host_is_safe(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"cdn.example.com": ["93.184.216.34"]})
        )

        assert urlpolicy.is_safe_url("https://cdn.example.com/obj?sig=x") is True

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # cloud metadata
            "127.0.0.1",  # loopback
            "10.0.0.5",  # private
            "172.16.9.9",  # private
            "192.168.1.1",  # private
            "0.0.0.0",  # "this" network
        ],
    )
    def test_private_ip_is_unsafe(self, monkeypatch, ip):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({"evil.example.com": [ip]}))

        assert urlpolicy.is_safe_url("https://evil.example.com/x?sig=y") is False

    def test_any_private_address_in_the_set_makes_it_unsafe(self, monkeypatch):
        # DNS returning one public + one private address must be rejected
        # (a rebinding / multi-A-record bypass attempt).
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            _fake_getaddrinfo({"mixed.example.com": ["93.184.216.34", "10.1.2.3"]}),
        )

        assert urlpolicy.is_safe_url("https://mixed.example.com/x") is False


class TestIsSafeUrlBoundary:
    @pytest.mark.parametrize("suffix", [".internal", ".local", ".svc", ".cluster.local"])
    def test_internal_suffix_is_unsafe_without_resolving(self, monkeypatch, suffix):
        # Blocked suffixes must reject BEFORE any DNS lookup.
        def _boom(*_a, **_k):
            raise AssertionError("must not resolve a blocked-suffix host")

        monkeypatch.setattr(socket, "getaddrinfo", _boom)

        assert urlpolicy.is_safe_url(f"https://backend{suffix}/x") is False

    def test_empty_host_is_unsafe(self):
        assert urlpolicy.is_safe_url("https:///nohost") is False


class TestIsSafeUrlError:
    def test_dns_failure_is_unsafe(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))

        assert urlpolicy.is_safe_url("https://nonexistent.example.com/x") is False
