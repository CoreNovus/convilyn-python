"""Shared fixtures for the resource-flow tests.

These tests exercise the upload / convert / goal flows with respx-mocked
HTTP; they are NOT the SSRF-guard tests. The external-URL safety guard
resolves hostnames via DNS, which would (a) hit the network — violating
the no-network unit-test rule — and (b) make the flow tests depend on a
live resolver. Stub the guard's host check to a pass here; the guard's
real resolve-and-reject logic is covered offline in
``tests/unit/_internal/test_urlpolicy.py``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_external_hosts(monkeypatch):
    monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _url: True)
