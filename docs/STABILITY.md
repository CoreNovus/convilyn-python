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
| **Refuse an input an existing method used to accept**, where the old behaviour risked destroying the caller's data | **minor** (`Y`), with a `Changed` entry that shows the migration |
| Bug fix, doc fix, internal refactor with no public-surface change | **patch** (`Z`) |

Adding a new keyword-only argument with a default, or a new exception
subclass of an existing public base, is a **minor** change: existing
`except APIError:` / call sites keep working.

**The third row is narrow, and it exists because the second one did not cover a
real case.** `download_to()` gained `overwrite: bool = False` — by the letter of
the sentence above, a defaulted keyword-only argument, therefore minor. But that
sentence earns "minor" with *existing call sites keep working*, and here they
demonstrably do not: a script that re-downloaded over its previous result
succeeded before and raises now. Read as "signature unchanged, so minor", the
rule would have shipped a silent break under a patch.

It is **not** major either, and the distinction is what the row records: what was
removed is a behaviour that destroyed the caller's file without asking, which this
package's own documentation already called wrong for the offline half. Charging a
major version to stop doing that would price the fix out of ever landing.

The bar is deliberately high — *the old behaviour risked destroying the caller's
data*. A method that merely became stricter about, say, an argument's format does
not qualify; that is a narrowed signature, and it is major. And a change under this
row is never quiet: it goes in `Changed`, never `Added`, and the entry has to show
the one-line migration.

## Removed in 3.0.0 — the WebSocket event stream

`goals.events()`, the `convilyn goals events` CLI command, `GoalEvent`,
`WebSocketError` and the `ws_url` / `ws_transport_factory` constructor
arguments are **gone**.

They never worked. The platform's WS gateway authenticates developer-portal
keys, a JWT, or an anonymous cookie — and this SDK rejects developer-portal
keys at construction and issues no JWT, so no credential it can hold was ever
accepted. Every test passed because the transport was mocked.

**This section used to say the opposite:** *"When gateway support lands,
`events()` starts working without an SDK upgrade — this is a platform
capability gate, not an API change."* That was wrong. The gateway's authorizer
takes its identity from `route.request.querystring.token`, and it must, because
the browser client shares it and a browser cannot set headers on a WebSocket
handshake. So "gateway support" would have meant putting a long-lived,
non-self-revocable API key in a URL query string — permanently, for every
streaming call.

Follow a run with `client.goals.wait(...)` / `retrieve(...)` (CLI:
`convilyn goals status`), which authenticate over HTTPS with an `Authorization`
header.

If streaming returns, it will be through a short-lived, single-use connect
ticket — a design that shares no code with what was removed, which is the other
reason keeping this was not "free optionality".

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
