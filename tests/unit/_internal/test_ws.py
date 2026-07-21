"""AsyncGoals.events — logic / boundary / error / object-state.

Uses the shared :class:`FakeWSTransport` / :func:`make_envelope` helpers
in :mod:`tests._fixtures.ws_fakes` so multiple test modules can drive
the WS streamer deterministically without spinning up a real
WebSocket. The fake yields canned frames via an ``asyncio.Queue`` so
tests can interleave server-side and client-side actions in lock-step.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from convilyn import AsyncConvilyn, Convilyn, GoalEvent, WebSocketError
from convilyn._internal.ws import (
    WebsocketsTransport,
    build_ws_connect_url,
    resolve_ws_url,
)
from tests._fixtures.ws_fakes import FakeWSTransport
from tests._fixtures.ws_fakes import make_envelope as _envelope


async def _consume(it: AsyncIterator[GoalEvent]) -> list[GoalEvent]:
    out: list[GoalEvent] = []
    async for ev in it:
        out.append(ev)
    return out


def _client(transport: FakeWSTransport, *, ws_url: str = "wss://test/v1") -> AsyncConvilyn:
    return AsyncConvilyn(
        api_key="ck_test",  # pragma: allowlist secret
        ws_url=ws_url,
        ws_transport_factory=lambda: transport,
    )


# ── 1. Logic — happy path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_events_yields_in_order_and_self_closes_on_completed():
    transport = FakeWSTransport(
        [
            _envelope("progress", seq=1, data={"percent": 25}),
            _envelope("tool_started", seq=2, data={"toolName": "extract"}),
            _envelope("tool_finished", seq=3, data={"toolName": "extract"}),
            _envelope("completed", seq=4, data={"result": "ok"}),
        ]
    )
    async with _client(transport) as client:
        events = await _consume(client.goals.events("job_test"))

    assert [e.type for e in events] == ["progress", "tool_started", "tool_finished", "completed"]
    assert [e.seq for e in events] == [1, 2, 3, 4]
    assert events[-1].is_terminal is True


@pytest.mark.asyncio
async def test_subscribe_envelope_sent_before_first_recv():
    transport = FakeWSTransport([_envelope("completed", seq=1)])
    async with _client(transport) as client:
        await _consume(client.goals.events("job_subscribe_test"))

    assert len(transport.sent) == 1
    sent = json.loads(transport.sent[0])
    assert sent == {"action": "subscribe", "jobSpecId": "job_subscribe_test"}


@pytest.mark.asyncio
async def test_ws_url_precedence_explicit_over_ctor_over_env(monkeypatch):
    monkeypatch.setenv("CONVILYN_WS_URL", "wss://from-env/v1")
    # explicit wins
    assert (
        resolve_ws_url(explicit="wss://explicit/v1", fallback="wss://ctor/v1")
        == "wss://explicit/v1"
    )
    # ctor wins over env
    assert resolve_ws_url(explicit=None, fallback="wss://ctor/v1") == "wss://ctor/v1"
    # env is last
    assert resolve_ws_url(explicit=None, fallback=None) == "wss://from-env/v1"


# ── 2. Boundary — all known event types parse, forward-compat fields ─


@pytest.mark.asyncio
async def test_all_known_event_types_parse():
    known_types = [
        "tool_started",
        "tool_finished",
        "agent_step_started",
        "agent_step_finished",
        "orchestration_transition",
        "status",
        "progress",
        "slot_needed",
        "keepalive",
        "agent_text",
        "agent_text_done",
    ]
    script = [_envelope(t, seq=i) for i, t in enumerate(known_types, start=1)]
    # Then a terminal so the iterator returns.
    script.append(_envelope("completed", seq=len(known_types) + 1))

    transport = FakeWSTransport(script)
    async with _client(transport) as client:
        events = await _consume(client.goals.events("job_test"))

    assert len(events) == len(known_types) + 1
    assert [e.type for e in events[:-1]] == known_types


@pytest.mark.asyncio
async def test_extra_top_level_fields_tolerated():
    """Forward-compat: an older SDK must keep working when the server
    starts emitting additional envelope fields like ``correlationId``.
    """
    raw = json.dumps(
        {
            "type": "completed",
            "schemaVersion": 2,
            "jobSpecId": "job_test",
            "emittedAt": "2026-05-20T12:00:00Z",
            "seq": 1,
            "data": {},
            "correlationId": "abc-123",  # not declared on GoalEvent
            "extraNested": {"foo": "bar"},
        }
    )
    transport = FakeWSTransport([raw])
    async with _client(transport) as client:
        events = await _consume(client.goals.events("job_test"))
    assert len(events) == 1
    assert events[0].type == "completed"


@pytest.mark.asyncio
async def test_missing_seq_defaults_to_zero():
    raw = json.dumps(
        {
            "type": "completed",
            "schemaVersion": 2,
            "jobSpecId": "job_test",
            "emittedAt": "2026-05-20T12:00:00Z",
            "data": {},
            # no seq
        }
    )
    transport = FakeWSTransport([raw])
    async with _client(transport) as client:
        events = await _consume(client.goals.events("job_test"))
    assert events[0].seq == 0


@pytest.mark.asyncio
async def test_missing_ws_url_raises_value_error(monkeypatch):
    monkeypatch.delenv("CONVILYN_WS_URL", raising=False)
    transport = FakeWSTransport([_envelope("completed", seq=1)])
    client = AsyncConvilyn(
        api_key="ck_test",  # pragma: allowlist secret
        # NO ws_url, NO env var
        ws_transport_factory=lambda: transport,
    )
    try:
        with pytest.raises(ValueError, match="No WebSocket URL configured"):
            await _consume(client.goals.events("job_test"))
    finally:
        await client.aclose()


# ── 3. Error — malformed payloads, transport failures ────────────────


@pytest.mark.asyncio
async def test_malformed_json_raises_websocket_error():
    transport = FakeWSTransport(["this is not json"])
    async with _client(transport) as client:
        with pytest.raises(WebSocketError, match="non-JSON message") as excinfo:
            await _consume(client.goals.events("job_test"))
    assert excinfo.value.payload == "this is not json"


@pytest.mark.asyncio
async def test_unknown_event_type_raises_websocket_error():
    raw = json.dumps(
        {
            "type": "not_a_known_type",
            "schemaVersion": 2,
            "jobSpecId": "job_test",
            "emittedAt": "2026-05-20T12:00:00Z",
            "data": {},
        }
    )
    transport = FakeWSTransport([raw])
    async with _client(transport) as client:
        with pytest.raises(WebSocketError, match="unrecognised event envelope") as excinfo:
            await _consume(client.goals.events("job_test"))
    assert excinfo.value.payload == raw


@pytest.mark.asyncio
async def test_connect_failure_raises_websocket_error():
    transport = FakeWSTransport([], fail_on_connect=True)
    async with _client(transport) as client:
        with pytest.raises(WebSocketError, match="Failed to open event stream"):
            await _consume(client.goals.events("job_test"))


@pytest.mark.asyncio
async def test_mid_stream_disconnect_raises_websocket_error():
    # No terminal event; the fake's recv() raises ConnectionError when
    # the queue is exhausted, simulating a server-side disconnect.
    transport = FakeWSTransport([_envelope("progress", seq=1, data={"percent": 50})])
    async with _client(transport) as client:
        with pytest.raises(WebSocketError, match="Event stream dropped"):
            await _consume(client.goals.events("job_test"))


# ── 4. Object-state — close() invariants + sync regression guard ─────


@pytest.mark.asyncio
async def test_close_called_once_on_happy_path():
    transport = FakeWSTransport([_envelope("completed", seq=1)])
    async with _client(transport) as client:
        await _consume(client.goals.events("job_test"))
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_close_called_when_caller_breaks_early():
    transport = FakeWSTransport(
        [
            _envelope("progress", seq=1, data={"percent": 25}),
            _envelope("progress", seq=2, data={"percent": 50}),
            _envelope("completed", seq=3),
        ]
    )
    async with _client(transport) as client:
        gen = client.goals.events("job_test")
        first = await gen.__anext__()
        await gen.aclose()  # mimics `async for` body raising / breaking

    assert first.type == "progress"
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_close_called_on_parse_error():
    transport = FakeWSTransport(["{not json"])
    async with _client(transport) as client:
        with pytest.raises(WebSocketError):
            await _consume(client.goals.events("job_test"))
    assert transport.close_calls == 1


@pytest.mark.asyncio
async def test_token_appended_to_connect_url():
    transport = FakeWSTransport([_envelope("completed", seq=1)])
    async with _client(transport, ws_url="wss://test/v1") as client:
        await _consume(client.goals.events("job_test"))
    assert transport.connected_url == "wss://test/v1?token=ck_test"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_per_call_ws_url_override_wins():
    transport = FakeWSTransport([_envelope("completed", seq=1)])
    async with _client(transport, ws_url="wss://ctor/v1") as client:
        await _consume(client.goals.events("job_test", ws_url="wss://percall/v1"))
    assert transport.connected_url is not None
    assert transport.connected_url.startswith("wss://percall/v1?token=")


def test_sync_goals_does_not_expose_events():
    """Streaming is async-only. Regression guard against an
    accidental future commit mirroring ``events`` onto sync ``Goals``.
    """
    sync_client = Convilyn(api_key="ck_test")  # pragma: allowlist secret
    try:
        assert not hasattr(sync_client.goals, "events")
    finally:
        sync_client.close()


def test_build_ws_connect_url_handles_existing_query_string():
    assert (
        build_ws_connect_url("wss://host/v1?foo=bar", token="ck_x")  # pragma: allowlist secret
        == "wss://host/v1?foo=bar&token=ck_x"
    )
    assert (
        build_ws_connect_url("wss://host/v1", token="ck_x")  # pragma: allowlist secret
        == "wss://host/v1?token=ck_x"
    )


# ── 5. WebsocketsTransport — production impl wrapping the library ────
#
# The class lazy-imports `websockets.asyncio.client.connect`, so tests
# patch that symbol with an AsyncMock returning a mock connection.
# These cover lines 57-93 (the entire WebsocketsTransport class) which
# were 0% covered before WS-2A PR 3.


class _FakeWS:
    """Mock object returned by the patched `ws_connect`.

    Mirrors the subset of the `websockets` client surface that
    :class:`WebsocketsTransport` uses (send / recv / close).
    """

    def __init__(self, *, recv_values: list[str | bytes] | None = None) -> None:
        self.recv_values = list(recv_values or [])
        self.sent: list[str] = []
        self.close_calls = 0

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str | bytes:
        return self.recv_values.pop(0)

    async def close(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_websockets_transport_connect_forwards_ping_kwargs(monkeypatch):
    """The lazy `ws_connect` import must receive ping_interval=240,
    ping_timeout=30 from the constructor defaults — those values keep
    AWS API Gateway's 10-min idle timeout from killing live streams.
    """
    captured_kwargs: dict[str, float] = {}
    fake_ws = _FakeWS()

    async def fake_connect(url, **kwargs):
        captured_kwargs.update(kwargs)
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport()

    await transport.connect("wss://example.test/v1")

    assert captured_kwargs == {"ping_interval": 240.0, "ping_timeout": 30.0}


@pytest.mark.asyncio
async def test_websockets_transport_connect_honours_custom_keepalive(monkeypatch):
    """Custom ping_interval / ping_timeout flow through to the library."""
    captured_kwargs: dict[str, float] = {}

    async def fake_connect(url, **kwargs):
        captured_kwargs.update(kwargs)
        return _FakeWS()

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport(ping_interval=10.0, ping_timeout=2.0)

    await transport.connect("wss://example.test/v1")

    assert captured_kwargs == {"ping_interval": 10.0, "ping_timeout": 2.0}


@pytest.mark.asyncio
async def test_websockets_transport_send_before_connect_raises_runtime_error():
    transport = WebsocketsTransport()

    with pytest.raises(RuntimeError, match="send called before connect"):
        await transport.send("hello")


@pytest.mark.asyncio
async def test_websockets_transport_recv_before_connect_raises_runtime_error():
    transport = WebsocketsTransport()

    with pytest.raises(RuntimeError, match="recv called before connect"):
        await transport.recv()


@pytest.mark.asyncio
async def test_websockets_transport_close_before_connect_is_noop():
    """No raise, no underlying call — matches the "if self._ws is None: return" guard."""
    transport = WebsocketsTransport()

    result = await transport.close()

    assert result is None


@pytest.mark.asyncio
async def test_websockets_transport_send_forwards_payload(monkeypatch):
    fake_ws = _FakeWS()

    async def fake_connect(url, **kwargs):
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport()
    await transport.connect("wss://example.test/v1")

    await transport.send('{"action": "subscribe"}')

    assert fake_ws.sent == ['{"action": "subscribe"}']


@pytest.mark.asyncio
async def test_websockets_transport_recv_returns_str_unchanged(monkeypatch):
    fake_ws = _FakeWS(recv_values=['{"type": "completed"}'])

    async def fake_connect(url, **kwargs):
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport()
    await transport.connect("wss://example.test/v1")

    message = await transport.recv()

    assert message == '{"type": "completed"}'


@pytest.mark.asyncio
async def test_websockets_transport_recv_decodes_bytes_as_utf8(monkeypatch):
    """Defensive UTF-8 decode — backend only emits text frames today but
    a future binary-frame regression would otherwise type-pollute.
    """
    fake_ws = _FakeWS(recv_values=['{"type": "完成"}'.encode()])

    async def fake_connect(url, **kwargs):
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport()
    await transport.connect("wss://example.test/v1")

    message = await transport.recv()

    assert message == '{"type": "完成"}'


@pytest.mark.asyncio
async def test_websockets_transport_close_resets_state(monkeypatch):
    """After close() the internal _ws reference is None, so a second
    close() is the silent no-op the connect-guard provides — verified
    by call-count on the underlying mock.
    """
    fake_ws = _FakeWS()

    async def fake_connect(url, **kwargs):
        return fake_ws

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect, raising=False)
    transport = WebsocketsTransport()
    await transport.connect("wss://example.test/v1")

    await transport.close()
    await transport.close()  # second call hits the None guard, not ws.close()

    assert fake_ws.close_calls == 1


# ── wss scheme guard (cleartext-token defence) ──────────────────────


class TestWssGuard:
    @pytest.mark.parametrize("bad", ["ws://gw.example.com/v1", "http://gw.example.com"])
    def test_non_wss_non_loopback_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError, match="insecure WebSocket"):
            resolve_ws_url(explicit=bad, fallback=None)

    @pytest.mark.parametrize("ok", ["ws://localhost:8000", "ws://127.0.0.1:9000"])
    def test_ws_loopback_allowed(self, ok: str) -> None:
        assert resolve_ws_url(explicit=ok, fallback=None) == ok

    def test_wss_allowed(self) -> None:
        assert resolve_ws_url(explicit="wss://gw.example.com/v1", fallback=None) == (
            "wss://gw.example.com/v1"
        )
