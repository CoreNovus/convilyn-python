# AGENT.md — guidance for AI coding agents

This file is for AI coding agents (Claude Code, Cursor, Cline,
Aider, Windsurf, GitHub Copilot Workspace) that open this directory
and want to extend the Convilyn client SDK without breaking it. Read
this first; it documents the invariants that the test suite will
defend.

## Where things live

```
sdk-consumer/              # PyPI: convilyn (was sdk-client/ before R5)
├── pyproject.toml          # one source of truth for deps + scripts
├── docs/
│   ├── README.md           # PyPI landing page
│   └── QUICKSTART.md       # 5-min new-user guide
├── examples/               # runnable Python + shell examples
├── src/convilyn/
│   ├── __init__.py         # public surface — only re-exports
│   ├── client.py           # AsyncConvilyn root client
│   ├── sync_client.py      # Convilyn sync facade
│   ├── exceptions.py       # ConvilynError hierarchy (frozen contract)
│   ├── types.py            # File / ConvertJob / ResultFile / JobError
│   ├── resources/          # one file per resource (files, convert, ...)
│   ├── cli/                # one file per sub-command (convert, doctor, api, goals)
│   ├── _internal/          # NOT public — http, auth, resilience
│   └── _version.py
└── tests/                  # one test file per module; respx for HTTP
```

## Public surface contract

Anything reachable as `from convilyn import X` is public and follows
semver. Treat `convilyn._internal.*` as private — agents can read it,
but should not import from it in new resources or examples. The
recipe for "I need transport behaviour I can't get from the public
API" is to add a Protocol seam in `_internal/`, not to reach across
the boundary in a resource.

## SOLID seams already in place

These are the extension points. Use them; do not duplicate them.

| Seam | Purpose | Where |
|---|---|---|
| `AuthStrategy` Protocol | Plug a credential carrier other than API key | `_internal/auth.py` |
| `AuthStrategy.bearer_token()` | Surface the raw token for non-HTTP transports (WebSocket subprotocol, etc.) | `_internal/auth.py` |
| `RetryPolicy` Protocol | Replace retry cadence (linear, decorrelated jitter, etc.) | `_internal/resilience.py` |
| `OutputRenderer` Protocol | Add new CLI output formats (YAML, table, …) | `cli/_output.py` |
| `_build_client` factory | Inject a mocked `Convilyn` in CLI tests | `cli/convert.py`, `cli/goals.py` |
| `HTTPClient.raw_request` | Issue an HTTP request without `>=400` raising | `_internal/http.py` |
| `_do_request_with_retry` | Shared retry loop — do not re-implement | `_internal/http.py` |
| `WSTransport` Protocol | Replace the WebSocket transport (fakes for tests, alternate libs) | `_internal/ws.py` |
| `ws_transport_factory` ctor arg | Inject a `WSTransport` factory at client construction — `AsyncConvilyn(ws_transport_factory=...)` | `client.py`, `resources/goals.py` |
| `goals.events` async generator | Stream goal-lane events; per-call `ws_url` override; `is_terminal` self-closes the iterator | `resources/goals.py` |

## Adding a new resource (e.g. `client.goals`)

1. Create `src/convilyn/resources/goals.py` with `AsyncGoals` (the
   async-primary class) and `Goals` (the sync wrapper) — follow the
   shape of `files.py` and `convert.py`. Inject `HTTPClient` via the
   constructor; never import `httpx` directly.
2. Add `client.goals = AsyncGoals(self._http)` in
   `client.py::AsyncConvilyn.__init__` and the matching line in
   `sync_client.py::Convilyn.__init__`.
3. Re-export new models / exceptions from `convilyn/__init__.py`.
4. Add a 4-category test file in `tests/test_goals.py` (logic /
   boundary / error / object-state) using respx for HTTP mocks.
5. Document the new resource in `docs/QUICKSTART.md` and add an
   `examples/0X_*.py` runnable snippet.

## Adding a new CLI sub-command

1. Create `src/convilyn/cli/<name>.py` with a Click command function
   and dedicated helpers (parsers, renderers).
2. Register with `cli.add_command(<name>_command, name="<name>")` in
   `cli/main.py`.
3. Reuse `_build_client` for any SDK call so tests can mock it.
4. Reuse `make_renderer` from `cli/_output.py` for `--json` / human
   output — do not print JSON inline in your command.
5. Add a 4-category test file in `tests/test_cli_<name>.py` with
   `CliRunner` + respx.

## Testing rules

* **No real backend calls in unit tests.** Use respx to mock HTTP and
  `tmp_path` for filesystem.
* **Four categories per behaviour**: logic, boundary, error,
  object-state. The unit-testing skill documents this in detail; the
  existing test files are good models.
* **Assert on observable behaviour**, not internal calls — assert
  which HTTP routes were hit, what exit code came back, what fields
  the response carried. The orchestration can evolve without test
  churn.
* **Click 8.3+ removed `mix_stderr`** — do not pass it to
  `runner.invoke`.

## Conventions

* `from __future__ import annotations` at the top of every module so
  forward references just work.
* Pydantic v2 with `populate_by_name=True` so SDK fields use Python
  snake_case while the wire stays camelCase.
* Frozen Pydantic models for public response types (`File`,
  `ConvertJob`, `ResultFile`, `JobError`).
* Behaviour on the resource, data on the model — the OpenAI /
  Stripe convention. Do not put HTTP calls on the data models.

## Forbidden / discouraged patterns

* Importing from `convilyn._internal` outside `convilyn._internal` and
  `cli/` (which is intentionally tight-coupled to the SDK's own
  transport).
* Adding a hidden retry loop inside a resource — use the one in
  `_do_request_with_retry`.
* Calling `time.sleep` anywhere in async code paths (use
  `await asyncio.sleep`).
* Bypassing `_build_client` in CLI sub-commands.
* Hand-rolling `Authorization` header — `AuthStrategy.headers()` owns
  that.
* Changing public field names (`filename`, `size`, `content_type`)
  without a major version bump.

## When you're stuck

* Read the relevant test file — the test names spell out the contract
  for the module.
* `convilyn doctor` for environment / connectivity issues.
* `convilyn api <METHOD> <PATH> --json` to inspect a backend endpoint
  the SDK has not wrapped yet.
* Open an issue with a minimal repro and the output of `convilyn
  doctor`.
