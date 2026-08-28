"""What `goals.understand()` TELLS the caller when the backend refuses (#4204).

Extracted rather than appended to `test_goals.py`: that file is grandfathered in
the repo's file-size ratchet, whose failure message is an instruction —
*"Extract something instead of raising the number."* It also mirrors a split the
package already makes, `_goals_markdown.py` / `test_goals_to_markdown.py`.

The defect these cover is a chain, and every link discarded the same sentence:

1. the backend rendered a message written for the markdown axis
   (`outputFormat=None …`) at a schema-axis caller who never sent the field;
2. the goal-lane route collapsed the domain error to
   `HTTPException(400, detail=<str>)`, dropping `code` and `details`;
3. `_flatten_error_envelope` did not recognise a **string** `detail`, so
   `message` degraded to the HTTP reason phrase — `"Bad Request"`;
4. `understand()` then built `UnderstandUnavailableError()` with no argument,
   dropping even that.

Link 2 is deliberately still there — changing the wire shape reaches the
contract and the frontend. These tests pin links 3 and 4, which is what makes
the backend's real reason visible to an SDK caller.

## Two corrections added alongside the doc-eval SDK launch harness (PLAN.md / #4660)

Its own negative-case table originally read: "A modality with no pipeline ->
UnderstandUnavailableError, naming the free path" and "A malformed JSON Schema
-> TypeError before any spend." Both are true only on a narrower slice than
stated, checked against live code rather than assumed:

* **The free-path claim is markdown-axis only.** `understand()`'s own
  unmapped-modality refusal (`understanding_admission._UNMAPPED_MODALITY_MESSAGE`)
  explicitly does NOT name a free alternative — that module's own comment:
  "none offers a 'free alternative', because on this axis there is not one and
  inventing one would repeat the defect these replace."
  `TestUnmappedModalityDoesNotClaimAFreeAlternative` asserts this against the
  server's CURRENT wording, which the backend is free to reword — it is not a
  guard against the mechanism drifting back to the old bug, only a record of
  what the message says today. `test_the_error_names_the_free_alternative` in
  `test_goals_to_markdown.py` already covers the markdown-axis half, where the
  claim IS true.
* **Only a non-dict `schema` is a `TypeError`.** A syntactically-valid dict
  that is an invalid JSON Schema round-trips to the server
  (`output_schema_admission.validate_output_schema`, via `jsonschema`'s
  `Draft202012Validator.check_schema`) and comes back as
  `UnderstandUnavailableError`, not `TypeError` — still before `confirm`/billing,
  but not a client-side check. `TestSchemaValidationErrorShapes` pins both
  halves.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn, UnderstandUnavailableError

API_BASE = "https://api.convilyn.com"

_SCHEMA = {"type": "object", "properties": {"total": {"type": "number"}}}


# ── logic: the server's reason survives to the caller ──────────────────


class TestTheServersReasonReachesTheCaller:
    @pytest.mark.asyncio
    async def test_a_string_detail_refusal_names_the_real_cause(self) -> None:
        """A caller who sent three files is told about the three files.

        `{"detail": "<str>"}` is the shape the goal lane actually emits, so this
        exercises links 3 and 4 together and fails if either is reverted.
        """
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400,
                    json={
                        "detail": (
                            "Structured understanding currently accepts one file per "
                            "request, and this request carries 3."
                        )
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError, match="carries 3"):
                    await client.goals.understand(["a", "b", "c"], schema=_SCHEMA)

    @pytest.mark.asyncio
    async def test_the_refusal_no_longer_claims_the_feature_is_missing(self) -> None:
        """The half that was actively false.

        The class default asserts the platform does not support understanding
        *at all*. For a request the platform understood and refused, that is a
        wrong claim about the product, not merely a vague one.
        """
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(400, json={"detail": "this request carries 3 files."})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError) as info:
                    await client.goals.understand(["a", "b", "c"], schema=_SCHEMA)

        assert "does not support" not in str(info.value)


# ── boundary: a refusal that explained nothing must not get worse ──────


class TestABodylessRefusalKeepsTheClassDefault:
    """`_decode_error` substitutes the HTTP reason phrase when the body carries
    no `message`, so `exc.message` is NEVER empty and truthiness cannot separate
    "the server explained" from "the server said nothing".

    Forwarding it unconditionally answers a genuine "not supported" with
    `"Not Implemented"` — a status label, strictly less informative than the
    default it would replace. That is a regression the passthrough could easily
    have shipped, so it is pinned rather than assumed.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 422, 501])
    async def test_the_platform_default_survives(self, status: int) -> None:
        """422 is in this list deliberately, not for symmetry.

        httpx spells it "Unprocessable Entity" and `http.HTTPStatus` spells it
        "Unprocessable Content". A guard written against the stdlib table passes
        404 and 501 and leaks the label on exactly this one status — measured,
        not supposed.
        """
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(status, json={})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError, match="does not support"):
                    await client.goals.understand(["file_abc"], schema=_SCHEMA)


# ── object-state: the TYPE is unchanged, which is what keeps 3.0.0 safe ──


class TestTheRaisedTypeIsUnchanged:
    """`UnderstandUnavailableError` is public surface on a published package
    (`convilyn` 3.0.0, PyPI). Only the message improves here; giving the
    wrong-request case its own type needs a wire discriminator that does not
    exist and a major bump. This is the falsifiable form of "not breaking".
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 404, 422, 501])
    async def test_every_unsupported_status_still_raises_the_same_class(self, status: int) -> None:
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(status, json={"detail": "nope"})
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError):
                    await client.goals.understand(["file_abc"], schema=_SCHEMA)


# ── logic: mixed-modality refusal names the mixed kinds ────────────────


class TestMixedModalityRefusal:
    @pytest.mark.asyncio
    async def test_a_mixed_modality_refusal_names_the_kinds(self) -> None:
        """Mirrors the multi-file test above, one refusal reason over — the
        server's `_MIXED_MODALITY_MESSAGE` mentions the specific kinds mixed,
        not just a generic count."""
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400,
                    json={
                        "detail": (
                            "Structured understanding currently accepts one kind of "
                            "file per request, and this request mixes image, video. "
                            "Send each kind as its own request."
                        )
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError, match="mixes image, video"):
                    await client.goals.understand(["a", "b"], schema=_SCHEMA)


# ── logic: the correction — no free-alternative claim on the schema axis ──


class TestUnmappedModalityDoesNotClaimAFreeAlternative:
    """`to_markdown()`'s equivalent refusal DOES name file-conversion as a
    free alternative (`test_the_error_names_the_free_alternative` in
    `test_goals_to_markdown.py`). `understand()`'s does not, and must not —
    there genuinely is no free path to schema-constrained extraction, and
    claiming one would repeat the defect this whole file's chain describes."""

    @pytest.mark.asyncio
    async def test_the_refusal_names_no_free_alternative(self) -> None:
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400,
                    json={
                        "detail": (
                            "Structured understanding has no pipeline for archive "
                            "files yet. Documents, images, audio and video are "
                            "supported."
                        )
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(UnderstandUnavailableError) as info:
                    await client.goals.understand(["file_abc"], schema=_SCHEMA)

        message = str(info.value).lower()
        assert "free" not in message
        assert "conversion" not in message


# ── boundary / error: what "malformed schema" actually raises ─────────────


class TestSchemaValidationErrorShapes:
    """Two distinct failure shapes share the vague name "malformed schema",
    and only one of them is a client-side `TypeError`."""

    @pytest.mark.asyncio
    async def test_a_non_dict_schema_raises_typeerror_before_any_network_call(self) -> None:
        async with respx.mock as mock:
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(TypeError, match="JSON Schema dict"):
                    await client.goals.understand(["file_abc"], schema="not-a-dict")  # type: ignore[arg-type]
        assert len(mock.calls) == 0

    @pytest.mark.asyncio
    async def test_a_syntactically_valid_but_invalid_schema_is_not_a_typeerror(self) -> None:
        """A dict that fails `Draft202012Validator.check_schema` server-side
        (a bad `type` value, here) passes the client-side dict check, round-
        trips to the server, and comes back as `UnderstandUnavailableError` —
        carrying the server's `jsonschema.SchemaError` text — not `TypeError`."""
        async with respx.mock as mock:
            mock.post(f"{API_BASE}/api/v1/jobs/goal").mock(
                return_value=httpx.Response(
                    400,
                    json={
                        "detail": (
                            "outputSchema is not a valid JSON Schema: "
                            "'not_a_real_type' is not valid under any of the given schemas"
                        )
                    },
                )
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(
                    UnderstandUnavailableError, match="not a valid JSON Schema"
                ) as info:
                    await client.goals.understand(["file_abc"], schema={"type": "not_a_real_type"})
        assert not isinstance(info.value, TypeError)
