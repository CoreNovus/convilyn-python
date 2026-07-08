# Changelog — `convilyn` (consumer SDK)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.1b1] — 2026-07-07

### Security

- **Storage URLs are validated before the client dials them.** The
  client now rejects an upload/download URL whose host resolves to a
  loopback, link-local, or private address (in addition to the existing
  https-only check), so a malformed or tampered response cannot redirect
  an upload or download to an internal target.
- **Downloads are streamed with a size cap** instead of being buffered
  whole in memory, so a very large or hostile response cannot exhaust
  memory. Uploads are likewise capped (`MAX_UPLOAD_BYTES`) and fail fast.
- **`base_url` must be https** for any non-loopback host — the API key
  travels in an `Authorization` header, so an `http://` target is
  refused to avoid sending it in cleartext. Loopback hosts may use http
  for local development.
- **WebSocket URLs must be `wss://`** for any non-loopback host, and
  connection errors no longer include the auth token from the URL.

### Changed

- Docstrings, README, and CHANGELOG were revised for clarity; no public
  API changed.

## [1.1.0] — 2026-07-07

First published release (PyPI). Sections below accumulated since 1.0.1;
the `Removed` entries predate any published version, so no released
consumer is affected.

> **Events are polling-only in v1**: retrieve goal progress with
> `client.goals.wait(...)` / `retrieve(...)`. The WebSocket gateway does
> not accept consumer `ck_` keys yet; `goals.events()` streaming is
> roadmap (see `docs/STABILITY.md`).

### Fixed

- `files.upload` now speaks the backend's presigned-**POST** upload grant: when the presign response carries `fields`, the SDK multipart-POSTs (fields verbatim, file part last) instead of PUTting — the backend switched input uploads to a size-capped S3 POST policy (`content-length-range`) and a PUT against the POST URL fails with S3 403. A grant without `fields` still uses the legacy presigned-PUT path, so the SDK works against both backend generations.
- The synchronous `Convilyn` client now runs every call — and the final `close()` — on **one private, long-lived event loop** instead of a fresh `asyncio.run` loop per call. Per-call loops orphaned pooled `httpx` connections, which touched their already-closed loop at interpreter teardown and crashed the CLI on Windows (`RuntimeError: Event loop is closed`). Calling a sync method after `close()` now raises a clear `RuntimeError`, and calling one from inside a running event loop still raises with guidance to use `AsyncConvilyn`.
- A **failed** conversion job now surfaces `JobFailedError` even when the backend attaches a 0-byte placeholder result file. `ResultFile.size` was `Field(gt=0)`, so parsing a failed job whose `resultFiles[0].size == 0` raised a pydantic `ValidationError` instead — masking the real failure. The bound is now `ge=0`.
- `convert.create` now sends the discriminated-union tag as snake_case `processor_type` (was camelCase `processorType`); the file-conversion `JobRequest` discriminator is `processor_type` per the contract (`processorType` is only the *response* field name), so the previous key made the backend reject every conversion with HTTP 400 `union_tag_not_found`. File conversions now succeed against the current backend.
- The CLI `--json` output now escapes non-ASCII (`ensure_ascii=True`); a raw glyph in a payload (e.g. a `✓` from a job) previously crashed with `UnicodeEncodeError` on a non-UTF-8 console (Windows `cp950`).
- `goals.start(slots=...)` now sends the answers as `slotAnswers` (`[{slotId, value}]`); the previous `slots` payload had no matching field on the create endpoint and was silently dropped, so pre-seeded slot answers never reached the backend.
- Goal-job parsing now coerces wire-`null` `pendingInterrupts` / `pendingSlots` / `fileIds` / `filledSlots` to an empty list/dict via a before-validator; the backend legitimately returns `null` for these, which previously raised a validation error on the non-optional fields and made the whole `GoalJob` unparseable.
- File uploads from a path now send a length-bearing bytes body (so httpx emits `Content-Length`); the previous async-generator body made httpx use `Transfer-Encoding: chunked`, which an S3 presigned PUT rejects with HTTP 501. Path-based uploads now succeed against real storage.

### Changed

- **`goals.events()` failure now points at `wait()` polling.** The WS gateway
  does not accept `ck_` keys in v1, so a connect failure's `WebSocketError`
  message and the method docstring now spell out that WebSocket streaming is
  polling-only for now (use `wait()`), mirroring the consumer-go guidance.
- **Author-SDK / developer-portal tokens (`cvl_` / `cvi_`) are now rejected**
  by `APIKey` / `Convilyn(api_key=...)` with a precise `AuthError`, instead of
  being treated as acceptable consumer keys (they never authenticated against
  the data plane — the backend answered with an opaque 401). The `ck_` prefix
  and any unknown prefix are still accepted (forward-compat). Brings the Python
  consumer SDK in line with the TypeScript one's `auth.ts` guard.

### Removed

- **BREAKING: `GoalJob.workflow_id`** — the backend's `GoalJobResponse` never
  echoes `workflowId` (the *request* accepts it; the response does not), so the
  attribute was always `None` at runtime. Removed pre-first-publish, so no
  released consumer is affected. `goals.start(workflow_id=...)` /
  `run(workflow_id=...)` and the `Workflow` / `WorkflowSummary` models are
  unchanged. `GoalJob` is now conformance-mapped in `sdk/sdks.json`, so any
  future field the wire doesn't speak fails CI instead of shipping silently.

### Added

- **`client.goals.start(..., llm_config_id=...)` / `run(..., llm_config_id=...)`**
  — optionally pin a goal run to one of your stored BYO-LLM provider configs
  (created in the console) so the run executes on your own provider/key. Omit it
  to use your account default. Serialised as `llmConfigId`; honoured only when
  BYO-LLM is enabled for your account, otherwise the run uses the platform
  provider.

### Fixed

- **Default API base URL** — corrected to `https://api.convilyn.corenovus.com`
  (was `https://api.convilyn.com`, which does not serve the API), so a default
  `Convilyn()` reaches the real backend out of the box. The base URL is the host
  root — resource paths carry their own `/api/v1` prefix.
- **API-key prefix** — the canonical consumer key is now `ck_` (minted in the
  API Console / Settings → API), matching the backend (`USER_API_KEY_PREFIX`)
  and the docs. `ACCEPTED_KEY_PREFIXES` now includes `ck_` (the developer-portal
  `cvl_` / `cvi_` tiers stay recognised); the quickstart + `convilyn doctor`
  examples show `ck_`. The runnable `examples/*` now lead with `ck_` too,
  completing the alignment.
- **Stale post-rename references** — after the `sdk-consumer` →
  `sdk-consumer-python` directory rename, the `pyproject.toml`
  `[project.urls]` (Changelog / Source Code), the `examples/03` AGENT.md
  reference, and the `examples/README.md` test-path link now point at
  `sdk-consumer-python`.

### Added

- **`CONVILYN_BASE_URL` environment override** — the client now honours the
  `CONVILYN_BASE_URL` env var (precedence: explicit `base_url=` arg →
  `CONVILYN_BASE_URL` → default), so the CLI and SDK can target a dev/staging
  API without code changes. `convilyn doctor` already surfaced this var and now
  reports the URL the client actually dials.
- **Public-API contract test** (`tests/contract/test_public_surface.py`) —
  freezes `convilyn.__all__`, the per-resource method sets, and the
  exception taxonomy, and fails if any `convilyn._internal` symbol leaks
  into the public namespace or the surface grows unexpectedly. The keystone
  guard behind the SemVer promise; see `docs/STABILITY.md`.
- **`convilyn.config`** — a public module home for the resilience config
  types (`RetryPolicy`, `ExponentialBackoffRetry`, `NoRetry`,
  `AutoThrottleConfig`). They are still re-exported from the top-level
  `convilyn` namespace, so `from convilyn import RetryPolicy` is unchanged —
  but the documented home is now a non-underscore module rather than
  `convilyn._internal`.
- **`docs/STABILITY.md`** — the published stability & versioning policy:
  what the public surface is, the SemVer promise, the deprecation policy,
  and the documented `raw_request` escape-hatch caveat.

- **`Convilyn(auto_throttle=...)`** — opt-in retry loop for
  `QuotaExceededError`. Pass `True` for the default policy (1 retry,
  60 s sleep cap, 5 s fallback delay) or an `AutoThrottleConfig` /
  dict for tuned knobs. The SDK reads the server's
  `details.retry_after_seconds` / `details.reset_at` hint when present
  and gives up immediately if the implied sleep exceeds `max_sleep`,
  so a misconfigured caller never blocks indefinitely.
- **Soft-limit signalling** — any response carrying the
  `X-Quota-State: soft_limit` header now emits a
  `convilyn.throttle` log warning + Python `UserWarning` and returns
  normally (forward-compat wiring; the backend will emit the header
  once the gateway support lands).
- **`client.account.usage_history(*, since=None)`** — list past usage
  periods (one row per metric+period). Wraps
  `GET /api/v1/payment/usage/history`; the new `UsageHistoryEntry`
  model carries `metric`, `period_start`, `period_end`, `used`, and
  optional `limit`. Pair with `client.account.get_quota()` for MTD
  spend reviews.
- `CostEstimate` now exposes `estimated_total_micro_u`,
  `estimated_min_micro_u`, and `estimated_max_micro_u` (wire aliases
  `estimatedTotalMicroU` / `estimatedMinMicroU` / `estimatedMaxMicroU`).
  Use these to render the projected cost range; the legacy
  `estimated_micro_u` upper-bound field stays for back-compat.

### Changed

- Renamed event types on `GoalEventType`: `specialist_started` →
  `agent_step_started`, `specialist_finished` → `agent_step_finished`,
  `handoff` → `orchestration_transition`. CLI glyphs follow.

### Removed

- `CostEstimate.max_iterations` and
  `CostEstimate.llm_cost_per_iter_micro_u` are no longer surfaced on
  the model. Callers should rely on the new cost-range triple
  (min / total / max).

### Documentation

- The package (`convilyn/__init__.py`) and async-client (`client.py`)
  docstrings now reflect the shipped resource surface — they previously
  said resources "land in subsequent releases" / "follow-up commits"
  while `client.files` / `convert` / `goals` / `workflows` / `account`
  already ship.

## [1.0.1] — 2026-06-30

### Security

- **`https`-only scheme guard on backend-supplied URLs.** `external_get` /
  `external_put` (the presigned-URL download/upload paths) now reject any URL
  whose scheme is not `https` (e.g. `http://`, `file://`, an internal address
  over plain HTTP). This is defence-in-depth against a compromised or MITM'd
  backend returning a downgrade/SSRF URL. Scheme — not host — is checked, so
  every legitimate https presign host (S3, CloudFront, custom domains) still
  works.
- **`convert.download_to` refuses to write through an existing symlink** at the
  destination path, so a pre-placed link cannot redirect the downloaded bytes.
  Writing to a regular or new path is unaffected.
- **`convilyn doctor` secret masking tightened** — diagnostics now reveal only
  the first 3 characters (the key tier prefix) and no longer print the trailing
  4 characters of an API key.

## [1.0.0] — 2026-05-24

First production release. The package surface has stabilised across the
R1-R5 workstreams; the bump from `0.1.0` reflects API readiness, not a
breaking change.

### Added

- **`client.account` resource** — `get_plan()` returns the caller's
  billing tier; `get_quota(tools=..., max_iterations=...)` previews
  workflow cost + returns the tier's quota verdict (`ok` /
  `soft_limit` / `quota_exceeded`). Read-only, no side effects.
- **`convilyn account` CLI** — `convilyn account plan` and
  `convilyn account quota` mirror the resource. Both support `--json`
  for pipe consumption.
- **Typed billing exceptions**: `PlanRequiredError` (HTTP 402 +
  `TIER_REQUIRED`) and `QuotaExceededError` (HTTP 402 +
  `QUOTA_EXCEEDED`). Both subclass `APIError`, so existing
  `except APIError:` handlers continue to catch them. Each carries an
  `upgrade_url` for the caller's pricing CTA.
- **`client.workflows` resource** — community marketplace surface:
  `search`, `get`, `fork`, `publish`, `patch`, `like`.
- **`client.goals` resource** — agentic AI workflows: `start`,
  `wait`, `run`, `retrieve`, `fill_slot`, `confirm`, `cancel`, `retry`,
  plus async-only `events()` WebSocket streaming.
- **`convilyn goals` CLI** — drive AI workflows from the shell.
  NDJSON streaming for `events`; pinned exit codes (0 / 1 / 2 / 3 / 130).
- **`convilyn convert` CLI** + `client.convert` + `client.files`
  resources (R1 ship).
- **`convilyn doctor` CLI** — environment + connectivity diagnostics
  for the SDK's dependencies and auth setup.
- **`convilyn api` CLI** — `gh`-style escape hatch for any backend
  endpoint the SDK has not wrapped yet.
- **Production-grade resilience**: retry on 5xx / 429 / 408 with
  exponential backoff + jitter, `Idempotency-Key` auto-stamped on
  mutating verbs, `Retry-After` honoured.

### Changed

- Error envelope handling normalises three shapes: flat
  `{code, message, ...}`, FastAPI `{"detail": {...}}`, and the older
  `{"error": {...}}`. Callers now see the same typed exception
  regardless of which endpoint raised.
- License moved from speculative `Apache-2.0` placeholder to `MIT`
  (matches the repo's existing precedent under `llm-gateway/LICENSE`).

### Documentation

- `docs/QUICKSTART.md` — 5-min Python + CLI walkthrough.
- `docs/README.md` — PyPI landing page.
- `AGENT.md` — SOLID seams + extension points for AI coding agents
  contributing to the SDK.
- 9 runnable examples under `examples/01_*.py` … `09_account_quota.py`.
