# AGENT.md — guidance for AI coding agents

This file is for AI coding agents (Claude Code, Cursor, Cline,
Aider, Windsurf, GitHub Copilot Workspace) that open this directory
and want to extend the Convilyn client SDK without breaking it. Read
this first; it documents the invariants that the test suite will
defend.

## Where things live

```
sdk/consumer-python/       # PyPI: convilyn (was sdk-client/ before R5, sdk-consumer/ before the sdk/ monorepo layout)
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
│   ├── cli/                # one file per sub-command (convert, doctor, api, goals, rag, ...)
│   ├── rag/                # retrieval toolkit — see "Agent tool namespaces"
│   ├── providers/          # connectors to model services — same section
│   ├── graphs/             # graphs for a LangGraph host — same section
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

## The offline `local` namespace

`convilyn.local` converts files on the user's machine. It has no client, no
transport, and no credential, so several conventions above do **not** apply to
it. Read this before "fixing" any of them.

**It is synchronous, and that is deliberate.** The rest of this SDK is
async-primary because it is IO-bound over HTTP — an `await` there lets the loop
run something else while the network answers. Conversion is CPU- and
subprocess-bound, which inverts the argument: an `async def` running pdfplumber
inline would satisfy the letter of the convention while blocking the loop for
seconds. `aconvert` / `aconvert_many` are thin `asyncio.to_thread` wrappers, and
they require the synchronous implementation to be the real one.
`tests/unit/local/test_api.py::TestAsyncWrappers` pins both halves.

**Layering: a leading underscore means internal, and there are no exceptions.**
Public are `__init__`, `types`, `errors`, `api`. Internal are `_probe` (is this
package importable / where is this executable), `_tools` (*what* the external
programs are and where they live), `_run` (*how* to invoke them), `_routes` (the
route table and the availability join), and `_engine` (generated). A reader
should never have to check a denylist to know which side a module is on — if you
add a module, its name states the answer.

`_tools` and `_run` are separate on purpose. "Do I have LibreOffice" is asked
whenever somebody lists what this machine can convert; "run LibreOffice" happens
only once a conversion starts. The split lets `_routes` answer the first without
being able to spawn a subprocess, and lets the generated bridge invoke the
second without importing the discovery tables.

**`_engine/` is GENERATED. Never edit it.** It is projected from Convilyn's
server-side conversion engine by `scripts/oss/project_local_engine.py` and
regenerated whenever that engine changes; a hand edit is silently lost at the
next regeneration. Every file carries a DO-NOT-EDIT header, a drift gate in
`scripts/ci/sdk_local_ci.py` compares the tree against a fresh projection, and
`tests/packaging/test_offline_engine_packaging.py` asserts the headers. To
change conversion behaviour, change it upstream and re-project.

**No optional dependency may be imported at module scope**, anywhere reachable
from `convilyn/__init__.py` or `cli/main.py`. The release pipeline installs the
wheel with **no extras** and runs `convilyn --version`; an eager parser import
would break that on a user's machine rather than here. Extractors are imported
lazily inside their registry entry, and the property is held by a packaging
test rather than by memory.

**Availability is data, not an exception.** `capabilities()` and `plan()` never
raise — a missing dependency is a fact about the machine, and the caller asking
is the one who has not decided what to do about it yet. Each `Route` carries
`unavailable_reason`, a full sentence naming what is missing and how to get it,
authored once in `local/routes.py`; the error classes quote it rather than
composing their own.

**Probes are injectable, and tests must inject them.** This suite runs with the
extras installed and, on some machines, with LibreOffice installed — so the
"missing dependency" arm that users on a fresh install actually meet would
otherwise never execute. Inject `probe=` (packages) and `find=` (external
tools). Note that injecting the lower-level `which` is *not* enough: the lookup
falls through to the platform's install locations and finds the real one.

## Agent tool namespaces (`rag`, and the ones that follow)

`convilyn.rag` is a retrieval toolkit and the first of several agent tools.
Every one of them is built to the shape in `docs/TOOLS.md`, which is the
user-facing statement of the rules below; a new tool namespace follows it, and
a change to a rule is made there and here together. Two rules decide every
design question; apply them before adding anything.

**Generality: the caller decides how it is used.** Every stage is a function
usable on its own (`rag.split`, `rag.normalize`), and a convenience object may
only compose stages that are also reachable separately. A function the caller
supplies (a length measure today) is an argument to the stage that uses it and
ranks equal to the built-in. Sizes, depths and switches are options, never
constants.

**Consistency: one way to do each thing.** Data lives in `recipe` as frozen
models and is serializable; functions are arguments and never stored in a
recipe. Pure computation is a synchronous function. There is one text fold,
`normalize`, and its stamp names how it was applied — do not fold text anywhere
else.

A stage gains a pluggable seam only when two real implementations ship or the
piece can only come from the caller. The layering rule is the same as `local`:
a leading underscore means internal, and `tests/contract/test_rag_surface.py`
holds it.

**Every default has a recorded basis.** A default value is `measured`,
`published` or `derived`, and `docs/TOOLS.md` lists which. Do not add or change
a default without stating its basis there; a number you cannot source is
`derived` at best, and is said to be. A tool namespace is *provisional* (see
`docs/STABILITY.md` clause 1c) until its defaults are measured: names and
signatures are frozen from the first release, default values may move in a
minor with a `Changed` entry.

**A store keeps and finds; it does not decide.** `Store`
(`rag/_stores/protocol.py`) is the whole interface. Cutting, the text fold and
ranking across kinds of search stay in the SDK, which is what makes a recipe
portable between stores. A new store is one module in `rag/_stores/` plus a row
in `_FACTORIES`, and its test module subclasses `StoreContract`
(`tests/unit/rag/store_contract.py`) — do not write a store's tests from
scratch, and do not add a method to `Store` that only one store can honour.
Anything a store does to text beyond the shared fold (the SQLite store's
two-character pieces) is that store's own and is said in `describe()`.

Every store answers `lexical` and `dense`; scores are comparable within one
search only, and combining the two kinds is `rag.fuse`'s job, never a store's.
A store compares the vector size it is given with `IndexIdentity.dimensions`
and refuses a mismatch with `ValueError` before touching its engine. numpy is
the `rag` extra and is imported inside the vector search only, so `import
convilyn` and keyword search keep working without it
(`tests/packaging/test_offline_engine_packaging.py` holds the first).

`rag/_config.py` reads the settings file. It is the one place a file becomes
connectors, so the rules live there: a setting named like a credential is
refused in any table or array (names are compared as lowercase words, so
`apiKey` is `api_key`); every setting is typed, `[store]` against the store's
own signature; error messages name the setting and never repeat a value
(errors are raised outside the handler of the parse, decode or validation
error, which holds the file's text, so nothing chains on); a provider name is
checked before the connector is built so its refusal never echoes it; and
connectors built from the file are closed by the corpus while those passed in
are not. A message the file's values can reach (a `Recipe` validator, say)
must not quote them either.

`rag/bench/_demo/` is shipped data: original text about a bookshop that does
not exist, and its question file. Keep it free of third-party text, real
names and anything the shipped-file vocabulary check refuses; a change to a
document needs its questions' excerpts checked (`tests/unit/cli/test_rag.py`
loads every question and holds a recall floor). `cli/rag.py` imports nothing
from `convilyn.rag` at module scope, so `convilyn --version` stays light.

`rag/bench/_pin.py` decides what makes two runs comparable: the question
set (digest, line order ignored), the documents (`SourceInfo.digest`, which
does not change when only the recipe does), `k` and `cover`. Anything else,
settings included, is what a check judges. A check that cannot compare
returns `not_comparable` (exit 4) and never passes; its reasons give counts,
never a question or document.

`rag/bench/_compare.py`'s `compare_variants` keeps the family's confidence:
each of `m` comparisons is made at `1 - (1 - confidence) / m`
(Bonferroni). Do not compare variants with each other or report an
unadjusted interval beside them, or the 95% no longer holds.
`rag/bench/_estimate.py` reads settings only (`_config.read`, and
`_config.check_provider` so a refusal never echoes the provider named): it
must never open a store or build a connector, which a test enforces by
failing on any socket.

`rag/bench/` measures retrieval. It calls only the `retrieve` it is given, one
question at a time, so the latency it reports is the retriever's. Matching
(`_score.grams`, `covers`) and the metrics (`_score.scores`) are pure
functions tested on hand-computed cases; change a definition only with its
TOOLS.md entry and those cases. A bound that cannot be measured fails: never
let a check pass on an empty set. Question-file errors name the line and
field and never quote the line.

Store code handles untrusted text twice over: documents and queries. SQL is
parameterised, a query is rewritten into quoted terms so nothing in it is read
as the engine's query syntax, and errors are fixed strings with the engine's
own error kept as `__cause__` only.

`_corpus` reuses `convilyn._internal.loop_runner.LoopRunner` for its
synchronous form, the same way `sync_client` does, and `providers` reuses
`convilyn._internal.resilience` for its retry policy. Those are the `_internal`
imports the tool namespaces make; prefer reusing what is there to writing a
second copy.

**A provider is a row of data.** `convilyn.providers` has few wire formats
(`providers/_wires/`, one module each) and many providers
(`providers/_profiles.py`, one row each). Never branch on a provider's name or
a format's: if two providers differ, the difference is a field of the row, and
a connector reaches a format only through the tables in `_wires/__init__.py`
(`EMBED_WIRES`, `RERANK_WIRES`), whose entries all have the shape in
`_wires/shapes.py`. A new format is a module and a line in a table; it touches
no connector. A model that needs different treatment from its siblings (its
own token limit, its own way of being told a document from a query) is a
prefix in a field of the row, not a test on the model's name. Every row states
the documentation its limits were read from and the date, and
`tests/unit/providers/test_profiles.py` fails a row that does not — read the
provider's own documentation before writing a number, and when it publishes
none, say so in the row rather than guessing. Limits are defaults the caller
can override, and an unknown model is sent as is.

Every call runs the fixed sequence in `providers/_steps.py`. The synchronous
and asynchronous form of a connector share all of it and differ only in how
they wait; keep it that way, so a fix lands in both. The address, the key and
the sending are `providers/_transport.py`, used by every connector: a rule
about where text may go or how the key is handled is written there once, never
in an embedder or a reranker. A reranker asks for a score for every document it
sends and refuses an answer that leaves one out, so that the answers of several
requests can be put in one order without a document going missing quietly.

`convilyn.providers` is a trust boundary: the caller's text goes to the service
the caller named, under the caller's key. Nothing may widen that. The key is
never put in an exception, a `repr`, a log or an address (a format whose
service also accepts the key as a query parameter still sends it in a header),
and nothing a caller passes becomes part of an address unchecked (the Gemini
format puts the model in the path, so it holds the model to a name's
characters); an error message is a fixed
sentence and never carries the response body, the request text or the URL;
plain `http` is refused except for `localhost`, where a client made here
ignores environment proxies; a redirect is never followed, whatever the
caller's client is set to; a key the HTTP layer would reject is refused first,
because that layer names the value it rejects. The provider's answer is
untrusted input: anything it can contain surfaces as `ProviderError`.
`tests/unit/providers/test_trust_boundary.py` and `TestNothingLeaks` in
`test_embedder.py` and `test_reranker.py` hold these. respx does not exercise the HTTP layer's own
header checks, so a claim about what that layer does needs its own evidence.

**A size travels with its ruler.** Passage sizes are an amount of content, so
`SplitOptions` names the estimate they are read with (`tokenizer`, `language`)
as data, and `rag.preset()` builds both together from the one table in
`_measure` and `_languages`. A new language is a row there, graded `derived`
unless retrieval was measured on it, plus its row in `docs/TOOLS.md`; do not
add a language whose estimate has not been checked against real tokenizers.

**A corpus calls models by shape, never by import.** `rag._models` defines
`Embedder` and `Reranker` as structural protocols; `_corpus` must not import
`convilyn.providers` (which already imports `convilyn.rag`, so the reverse
would be a cycle), and a caller's own object ranks equal to a connector. A
failed query embedding or rerank falls back and is named in
`SearchResult.skipped`; a failure while adding a document is raised. Embedding
is skipped for a passage whose digest already has a stored vector
(`Store.vectors`), so keep `Passage.digest` the hash of the text alone.

**Ranking steps take hits, not a store.** `rag.fuse` and `rag.merge_adjacent`
(`_rank`) work on any sequence of `Hit`, so a ranking from outside this SDK is
as good as one from a store; keep them free of store and network calls. A
passage is the same passage across rankings when its `passage_id` is the same.
A new fusion method is a value of `FuseOptions.method` with a measured or
published basis in `docs/TOOLS.md`, not a callback field: a function cannot be
written to a recipe file.

**No default path loads a model.** Tokens are estimated from measured
characters-per-token tables (`_measure`), never by downloading a tokenizer; an
exact count is the caller's `measure`. The same holds for any later stage that
needs an embedding or a language model: it is the caller's key or the caller's
function.

**Input is untrusted.** Markdown reaches `split` from wherever the caller got
it. Patterns in `_markdown` may not backtrack, the cost of every stage stays
proportional to its input, and `tests/unit/rag/test_split_hostile_input.py`
holds both by counting work rather than timing it.

`_markdown` finds block boundaries and keeps source lines verbatim; it is a
scanner, not a renderer, which is why no Markdown library sits behind it.

**A graph is a row, and it brings nothing of its own.** `convilyn.graphs` ships
compiled graphs for a host application built on LangGraph. Adding one is four
things: a module `graphs/_<name>.py` exporting the function that builds it and
an `INFO`, a row in `graphs/_registry.py`, a test module that subclasses
`GraphContract` (`tests/unit/graphs/graph_contract.py`) and supplies only a
graph, an input and the expected output (plus a `Pause` when the graph can
pause for an answer, so the contract resumes it in every host shape), and its
section in `docs/TOOLS.md`.
Do not write a graph's hosting tests from scratch: the contract is the
definition of "does not disturb the host".

The rules the contract holds, which a new graph must not need an exception to:
compile with no saved-state backend, no store and no context schema; take every
dependency (model call, search, check) as a keyword argument, and accept a
plain function wherever a model is not required; declare an input schema and an
output schema; keep state to plain data; prefix keys the graph keeps for itself
with `cv_<name>_`. Write each step once as a coroutine `(state, call)` that
calls the caller's functions only as `await call(function, *arguments)`, and
wrap it with `graphs/_nodes.py::node`: it passes a `call` that never waits
under `invoke` and one that awaits under `ainvoke`, which is what lets one graph
run both ways without keeping a mode anywhere a caller's function could see. `as_node` and `as_tool` (`graphs/_adapters.py`) take any
compiled graph and are written once; a graph never gets its own.

The framework is imported only under `graphs/` and only by the modules with a
leading underscore; `types` and `errors` stay plain. It is the `graphs` extra,
outside `all`, and `tests/contract/test_graphs_surface.py` proves the package
and CLI import without it.

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
