"""Auto-throttle policy unit tests — pure-function decisions only.

Transport-side wiring (HTTPClient retry + soft-limit header) is exercised
in ``tests/unit/_internal/test_http.py`` and the resource-level tests in
``tests/unit/resources/test_account.py``. This file targets
:mod:`convilyn._internal.throttle` directly so a regression in sleep
math or normalisation can be diagnosed without spinning up httpx.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone

import pytest

from convilyn._internal.throttle import (
    DEFAULT_FALLBACK_SLEEP_SECONDS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_SLEEP_SECONDS,
    AutoThrottleConfig,
    compute_quota_sleep,
    emit_soft_limit_warning,
    resolve_auto_throttle,
    response_has_soft_limit_header,
)
from convilyn.exceptions import QuotaExceededError


def _quota_error(details: dict | None = None) -> QuotaExceededError:
    return QuotaExceededError(
        402,
        "QUOTA_EXCEEDED",
        "Monthly cap reached",
        details,
    )


# ── 1. Logic — resolver normalises every input form ────────────────


class TestResolverLogic:
    def test_none_returns_disabled(self) -> None:
        assert resolve_auto_throttle(None) is None

    def test_false_returns_disabled(self) -> None:
        assert resolve_auto_throttle(False) is None

    def test_true_returns_default_config(self) -> None:
        cfg = resolve_auto_throttle(True)
        assert isinstance(cfg, AutoThrottleConfig)
        assert cfg.max_retries == DEFAULT_MAX_RETRIES
        assert cfg.max_sleep == DEFAULT_MAX_SLEEP_SECONDS
        assert cfg.fallback_sleep == DEFAULT_FALLBACK_SLEEP_SECONDS

    def test_dict_kwargs_passed_through(self) -> None:
        cfg = resolve_auto_throttle({"max_retries": 3, "max_sleep": 10.0})
        assert cfg is not None
        assert cfg.max_retries == 3
        assert cfg.max_sleep == 10.0
        # Untouched field uses the default.
        assert cfg.fallback_sleep == DEFAULT_FALLBACK_SLEEP_SECONDS

    def test_config_returned_verbatim(self) -> None:
        original = AutoThrottleConfig(max_retries=2)
        assert resolve_auto_throttle(original) is original


# ── 2. Boundary — sleep calculation hits every branch ──────────────


class TestQuotaSleepBoundary:
    def test_retry_after_seconds_used_when_present(self) -> None:
        err = _quota_error({"retry_after_seconds": 7})
        cfg = AutoThrottleConfig(max_sleep=30.0)
        assert compute_quota_sleep(err, cfg) == 7.0

    def test_retry_after_string_parsed(self) -> None:
        err = _quota_error({"retry_after_seconds": "12"})
        assert compute_quota_sleep(err, AutoThrottleConfig()) == 12.0

    def test_reset_at_future_yields_positive_delta(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(seconds=10)
        err = _quota_error({"reset_at": future.isoformat()})
        cfg = AutoThrottleConfig(max_sleep=60.0)
        sleep_s = compute_quota_sleep(err, cfg)
        assert sleep_s is not None
        # Allow a small wall-clock drift for test machine.
        assert 8.0 <= sleep_s <= 11.0

    def test_reset_at_past_returns_zero_not_negative(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        err = _quota_error({"reset_at": past.isoformat()})
        assert compute_quota_sleep(err, AutoThrottleConfig()) == 0.0

    def test_no_hints_fall_through_to_fallback(self) -> None:
        err = _quota_error({})
        cfg = AutoThrottleConfig(fallback_sleep=2.5)
        assert compute_quota_sleep(err, cfg) == 2.5

    def test_sleep_exceeding_max_returns_none(self) -> None:
        err = _quota_error({"retry_after_seconds": 999.0})
        assert compute_quota_sleep(err, AutoThrottleConfig(max_sleep=60.0)) is None


# ── 3. Error — invalid inputs raise typed errors, malformed reset ignored ──


class TestErrorHandling:
    def test_resolver_rejects_unknown_type(self) -> None:
        with pytest.raises(TypeError, match="auto_throttle"):
            resolve_auto_throttle(42)  # type: ignore[arg-type]

    def test_negative_max_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            AutoThrottleConfig(max_retries=-1)

    def test_zero_max_sleep_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_sleep"):
            AutoThrottleConfig(max_sleep=0.0)

    def test_negative_fallback_sleep_rejected(self) -> None:
        with pytest.raises(ValueError, match="fallback_sleep"):
            AutoThrottleConfig(fallback_sleep=-0.1)

    def test_malformed_reset_at_falls_back(self) -> None:
        err = _quota_error({"reset_at": "not-a-timestamp"})
        cfg = AutoThrottleConfig(fallback_sleep=3.0)
        assert compute_quota_sleep(err, cfg) == 3.0

    def test_missing_details_falls_back(self) -> None:
        # ``QuotaExceededError`` stores ``details`` as ``{}`` when None
        # is passed, so the sleep calc must tolerate the empty dict.
        err = QuotaExceededError(402, "QUOTA_EXCEEDED", "Cap", None)
        assert compute_quota_sleep(err, AutoThrottleConfig()) == DEFAULT_FALLBACK_SLEEP_SECONDS


# ── 4. Object-state — soft-limit signal channels ──────────────────


class TestSoftLimitWarning:
    def test_warning_emits_user_warning(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            emit_soft_limit_warning(source="https://api.convilyn.com/foo")
        assert any("soft_limit" in str(w.message) for w in captured)

    def test_warning_includes_source(self) -> None:
        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            emit_soft_limit_warning(source="GET /api/v1/foo", details="bar")
        text = "\n".join(str(w.message) for w in captured)
        assert "GET /api/v1/foo" in text
        assert "bar" in text

    def test_header_detection_case_insensitive(self) -> None:
        import httpx

        headers = httpx.Headers({"X-Quota-State": "Soft_Limit"})
        assert response_has_soft_limit_header(headers)

    def test_header_detection_trims_whitespace(self) -> None:
        import httpx

        headers = httpx.Headers({"X-Quota-State": "  soft_limit  "})
        assert response_has_soft_limit_header(headers)

    def test_header_absent_returns_false(self) -> None:
        import httpx

        headers = httpx.Headers({})
        assert not response_has_soft_limit_header(headers)

    def test_header_unrelated_value_returns_false(self) -> None:
        import httpx

        headers = httpx.Headers({"X-Quota-State": "ok"})
        assert not response_has_soft_limit_header(headers)
