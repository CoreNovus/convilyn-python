"""Client initialisation — logic / boundary / error / object-state coverage."""

from __future__ import annotations

import pytest

from convilyn import AsyncConvilyn, AuthError, Convilyn

# ── Logic ────────────────────────────────────────────────────────────


class TestClientLogic:
    def test_sync_client_from_kwarg(self, monkeypatch):
        monkeypatch.delenv("CONVILYN_BASE_URL", raising=False)
        c = Convilyn(api_key="ck_test_explicit")
        try:
            assert c.base_url == "https://api.convilyn.corenovus.com"
            assert c.async_client is not None
        finally:
            c.close()

    def test_async_client_from_kwarg(self, monkeypatch):
        monkeypatch.delenv("CONVILYN_BASE_URL", raising=False)
        c = AsyncConvilyn(api_key="ck_test_explicit")
        assert c.base_url == "https://api.convilyn.corenovus.com"

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://dev-api.example")
        c = AsyncConvilyn(api_key="ck_x")
        assert c.base_url == "https://dev-api.example"

    def test_explicit_base_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("CONVILYN_BASE_URL", "https://dev-api.example")
        c = AsyncConvilyn(api_key="ck_x", base_url="https://explicit.example")
        assert c.base_url == "https://explicit.example"

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("CONVILYN_API_KEY", "ck_from_env")
        c = AsyncConvilyn(api_key="ck_explicit")
        # We rely on APIKey.repr masking; only first 6 chars exposed.
        assert "ck_ex" in repr(c)

    def test_init_from_env(self, monkeypatch):
        monkeypatch.setenv("CONVILYN_API_KEY", "ck_env_only")
        c = AsyncConvilyn()
        assert "ck_en" in repr(c)


# ── Boundary / Error ─────────────────────────────────────────────────


class TestClientErrors:
    def test_missing_key_raises_auth_error(self, monkeypatch):
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        with pytest.raises(AuthError):
            AsyncConvilyn()

    def test_empty_key_raises_auth_error(self):
        with pytest.raises(AuthError):
            AsyncConvilyn(api_key="")

    def test_custom_base_url_respected(self):
        c = AsyncConvilyn(api_key="ck_x", base_url="https://staging.convilyn.com")
        assert c.base_url == "https://staging.convilyn.com"

    def test_trailing_slash_normalised(self):
        c = AsyncConvilyn(api_key="ck_x", base_url="https://staging.convilyn.com/")
        assert c.base_url == "https://staging.convilyn.com"


# ── Object-state — lifecycle ─────────────────────────────────────────


class TestClientLifecycle:
    @pytest.mark.asyncio
    async def test_async_context_manager_closes(self, monkeypatch):
        monkeypatch.delenv("CONVILYN_BASE_URL", raising=False)
        async with AsyncConvilyn(api_key="ck_x") as c:  # pragma: allowlist secret
            assert c.base_url == "https://api.convilyn.corenovus.com"
        # If aclose() didn't run we'd leak the httpx pool; the test fixture
        # would emit a ResourceWarning. Reaching this line cleanly is enough
        # to confirm __aexit__ ran without raising.

    def test_sync_context_manager_closes(self):
        with Convilyn(api_key="ck_x") as c:  # pragma: allowlist secret
            assert c.async_client is not None

    def test_repr_does_not_leak_key(self):
        c = AsyncConvilyn(api_key="ck_sensitive_token_value_12345")
        text = repr(c)
        assert "sensitive_token_value" not in text
        assert "12345" not in text

    def test_sync_calls_and_aclose_share_one_loop(self):
        """#2112 — per-call ``asyncio.run`` orphaned pooled httpx
        connections; every sync call plus the final aclose must observe
        the same running loop."""
        import asyncio

        observed: list[asyncio.AbstractEventLoop] = []

        async def _observe(*_args, **_kwargs):
            observed.append(asyncio.get_running_loop())

        c = Convilyn(api_key="ck_x")
        c._async.goals.retrieve = _observe  # type: ignore[method-assign]
        c._async.account.get_plan = _observe  # type: ignore[method-assign]
        c._async.aclose = _observe  # type: ignore[method-assign]

        c.goals.retrieve("job_x")
        c.account.get_plan()
        c.close()

        assert observed[0] is observed[1] is observed[2]

    def test_sync_close_is_idempotent(self):
        c = Convilyn(api_key="ck_x")
        c.close()

        c.close()  # second close must be a no-op, not a crash

        with pytest.raises(RuntimeError, match="client is closed"):
            c.account.get_plan()
