# Stability & versioning policy

`convilyn` follows [Semantic Versioning](https://semver.org/). This page
defines exactly **what is covered by that promise** so you know what you
can depend on and what may change underneath you.

## The public surface

The public, semver-covered surface of this package is:

1. **Everything reachable as `from convilyn import X`** — i.e. every name
   in `convilyn.__all__`. That is the clients (`Convilyn`,
   `AsyncConvilyn`), the resource method signatures reached through them
   (`client.files`, `client.convert`, `client.goals`, `client.workflows`,
   `client.account`), the response models (`File`, `ConvertJob`,
   `GoalJob`, `Workflow`, `CostEstimate`, …), the exception taxonomy
   (`ConvilynError` and its subclasses), and the resilience config
   (`RetryPolicy`, `ExponentialBackoffRetry`, `NoRetry`,
   `AutoThrottleConfig`, also re-exported from `convilyn.config`).
2. **The `convilyn` CLI** — command names, documented flags, the
   `--json` output shape, and the exit codes (`0` ok, `1` usage,
   `2` API error, `3` job failed, `130` interrupted).
3. **The authentication contract** — a consumer API key (canonical prefix
   `ck_`, minted in the API Console / Settings → API) passed as
   `api_key=` or `CONVILYN_API_KEY`. Author-SDK / developer-portal tokens
   (`cvl_` / `cvi_`) are **rejected** with a precise error — they are not
   consumer keys; any other prefix is accepted (forward-compat).

A test guards this surface: `tests/contract/test_public_surface.py`
freezes `convilyn.__all__`, the resource method sets, and the exception
hierarchy, and asserts that nothing from `convilyn._internal` leaks into
the public namespace. Any deliberate change to the public surface must
update that test **and** the [CHANGELOG](../CHANGELOG.md) in the same
commit.

## What is NOT public

- **`convilyn._internal.*`** — transport (`HTTPClient`), the WebSocket
  transport, the auth strategy implementation, idempotency-key
  generation, and the retry/throttle *implementations*. These have **no
  stability guarantee** and may change or move in any release. Import the
  public config types from `convilyn` / `convilyn.config`, never from
  `convilyn._internal`.
- **Any attribute or method whose name starts with `_`** (e.g.
  `client._async`, `client._http`).
- **Wire-format field aliases** beyond what the typed models expose.

## SemVer in practice

| Change | Version bump |
|---|---|
| Remove/rename a public symbol, remove a CLI command/flag, change an exit code, narrow a method signature, change a response model field type | **major** (`X`) |
| Add a new public symbol, resource method, CLI command/flag, model field, or exception subclass — backward compatible | **minor** (`Y`) |
| Bug fix, doc fix, internal refactor with no public-surface change | **patch** (`Z`) |

Adding a new keyword-only argument with a default, or a new exception
subclass of an existing public base, is a **minor** change: existing
`except APIError:` / call sites keep working.

## Known v1 limitation — goal events are polling-only

`goals.events()` (WebSocket streaming) is part of the public surface but
is **not serviceable in v1**: the platform's WS gateway does not accept
consumer `ck_` keys yet, so a connect attempt raises a `WebSocketError`
that points back at polling. The supported way to follow a goal run is
`client.goals.wait(...)` / `retrieve(...)` (CLI: `convilyn goals
status`). When gateway support lands, `events()` starts working without
an SDK upgrade — this is a platform capability gate, not an API change.

## Deprecation policy

We do not remove public surface without warning. A symbol slated for
removal is first **deprecated for at least one minor release**: it keeps
working, emits a `DeprecationWarning`, and is documented in the
CHANGELOG under `Deprecated`. Removal then happens only in a subsequent
**major** release.

## The documented escape hatch

For endpoints the typed resources don't cover yet, the SDK exposes one
intentional low-level seam — `client._async._http.raw_request(...)`
(and the `convilyn api` CLI command). Because it reaches through a
`_`-prefixed attribute it is **explicitly outside** the semver guarantee:
it is a pragmatic bridge, not a stable API. Prefer a typed resource
method whenever one exists; expect the escape hatch's internals to move.
