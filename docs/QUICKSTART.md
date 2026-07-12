# Convilyn SDK — Quickstart

This guide takes you from `pip install` to a converted file in **under
five minutes**. By the end you'll have run the same workflow from both
Python and the shell.

## 1. Install

```bash
pip install convilyn
```

A single install gives you the Python library *and* the `convilyn`
binary — no separate package, no extras needed.

> Requires Python 3.10 or later.

## 2. Get an API key

Sign up at <https://convilyn.corenovus.com>, then mint your `ck_…` API key from
**Settings → API** (the API Console) at
<https://convilyn.corenovus.com/en/settings/api> — the page is auth-gated and is
also where you manage billing, usage, and quota. Export it once so the
SDK and CLI both pick it up:

```bash
export CONVILYN_API_KEY=ck_...
```

> The CLI also accepts an explicit `--api-key` flag, but the
> environment variable is the recommended default — it works for both
> Python scripts and one-off shell commands.

## 3. Verify your setup

Run the doctor command first to catch any environment issues before
hitting the API:

```bash
$ convilyn doctor
✓ [OK] Python: 3.11.9
✓ [OK] convilyn SDK: 1.1.1b1
✓ [OK] httpx: 0.28.1
✓ [OK] pydantic: 2.13.4
✓ [OK] click: 8.3.1
✓ [OK] CONVILYN_API_KEY: ck_xx…XXXX
✓ [OK] CONVILYN_BASE_URL: https://api.convilyn.corenovus.com (default)
All checks passed.
```

> Add `--ping` to also probe the backend's `/api/v1/health` endpoint:
> `convilyn doctor --ping`.

## 4. Convert a file (Python)

The five-line hello-world:

```python
from convilyn import Convilyn

client = Convilyn()
file = client.files.upload("report.docx")
job = client.convert.create_and_wait(file=file, target_format="pdf")
client.convert.download_to(job, to="report.pdf")
```

What just happened:

1. `files.upload` got a storage URL from the API, streamed the
   file to storage, and registered the upload with the backend.
2. `convert.create_and_wait` started a `document_conversion` job and
   polled until it finished (or failed).
3. `convert.download_to` fetched the presigned download URL from the
   completed job and wrote the bytes to disk.

If any step fails (auth, transport, conversion error) you get a typed
exception: `AuthError`, `APIError`, `RetryExhaustedError`,
`JobFailedError`, `JobTimeoutError`. Catch the base `ConvilynError`
to handle them all uniformly.

## 5. Convert a file (CLI)

The same workflow as a single command:

```bash
$ convilyn convert report.docx --to pdf
↑ Uploading report.docx (32.4 KiB)
▶ Creating conversion → pdf
… Converting… 100%
↓ Downloaded report.pdf
✓ report.pdf (28.1 KiB)
```

Pipe-friendly mode for shell pipelines and AI agents:

```bash
$ convilyn convert report.docx --to pdf --json | jq .
{
  "command": "convert",
  "file_id": "file_abc",
  "job_id": "job_xyz",
  "status": "completed",
  "output_path": "report.pdf",
  "output_size_bytes": 28798,
  "elapsed_seconds": 3.4
}
```

Safe preview before spending an API call:

```bash
$ convilyn convert report.docx --to pdf --dry-run
↑ [dry-run] Would upload: report.docx (32400 B, application/vnd.openxmlformats-officedocument.wordprocessingml.document)
▶ [dry-run] Would POST /api/v1/jobs: {processorType=document_conversion, ...}
↓ [dry-run] Would download to: report.pdf
[dry-run] No API calls made.
```

## 6. Call any endpoint (escape hatch)

The CLI ships a gh-style `api` sub-command for endpoints the SDK has
not wrapped yet. Same auth, retry, and `Idempotency-Key` behaviour as
the high-level commands.

```bash
# Inspect a job:
$ convilyn api GET /api/v1/jobs/job_xyz --json | jq .

# Hit an arbitrary endpoint:
$ convilyn api POST /api/v1/some/new/endpoint --data '{"x": 1}'

# Pipe a body in from a file or stdin:
$ convilyn api POST /api/v1/echo --input body.json
$ echo '{"x": 1}' | convilyn api POST /api/v1/echo --input -
```

> Pair with `--include` to see status line + headers (curl -i style),
> or `-o file` to write the body to disk silently.

## 7. AI workflows (`client.goals`)

The AI workflow runs **agentic** workflows: the backend assembles a
multi-step plan, calls MCP tools, and may stop to ask the user for
clarification mid-flight. Surface mirrors the conversion API but adds
HITL (`fill_slot` / `confirm`) and a WebSocket event stream.

### 7.1 Five-line hello-world

```python
from convilyn import Convilyn

client = Convilyn()
job = client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])
print(job.status, job.progress)
```

`run()` is shorthand for `start()` → `wait()`. It returns when the job
reaches a terminal status **or** stops for HITL (`slots_pending`).

### 7.2 Human-in-the-loop (slot filling)

When the agent needs more information, `wait()` returns with
`job.needs_input == True` and one or more `PendingSlot` entries:

```python
job = client.goals.run(workflow_id="doc_analyzer", files=["file_abc"])
while job.needs_input:
    slot = job.pending_slots[0]
    answer = input(f"{slot.question}: ")
    job = client.goals.fill_slot(job.job_spec_id, slot_id=slot.slot_id, value=answer)
    job = client.goals.confirm(job.job_spec_id)
    job = client.goals.wait(job.job_spec_id)
print("final status:", job.status)
```

`confirm()` submits the filled slots for execution; `wait()` then
polls until the next stopping condition.

### 7.3 WebSocket event stream (async-only)

> **Not available in v1.** The event-stream gateway does not accept
> consumer (`ck_`) keys yet, so `goals.events()` raises `WebSocketError`
> today — use `wait()` / `retrieve()` polling (shown above). The example
> below is the API that will work once gateway support lands; see
> `STABILITY.md`.

For live progress instead of polling, subscribe to the events stream.
Streaming is async-only — there is no sync iterator:

```python
import asyncio
from convilyn import AsyncConvilyn

async def main():
    async with AsyncConvilyn(ws_url="wss://ws.convilyn.corenovus.com") as client:
        job = await client.goals.start(workflow_id="doc_analyzer", files=["file_abc"])
        async for ev in client.goals.events(job.job_spec_id):
            print(ev.type, ev.data)
            if ev.is_terminal:
                break

asyncio.run(main())
```

Set the WS URL via the `ws_url=` constructor arg or the
`CONVILYN_WS_URL` environment variable; the SDK raises
`WebSocketError` if neither is configured.

### 7.4 CLI — `convilyn goals`

The same surface from the shell:

```bash
# Dry-run preview (no network)
$ convilyn goals start --workflow-id doc_analyzer --files file_abc --dry-run --json

# Start and capture the id
$ JOB_ID=$(convilyn goals start --workflow-id doc_analyzer --files file_abc --json \
  | jq -r '.job_spec_id')

# Stream events as NDJSON (one JSON event per line, pipe-friendly)
$ convilyn goals events "$JOB_ID" --json | jq -c

# Answer a slot the agent is waiting on
$ convilyn goals fill-slot "$JOB_ID" --slot-id topic --value '"AI safety"'

# Snapshot or poll
$ convilyn goals status "$JOB_ID" --json
$ convilyn goals status "$JOB_ID" --watch --timeout 600

# Lifecycle
$ convilyn goals confirm "$JOB_ID"
$ convilyn goals cancel  "$JOB_ID"
$ convilyn goals retry   "$JOB_ID" --rerun-mode fresh_rerun
```

### 7.5 Known limitations

* **Worker time cap.** Very long-running continuations are not yet
  supported; a job that exceeds the worker timeout surfaces as
  `failed`. Re-run with
  `convilyn goals retry --rerun-mode fresh_rerun` if you hit this.
* **No auto-reconnect.** A WebSocket drop raises `WebSocketError`;
  the SDK does not silently reconnect because the backend does not
  replay missed events. Inspect `client.goals.retrieve(...)` and
  re-subscribe if you want to resume.
* **Sync streaming is intentionally absent.** `Convilyn` (sync)
  exposes every other AI workflow method; `events()` lives only on
  `AsyncConvilyn`. Use `AsyncConvilyn` directly when you
  need streaming.
* **Subscribe-then-start ordering.** If you subscribe before the
  server has created the job, the WS may close immediately. Always
  call `start()` first, then `events()`.

## 8. Check your plan + quota before running (`client.account`)

Some Convilyn endpoints (fork a public workflow, publish your own,
run an expensive AI workflow) require Pro tier. The SDK
surfaces the platform's tier + quota model so you can pre-flight a
call without parsing raw HTTP errors.

### 8.1 What tier am I on?

```python
plan = client.account.get_plan()
print(plan.tier)              # "free" | "pro"
```

### 8.2 Will this workflow fit my quota?

```python
estimate = client.account.get_quota(
    tools=["pdf-mcp:extract_text", "openai-mcp:summarise"],
    max_iterations=25,
)
print(estimate.estimated_usd, estimate.quota_check.state)
# 0.0034  "ok"             ← ready to run
# 0.5200  "soft_limit"     ← pro caller is over the soft cap; will get a warning header
# 0.5200  "quota_exceeded" ← free tier; running this would fail with QuotaExceededError
```

`estimate.quota_check.upgrade_url` points at the in-app pricing CTA
when the verdict is anything other than `"ok"`.

### 8.3 Typed errors when a paywall fires

The SDK raises typed `APIError` subclasses on every billing-related
402; you can catch the granularity you care about:

```python
from convilyn import (
    APIError,
    PlanRequiredError,
    QuotaExceededError,
)

try:
    client.workflows.fork(source_spec_id="doc_analyzer")
except PlanRequiredError as exc:
    print(f"upgrade required: {exc.upgrade_url}")
except QuotaExceededError as exc:
    print(f"used {exc.estimated_micro_u}/{exc.threshold_micro_u} micro-U")
except APIError:
    raise  # transient or unknown — let the caller decide
```

`PlanRequiredError` and `QuotaExceededError` both subclass `APIError`,
so existing `except APIError:` blocks keep working — opt into the
typed handler only when you want to distinguish a paywall from a
transient backend failure.

### 8.4 Same thing from the shell

```bash
$ convilyn account plan --json | jq -r .tier
free

$ convilyn account quota --tool pdf-mcp:extract_text --max-iter 25 --json | jq '.state'
"ok"
```

Both sub-commands exit `1` (USAGE) on `PlanRequiredError` or
`QuotaExceededError` because the caller needs to act on billing —
not retry the API. Transport / 5xx errors exit `2` (API_ERROR) as
usual.

### 8.5 Free vs paid — at a glance

| Action | Free tier | Pro tier |
|---|---|---|
| `pip install convilyn` | ✅ free | ✅ free |
| Call any API (with valid `ck_` key) | ✅ free up to monthly cap | ✅ higher cap |
| `client.convert` (file conversion) | ✅ within cap | ✅ within cap |
| `client.goals` (agentic workflow run) | ✅ within cap | ✅ higher cap + soft-limit warning past 100% |
| `client.workflows.fork` (private copy of a public workflow) | ❌ raises `PlanRequiredError` | ✅ |
| `client.workflows.publish` (make your workflow public) | ❌ raises `PlanRequiredError` | ✅ |

## 9. Going further

* **Async API**: use `AsyncConvilyn` if you're inside an event loop;
  every method has an `async` counterpart with the same signature.
  See [`examples/02_async_convert.py`](../examples/02_async_convert.py).
* **Custom retry policy**: pass `retry_policy=` to the constructor to
  replace the default `ExponentialBackoffRetry`. The Protocol is two
  methods: `should_retry()` and `next_delay()`.
* **Bring-your-own transport**: every resource accepts an
  `HTTPClient` via the constructor so you can inject `httpx.MockTransport`
  or a recording transport for tests.
* **Authoring your own workflow / tool server**: install the *author*
  SDK with `pip install convilyn-author` (a separate package — see
  [`sdk/author-python/docs/README.md`](../../author-python/docs/README.md)).
* **Contributing**: read [`../AGENT.md`](../AGENT.md) for the SOLID
  seams (`RetryPolicy`, `OutputRenderer`, `_build_client` factory)
  before extending the SDK.

## 10. Common questions

**Where can I see all the supported formats?**
Backend supports `document_conversion` between DOCX / PDF / PPTX / TXT
/ HTML / Markdown / RTF / ODT. Other processor types
(`image_conversion`, `ocr_processing`, `media_processing`,
`pdf_operations`, image / document compression) ship as additional
SDK methods in subsequent releases.

**What if a job takes more than 5 minutes?**
The default `convert.create_and_wait` timeout is 5 minutes; override
with `timeout=…`. After timeout the job is still alive on the
backend — call `client.convert.retrieve(job.job_id)` to fetch its
current state.

**What about long AI workflows (goals)?** An agentic workflow's
analyze/execute phases can legitimately run for many minutes, so a flat
`timeout` forces a bad trade-off. Use `idle_timeout` instead: raise the
total budget to your hard deadline and bound the time you tolerate
*without progress*:

```python
job = client.goals.wait(job_id, timeout=1800, idle_timeout=120)
```

A job that keeps advancing holds the loop open; a stalled one raises
`GoalJobTimeoutError` with `reason="idle"` after 2 idle minutes. Either
way the job stays alive server-side — `client.goals.retrieve(job_id)`
resumes observation.

**How do I get HTTP traces for debugging?**
Set `CONVILYN_DEBUG=1` to surface full repr / stacktraces from CLI
errors. The SDK uses `httpx` underneath; standard `httpx` debug
hooks (`event_hooks`) work via the underlying `AsyncClient`.

**Where are the API docs?**
Auto-generated reference is on the v1.x roadmap. For now the source
is the single source of truth — every public symbol has a docstring,
and the [`examples/`](../examples/) directory covers the common
flows.
