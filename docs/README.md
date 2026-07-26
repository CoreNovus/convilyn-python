# convilyn

Official Python client for the [Convilyn](https://convilyn.corenovus.com) API —
convert files, run AI workflows, and ship in five lines of Python or a
single shell command.

```bash
pip install convilyn
export CONVILYN_API_KEY=ck_...
convilyn convert report.docx --to pdf
```

```python
from convilyn import Convilyn

client = Convilyn()  # reads CONVILYN_API_KEY from env
file = client.files.upload("report.docx")
job = client.convert.create_and_wait(file=file, target_format="pdf")
client.convert.download_to(job, to="report.pdf")
```

## What you get

* **Python SDK** — `Convilyn` (sync) and `AsyncConvilyn` (async) with
  resource-style accessors: `client.files`, `client.convert`,
  `client.goals` (agentic / AI workflows with HITL slot filling),
  `client.workflows` (community marketplace), `client.user_workflows`
  (manage the workflows you authored), `client.builder` (chat-Builder
  sessions), and `client.account` (billing tier + cost-preview).

  > **Goal progress is polling-only in v1.** Follow a run with
  > `client.goals.wait(...)` / `retrieve(...)` (or `convilyn goals
  > status` from the shell). The WebSocket gateway does not accept
  > consumer `ck_` keys yet, so `goals.events()` streaming raises with
  > guidance pointing back at polling until that lands.
* **CLI** — five sub-command groups installed with the package:
  * `convilyn convert <file> --to <format>` — upload, convert, download
  * `convilyn doctor` — environment + connectivity diagnostics
  * `convilyn api <METHOD> <PATH>` — gh-style escape hatch for any API endpoint
  * `convilyn goals {start,status,events,fill-slot,confirm,cancel,retry}`
    — drive AI workflows from the shell (NDJSON streaming, HITL,
    pinned exit codes)
  * `convilyn account {plan,quota}` — pre-flight your billing tier
    and workflow cost before running
* **Free to install, metered to use** — `pip install convilyn` is free.
  API calls draw on your monthly quota (a generous free tier is
  available; Pro lifts the cap). The SDK raises typed
  `PlanRequiredError` / `QuotaExceededError` (both subclass `APIError`)
  when an action exceeds your tier — see
  [QUICKSTART §8](./QUICKSTART.md#8-check-your-plan--quota-before-running-clientaccount).
* **Resilient by default** — retry on 5xx / 429 / 408
  with exponential backoff + jitter, `Idempotency-Key` auto-stamped on
  mutating verbs, `Retry-After` honoured.
* **AI-agent friendly** — every command supports `--json` for
  machine-readable output, `--dry-run` for safe previews, and pinned
  exit codes (0 / 1 / 2 / 3 / 130) so agent loops can branch on the
  result without parsing free-text.

## Companion package — building your own workflows

`convilyn` is the **consumer** SDK (you call the API). If you want to
*build* a tool server or author a workflow spec for the Convilyn
platform, install the **author** SDK:

```bash
pip install convilyn-author          # separate package
convilyn-author init my-server
```

The two packages are intentionally separate so consumers don't pay
the uvicorn / FastAPI dependency cost. See
[`convilyn-author`](https://github.com/CoreNovus/convilyn-author-python)
for its source and docs.

## Next

* [QUICKSTART.md](./QUICKSTART.md) — 5-minute new-user guide (covers
  convert, goals, workflows, and account / quota chapters)
* [STABILITY.md](./STABILITY.md) — what the public API is and the
  SemVer / deprecation promise behind it
* [CHANGELOG.md](../CHANGELOG.md) — version history
* [examples/](../examples/) — runnable Python + shell scripts
* [AGENT.md](../AGENT.md) — guidance for AI coding agents
  contributing to this SDK

## Licence

Apache-2.0. See [`LICENSE`](../LICENSE).
