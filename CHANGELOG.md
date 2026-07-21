# Changelog — `convilyn` (consumer SDK)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.2.0b13] - 2026-07-21

### Changed

- Install and development instructions are now uv-first across the README and examples (pip remains a documented alternative). No API or behaviour change.

## [1.2.0b12] - 2026-07-21

### Changed

- Documentation polish across the public surface: docstrings, guides, examples, and this changelog now use plain product language throughout (internal shorthand and tracker references removed). No API or behaviour change.

### Added

- **`goals.understand(files, *, schema, instructions=None)` — grounded,
  schema-constrained understanding.** Returns a result that
  conforms to `schema` (a plain JSON Schema dict — language-neutral, no new
  client-side validation dependency) and is grounded by the platform before it
  is returned, instead of a freeform `goal_text` answer. **Safe-degrade:** when
  the connected platform does not yet support schema-grounded understanding, it
  raises the new `UnderstandUnavailableError` rather than silently returning an
  ungrounded result (402/429/5xx propagate unchanged). async + sync. `extract()`
  is now **deprecated** in favour of `understand()` (see _Deprecated_ below).
- **`client.builder` — chat-driven workflow authoring.** Build a `uw_`
  workflow by conversation from your own client (parity with the web app):
  `create_session()` → `send_message()` → on a `register` verdict read
  `BuilderTurn.registered_workflow_id` and hand it to `goals.run(...)`. Also
  `get_session()`, `messages()`, and `quota()` (async + sync). Requires a
  Pro-tier account (402 `TIER_REQUIRED`; the `discover` sub-mode is exempt). New
  public types `BuilderSession` / `BuilderTurn` / `BuilderMessage` /
  `BuilderMessageList` / `BuilderPendingSlot` / `BuilderAttachment` /
  `BuilderQuota`.

### Deprecated

- **`goals.extract()` — superseded by `goals.understand()`.** `extract()`
  now emits a `DeprecationWarning`. It runs a single fixed workflow with no
  caller control over the output shape, whereas `understand(files, schema=...)`
  returns a result that conforms to a caller-supplied JSON Schema and is grounded
  by the platform before it is returned. `extract()` keeps working unchanged (a
  thin wrapper over the same `run() → artifacts() → parse` machinery
  `understand()` reuses) for back-compat; migrate to `understand()` for a
  guaranteed, grounded shape.

## [1.2.0b11] — 2026-07-21

### Added

- **`client.user_workflows` — typed management namespace for the workflows you
  author.** `list()` (cursor-paged), `get()`, `runs()`, `export()`
  (portable JSON document + `X-Export-Schema-Version`), `delete()` (409
  `WORKFLOW_IS_PUBLIC_USE_ARCHIVE` while public). async + sync. Wraps the
  curated `/user_workflows/*` management subset now declared in the SDK
  contract (`sdk_public_openapi.yaml`); pairs with
  `goals.run(user_workflow_id=…)` for running them and `client.builder` for
  authoring them. New top-level models: `UserWorkflowSummary`,
  `UserWorkflowsPage`, `UserWorkflowDetail`, `UserWorkflowRun`,
  `UserWorkflowExport`. Community-gallery workflows by other authors remain
  under `client.workflows` — the two namespaces stay deliberately distinct
  (minor, additive).
- The public API-surface test now also covers the `builder` resource methods
  and the seven client resource accessors.

## [1.2.0b10] — 2026-07-18

### Added

- **`goals.start(user_workflow_id=...)` / `run(...)` / `run_interactive(...)`
  (async + sync) — run a Builder-authored workflow (`uw_...`) on the typed SDK
  surface.** Previously only built-in catalog workflows (`workflow_id=`) or
  natural-language goals (`goal_text=`) were typed parameters; a user-authored
  workflow had to be sent through the `raw_request` escape hatch. The three
  workflow sources (`workflow_id` / `user_workflow_id` / `goal_text`) are now
  mutually exclusive and validated client-side (exactly one required). A
  `user_workflow_id` run may start with no `files` (it can collect them via
  checkpoints); only the `goal_text`-only NLP path still requires `files`. The
  `convilyn goals start` CLI gains a matching `--user-workflow-id` flag. Wire
  key: `userWorkflowId` (already on the create contract; this exposes it as a
  first-class SDK argument).

## [1.2.0b9] — 2026-07-18

### Fixed

- **`PlanTier` now includes `"business"`.** A business-tier account's
  `account.get_plan()` / `account.get_quota()` response previously raised a
  pydantic validation error because `PlanTier` was `Literal["free", "pro"]`.
  The literal now mirrors the backend plan catalog (`free` / `pro` /
  `business`). `get_plan()`'s docstring no longer references a phantom
  `/billing/plan` endpoint — `cost-preview` is documented as the SDK's
  canonical tier source (the `ck_`-accepting endpoint that returns
  `quotaCheck.tier`; the web app's JWT-only `/payment/subscription` is not
  reachable with a `ck_` key).

### Tests

- **Public-surface contract test truthed up to the shipped surface.** The
  keystone guard (`tests/contract/test_public_surface.py`) had drifted: its
  frozen sets never caught up with three already-released additions —
  `goals.extract()` (`1.2.0b6`), `client.files.list()` + its `FileList` /
  `StoredFile` / `StorageUsage` exports (`1.2.0b8`), and `goals.run_interactive()`
  (`1.2.0b8`) — so the guard failed against the code it is meant to protect. The
  frozen `__all__` and per-resource method sets now match the shipped surface.
  No public API change — the contract snapshot catches up. This unblocks the
  pre-publish gate (a red keystone test is a publish blocker).

## [1.2.0b8] — 2026-07-13

### Added

- **`goals.run_interactive(on_slot=, on_preview=)`** (async + sync) — drives the
  whole human-in-the-loop lifecycle to a terminal state so callers no longer
  hand-roll the `slots_pending → fill_slot → confirm → wait` loop. Reacts to
  each stop: `slots_pending` → `on_slot(slot, job)` for each pending slot →
  `fill_slots`; `ready` → `confirm`; `ready_with_preview` → `on_preview(job)`
  (default approve) → `confirm`/`cancel`; terminal → return. Callbacks may be
  sync or async. A `max_rounds` guard (`GoalJobTimeoutError(reason="rounds")`)
  bounds a runaway callback. Reuses the existing `wait`/`fill_slots`/`confirm`
  primitives — no new endpoint, no changed semantics. (Compiled/silent-mode
  workflows never stop for input, so this just runs them to completion.)

### Added

- **`client.files.list()`** (async + sync) — lists your **durable** stored
  files (e.g. emailed-in attachments) with a storage-usage summary
  (`used_bytes` / `free_bytes` / `over_quota`). Returns typed `FileList` /
  `StoredFile` / `StorageUsage`. Note: ordinary uploads are ephemeral and are
  removed by the platform's ~1-hour cleanup, so a just-uploaded transient file
  is **not** listed here — this surfaces durable storage only. An
  unauthenticated caller gets an empty list.

## [1.2.0b6] — 2026-07-12

### Added

- **`goals.extract(files)`** (async + sync) — one-call document extraction.
  Sugar over `start()` → `wait()` → `artifacts()` for the common "image/PDF →
  one JSON object" case, so single-step extraction no longer pays the full
  Goal Lane lifecycle boilerplate (start/wait/fetch/parse). Runs the platform's
  document-extraction workflow and returns the parsed JSON of the job's primary
  JSON artifact. It is **not** a new inference product — the understanding comes
  from the same platform workflow; this only collapses the run-then-fetch-then-
  parse dance into one method. A caller-supplied output `schema` is not yet
  supported (roadmap); to steer the extraction, call `run()` + `artifacts()`.

## [1.2.0b5] — 2026-07-11

### Added

- **Status-aware waiting: `goals.wait(..., idle_timeout=)` / `goals.run(..., idle_timeout=)`.**
  Long agentic runs (7–9 min analyze/execute phases are normal) made the flat
  300 s `timeout` a bad trade-off — give up on healthy jobs or wait forever on
  wedged ones. `idle_timeout` bounds the time tolerated **without any status
  or progress change**; a job that keeps advancing holds the loop open. On an
  idle trip, `GoalJobTimeoutError.reason == "idle"` (total-budget trips carry
  `"total"`); the message says the job may still be healthy on a long phase.
  Fully backward compatible — omitted, behaviour is unchanged.

## [1.2.0b4] — 2026-07-11

### Added

- **`client.files.delete(file_id)`** (async + sync) — deletes an uploaded
  file's cloud copy (storage object + metadata record) the moment you are
  done with it, instead of waiting for the platform's ~1-hour automatic
  cleanup. Only the uploader can delete (404 otherwise); a file attached to
  a still-running job returns 409 `FILE_IN_USE`. Aimed at privacy-sensitive
  callers (e.g. edge devices processing family documents) who want
  deterministic control over cloud retention.

## [1.2.0b3] — 2026-07-11

### Fixed

- **`goals.confirm()` without `expected_version` no longer fails.** The SDK
  used to send a body-less POST when no version was supplied; backends that
  declare the confirm body as a required parameter rejected it with a
  validation error before the handler ran. The SDK now always sends a JSON
  object (`{}` when empty). Server-side, the confirm body is also optional
  now, and `expectedVersion` omission on `fill_slots` is documented: the
  server conditions the write on the version it just read, so you only need
  to pass `job.item_version` when you want strict read-your-write locking.

### Docs

- `fill_slots()` / `confirm()` docstrings now spell out the optimistic-locking
  semantics (when to pass `expected_version`, what a 409 means).

## [1.2.0b2] — 2026-07-11

### Added

- **`client.workflows.catalog()`** (async + sync) — lists the platform's
  built-in goal-lane workflow catalog (`GET /workflows/catalog`), returning
  the new `CatalogWorkflow` type (workflow id, name, supported inputs,
  locales, `tier` / `free_tier_allowed` gate hints). Previously
  `workflows.search()` only reached the user-published community listing,
  so the built-in catalog was invisible to SDK callers.

## [1.2.0b1] — 2026-07-11

### Added

- **AI-workflow output artifacts are now reachable from the SDK.** Three new
  methods on `client.goals` (async + sync):
  - `goals.artifacts(job_spec_id)` → `list[Artifact]` — every output artifact
    of a completed/partial job, each with a presigned `download_url` valid
    for 1 hour;
  - `goals.download_artifact_url(job_spec_id, artifact_id)` →
    `ArtifactDownload` — mint a fresh presigned URL for one artifact;
  - `goals.download_artifact_to(job_spec_id, artifact_id, to=...)` — stream
    one artifact to disk (same size-capped streaming + symlink refusal as
    `convert.download_to`).
  Previously the only way to retrieve an AI-workflow's output was to call
  `GET /jobs/goal/{id}/artifacts` by hand outside the SDK.
- New public types `Artifact` and `ArtifactDownload` (exported at top level),
  wired into the conformance harness against the contract's new
  `OutputArtifact` / `DownloadInfo` schemas.

## [1.1.1b5] — 2026-07-10

### Fixed

- **`import convilyn` no longer crashes on Python 3.10.** `client.py` /
  `sync_client.py` used `typing.Self` (Python 3.11+, PEP 673) despite the
  package declaring `Requires-Python: >=3.10`; both now fall back to
  `typing_extensions.Self` on 3.10 (already an unconditional dependency for
  `python_version < '3.11'`, just not wired up until now). A second,
  previously-masked 3.10 incompatibility in `_internal/throttle.py`
  (`from datetime import UTC`, also 3.11+ — hidden behind the `typing.Self`
  crash until that one was fixed) is corrected the same way, using
  `datetime.timezone.utc`, which has always been available.

## [1.1.1b4] — 2026-07-09

### Fixed

- Public-mirror CI is now green: the SDK source is `ruff format`-clean (formatting
  was previously unenforced on `sdk/`), the secret-scan uses `detect-secrets-hook`
  (the old step's `git diff --exit-code` always failed on detect-secrets' volatile
  `generated_at` timestamp), and the broken typecheck step (`pyright`/`mypy`, neither
  shipped) was removed.

### Changed

- Pin `ruff==0.15.6` in the `dev` extra and enforce `ruff format` on the SDK tree so
  local formatting never drifts from the mirror CI.

## [1.1.1b3] — 2026-07-08

### Docs

- Remove references to the not-yet-published TypeScript / Go SDKs — Python is
  the only SDK live today; the multi-language framing returns when they ship.

## [1.1.1b2] — 2026-07-08

### Docs

- Correct the README licence line to **Apache-2.0** (matches `LICENSE` +
  the `[project] license` field; the `1.1.1b1` README still said MIT).

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
  polling-only for now (use `wait()`).
- **Author-SDK / developer-portal tokens (`cvl_` / `cvi_`) are now rejected**
  by `APIKey` / `Convilyn(api_key=...)` with a precise `AuthError`, instead of
  being treated as acceptable consumer keys (they never authenticated against
  the data plane — the backend answered with an opaque 401). The `ck_` prefix
  and any unknown prefix are still accepted (forward-compat).

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
pre-1.0 development cycle; the bump from `0.1.0` reflects API readiness, not a
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
  resources.
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
