"""Auth resolution — APIKey + resolve_auth + tier-mismatch rejection + forward-compat."""

from __future__ import annotations

import pytest

from convilyn._internal.auth import (
    ACCEPTED_KEY_PREFIXES,
    AUTHOR_KEY_PREFIXES,
    APIKey,
    is_author_key,
    is_known_prefix,
    resolve_auth,
)
from convilyn.exceptions import AuthError

# ── Logic ────────────────────────────────────────────────────────────


class TestAPIKeyLogic:
    def test_canonical_ck_prefix_headers_format(self):
        # `ck_` is the canonical consumer key prefix (minted in the API
        # Console / Settings → API); it must produce a clean Bearer header.
        a = APIKey("ck_secret")  # pragma: allowlist secret
        assert a.headers() == {"Authorization": "Bearer ck_secret"}

    def test_key_property_exposes_value(self):
        a = APIKey("ck_secret")  # pragma: allowlist secret
        assert a.key == "ck_secret"

    def test_repr_masks_key(self):
        a = APIKey("ck_super_secret_token_value")  # pragma: allowlist secret
        text = repr(a)
        assert "super_secret_token_value" not in text
        assert "ck_su" in text


# ── Boundary / Error ─────────────────────────────────────────────────


class TestAPIKeyErrors:
    def test_empty_string_rejected(self):
        with pytest.raises(AuthError):
            APIKey("")

    def test_non_string_rejected(self):
        with pytest.raises(AuthError):
            APIKey(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize("prefix", AUTHOR_KEY_PREFIXES)
    def test_author_key_rejected_with_precise_message(self, prefix):
        # Pasting an Author-SDK / developer-portal token into the consumer SDK
        # is a mistake we catch up front, not an opaque 401 later.
        with pytest.raises(AuthError, match="consumer API key"):
            APIKey(f"{prefix}some_author_token")  # pragma: allowlist secret

    def test_author_key_not_leaked_in_error(self):
        with pytest.raises(AuthError) as excinfo:
            APIKey("cvl_super_secret_author_token")  # pragma: allowlist secret
        assert "super_secret_author_token" not in str(excinfo.value)


# ── Resolution — explicit > env ──────────────────────────────────────


class TestResolveAuth:
    def test_explicit_takes_precedence(self):
        auth = resolve_auth("ck_explicit", env={"CONVILYN_API_KEY": "ck_env"})
        assert auth.headers() == {"Authorization": "Bearer ck_explicit"}

    def test_env_fallback(self):
        auth = resolve_auth(None, env={"CONVILYN_API_KEY": "ck_env_only"})
        assert auth.headers() == {"Authorization": "Bearer ck_env_only"}

    def test_neither_raises(self):
        with pytest.raises(AuthError):
            resolve_auth(None, env={})

    def test_empty_string_treated_as_missing(self):
        with pytest.raises(AuthError):
            resolve_auth("", env={})

    def test_author_key_via_env_rejected(self):
        token = "cvl_author_token"  # pragma: allowlist secret
        with pytest.raises(AuthError, match="consumer API key"):
            resolve_auth(None, env={"CONVILYN_API_KEY": token})


# ── Forward-compat — unknown prefixes still work ─────────────────────


class TestForwardCompat:
    def test_known_prefixes_recognised(self):
        for prefix in ACCEPTED_KEY_PREFIXES:
            assert is_known_prefix(f"{prefix}example") is True

    def test_unknown_prefix_still_constructs(self):
        # If a future backend mints a `cvx_` consumer tier the SDK should still work.
        a = APIKey("cvx_unknown_tier")
        assert a.headers() == {"Authorization": "Bearer cvx_unknown_tier"}

    def test_unknown_prefix_not_flagged_as_known(self):
        assert is_known_prefix("cvx_unknown_tier") is False

    def test_author_prefixes_flagged(self):
        assert is_author_key("cvl_x") is True
        assert is_author_key("cvi_x") is True
        assert is_author_key("ck_x") is False
        assert is_author_key("cvx_x") is False
