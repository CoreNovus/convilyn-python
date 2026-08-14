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
   **1b. `convilyn.local`** — the offline conversion namespace, covered from
   **1.3.0**. That is every name in `convilyn.local.__all__`: the functions
   (`convert`, `convert_many`, `plan`, `capabilities`, `detect_format`, and the
   `a`-prefixed async wrappers), the models (`Route`, `Requirement`,
   `ConversionResult`, `Capabilities`, `ProgressEvent`), and the error taxonomy
   (`LocalError` and its subclasses, all of which also subclass
   `ConvilynError`).

   The `convilyn.local.pdf` sub-namespace is covered on the same terms from
   **1.6.0**: every name in its `__all__` (`select`, `merge`, `rotate`,
   `compress`, `encrypt`, `decrypt`, `burst`, `extract_text`, `page_count`).
   Page operations are separate from `convert` because they change the pages
   and never the format; that split is part of the surface, not an
   implementation detail, and will not be collapsed in a minor release.

   Clause 1b is listed separately from clause 1 because it is reached as
   `convilyn.local`, not through `convilyn.__all__`, so clause 1 does not
   describe it. `tests/contract/test_local_surface.py` freezes it.

   **The names of the optional extras are covered; their contents are not.**
   `convilyn[pdf]` will keep meaning "PDF support" — but which distributions
   provide it may change in a minor release, because that is a packaging
   decision rather than an API. Depend on the extra, never on what it installs.
   `convilyn.local._engine` is likewise **not** public: it is generated from
   Convilyn's server-side engine and regenerated whenever that changes.
2. **The `convilyn` CLI** — command names, documented flags, the
   `--json` output shape, and the exit codes (`0` ok, `1` usage,
   `2` API error, `3` job failed, `130` interrupted). This includes the
   `convilyn local` group (`convert`, `batch`, `formats`, `doctor`, and the
   `pdf` sub-group).
3. **The authentication contract** — a consumer API key (canonical prefix
   `ck_`, minted in the API Console / Settings → API) passed as
   `api_key=` or `CONVILYN_API_KEY`. Author-SDK / developer-portal tokens
   (`cvl_` / `cvi_`) are **rejected** with a precise error — they are not
   consumer keys; any other prefix is accepted (forward-compat).

   **`convilyn.local` is outside this clause entirely.** It reads no
   credential, opens no connection, and consumes no quota. Everything else in
   this package needs a key; that half does not.

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
intentional low-level escape hatch — `client._async._http.raw_request(...)`
(and the `convilyn api` CLI command). Because it reaches through a
`_`-prefixed attribute it is **explicitly outside** the semver guarantee:
it is a pragmatic bridge, not a stable API. Prefer a typed resource
method whenever one exists; expect the escape hatch's internals to move.
