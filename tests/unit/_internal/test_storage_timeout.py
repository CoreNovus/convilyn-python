"""The object-storage hop gets its own timeout budget.

Reported: uploading a 1.5 MB SEC filing raised a bare `httpx.ReadTimeout`, and the
follow-up measurement is what makes the fix's shape non-obvious — the second
occurrence was a FEW-KILOBYTE file, so payload size is not the common factor.
Both were the first upload-bearing call in a fresh process, and bare HTTP to
the API in the same window answered in 0.33 s.

So the defect is not "30 s is too small". It is that one number covered four
different waits on two different hosts: a status poll against the API, and a
DNS + TCP + TLS handshake plus a body transfer to a storage host this process
has never spoken to. `STORAGE_TIMEOUT` separates them.

**The API default is deliberately unchanged**, and the second class below is
what keeps that honest — without it, these tests would pass equally against a
change that simply widened everything.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn
from convilyn._internal.http import DEFAULT_TIMEOUT, STORAGE_TIMEOUT

API_BASE = "https://api.convilyn.com"
PRESIGN_URL = "https://storage.example.com/upload"


@pytest.fixture(autouse=True)
def _allow_external_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the external-URL host check, exactly as `tests/unit/resources/
    conftest.py` does for the flow tests.

    Declared here rather than inherited: that conftest is scoped to
    `tests/unit/resources/`, and this file lives one package over. The guard
    resolves hostnames via a real `socket.getaddrinfo`, which would hit the
    network and make these tests depend on a live resolver; its own
    resolve-and-reject logic is covered offline in `test_urlpolicy.py`.

    Worth noticing here: that stub is also why the blocking
    `getaddrinfo` this issue's follow-up measurement points at is invisible to
    the whole suite. It is never executed under test.
    """
    monkeypatch.setattr("convilyn._internal.http.is_safe_url", lambda _url: True)


def _stub_upload(mock: respx.Router) -> respx.Route:
    #: Grant shape copied from `tests/unit/resources/test_files.py` — the
    #: contract makes all four of uploadUrl / fields / fileId / s3Key required.
    mock.post(f"{API_BASE}/api/v1/upload/presign").mock(
        return_value=httpx.Response(
            200,
            json={
                "uploadUrl": PRESIGN_URL,
                "fields": {"Content-Type": "text/plain", "key": "input/a.txt"},
                "fileId": "file_abc",
                "s3Key": "input/a.txt",
                "expiresIn": 3600,
            },
        )
    )
    #: Canonical FileResponse — camelCase on the wire.
    mock.post(f"{API_BASE}/api/v1/upload/confirm").mock(
        return_value=httpx.Response(
            200,
            json={
                "fileId": "file_abc",
                "fileName": "a.txt",
                "fileSize": 1,
                "mimeType": "text/plain",
                "isInput": True,
                "createdAt": "2026-05-20T12:00:00Z",
                "jobId": None,
            },
        )
    )
    return mock.post(PRESIGN_URL).mock(return_value=httpx.Response(204))


# ── 1. Logic — the storage leg carries the storage budget ────────────


class TestTheStorageLegUsesItsOwnBudget:
    @pytest.mark.asyncio
    async def test_the_upload_post_is_sent_with_the_storage_timeout(self, tmp_path) -> None:
        source = tmp_path / "a.txt"
        source.write_bytes(b"x")

        async with respx.mock as mock:
            storage = _stub_upload(mock)
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client.files.upload(str(source))

        assert storage.call_count == 1
        assert storage.calls.last.request.extensions["timeout"] == {
            "connect": 30.0,
            "read": 300.0,
            "write": 300.0,
            "pool": 30.0,
        }

    @pytest.mark.asyncio
    async def test_an_external_download_uses_it_too(self) -> None:
        """Same hop, opposite direction — a large artifact coming back has the
        same fixed per-hop costs as one going out."""
        async with respx.mock as mock:
            route = mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"bytes")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.external_get("https://storage.example.com/out")

        assert route.calls.last.request.extensions["timeout"]["read"] == 300.0


# ── 2. Boundary — the API default did NOT move ───────────────────────


class TestTheApiBudgetIsUnchanged:
    """The discriminating class. `DEFAULT_TIMEOUT` is 30 s because an API call
    that should answer in under a second has no business waiting longer, and
    widening it globally would slow every real failure down."""

    @pytest.mark.asyncio
    async def test_an_ordinary_api_request_still_gets_thirty_seconds(self) -> None:
        async with respx.mock as mock:
            route = mock.get(f"{API_BASE}/api/v1/account/plan").mock(
                return_value=httpx.Response(200, json={"tier": "free", "features": {}})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                await client._http.request("GET", "/api/v1/account/plan")

        assert route.calls.last.request.extensions["timeout"] == {
            "connect": 30.0,
            "read": 30.0,
            "write": 30.0,
            "pool": 30.0,
        }

    def test_the_default_is_still_thirty(self) -> None:
        assert DEFAULT_TIMEOUT == 30.0

    def test_the_two_budgets_actually_differ(self) -> None:
        """A vacuity guard: if `STORAGE_TIMEOUT` were ever built from
        `DEFAULT_TIMEOUT` the assertions above would still pass while measuring
        nothing about the separation."""
        assert STORAGE_TIMEOUT.read != DEFAULT_TIMEOUT
        assert STORAGE_TIMEOUT.write != DEFAULT_TIMEOUT


# ── 3. Object state — connect is separated from read ─────────────────


class TestConnectIsSeparableFromRead:
    def test_connect_is_not_widened_with_read(self) -> None:
        """The measured failures point at per-hop fixed costs, not at a slow
        body, so `read` and `write` grow while `connect` stays where it was —
        a handshake that cannot complete in 30 s should surface, not hang for
        five minutes."""
        assert STORAGE_TIMEOUT.connect == 30.0
        assert STORAGE_TIMEOUT.read == 300.0


# ── 4. Error — a timeout still escapes as httpx, deliberately ────────


class TestATransportTimeoutIsNotTranslated:
    @pytest.mark.asyncio
    async def test_a_read_timeout_on_the_storage_hop_propagates_verbatim(self, tmp_path) -> None:
        """`exceptions.py`'s hierarchy states transport errors are exposed as
        `httpx` exceptions verbatim, and that change was scoped to the BUDGET rather
        than to a new exception class. Pinned so that stays a decision rather
        than drifting into one by accident — QUICKSTART now says it too.
        """
        source = tmp_path / "a.txt"
        source.write_bytes(b"x")

        async with respx.mock as mock:
            _stub_upload(mock)
            mock.post(PRESIGN_URL).mock(side_effect=httpx.ReadTimeout("slow"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(httpx.ReadTimeout):
                    await client.files.upload(str(source))
