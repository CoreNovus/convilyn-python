# Convilyn SDK — Quickstart

This guide takes you from install to a converted file in **under
five minutes**. By the end you'll have run the same workflow from both
Python and the shell.

## 1. Install

```bash
uv add convilyn          # or: pip install convilyn
```

A single install gives you the Python library *and* the `convilyn`
binary — no separate package, no extras needed.

> Requires Python 3.10 or later.

### Optional extras — offline conversion

Everything in §2 onward needs an API key. The offline converter (§1b) does not,
and it needs one optional dependency per file format. Install only what you use:

| I want to read… | Install | Also pulls in |
|---|---|---|
| PDF | `uv add "convilyn[pdf]"` | pdfminer.six, cryptography — the heaviest leaf |
| Word (`.docx`) | `uv add "convilyn[docx]"` | lxml |
| PowerPoint (`.pptx`) | `uv add "convilyn[pptx]"` | lxml **and Pillow** |
| Excel (`.xlsx`) | `uv add "convilyn[xlsx]"` | — |
| XML | `uv add "convilyn[xml]"` | — |
| Images — convert between them, and better image handling everywhere | `uv add "convilyn[images]"` | Pillow |
| All of the above | `uv add "convilyn[all]"` | |

**Plain text, CSV and Markdown rendering need nothing at all** — they work on
the bare install above.

Three format families need a program rather than a package: legacy and
OpenDocument office files (`.doc`, `.odt`, `.rtf`, `.xls`, `.ods`, `.ppt`,
`.odp`) go through **LibreOffice**, ebooks (`.epub`, `.mobi`, `.azw3`) through
**Calibre**, and video and audio through **FFmpeg**. None can come from PyPI, so
none is an extra. Install one and its formats become available with no further
configuration — `convilyn local doctor` reports which are present and where to
get the rest.

Those ten are converted once into the modern sibling this engine already reads
(`.odt` → `.docx`, `.ods` → `.xlsx`, and so on) and then read from there, so
they inherit headings, tables and embedded images exactly as the modern formats
do. The result reports the format you gave it, not the sibling's, and says in
its warnings which route it took.

#### Images are measured, not promised

`convilyn[images]` installs Pillow, and what Pillow can do varies by build and
platform. So the route table is **probed on your machine** rather than copied
from a list: `capabilities()` asks each codec whether it can actually read and
write, which is why it can tell you that this install converts `png` and does
not convert `heic`.

A format that needs a codec Pillow does not bundle still appears, with the
package that would add it:

```console
$ convilyn local formats --from heic
!  heic → unavailable. Reading heic needs pillow-heif, a Pillow plugin this
   package does not install: a HEIF/HEIC codec. Add it with
   `pip install pillow-heif` and the format becomes available with no further
   configuration.
```

Those plugins are named rather than shipped as extras. Each needs a native
library whose wheel coverage is uneven across platforms, and a failed build of
one would otherwise break installing `convilyn[images]` for everybody. A few
formats — `psd`, `pcd` — Pillow reads and never writes; there the reason says
so instead of naming a package that would not help.

Images are refused above **40 megapixels**, checked against the header before
the file is decoded. That is a decompression-bomb guard, not a quality limit.

### Adding and removing

`uv` is recommended for a reason that is not taste: **`pip uninstall convilyn`
does not remove an extra's packages.** That is a pip limitation, and a user who
does not know it concludes the SDK left rubbish behind.

| | add | remove |
|---|---|---|
| uv, as a library | `uv add "convilyn[pdf]"` | `uv remove convilyn` — prunes what it pulled in |
| uv, CLI only | `uv tool install "convilyn[documents]"` | `uv tool uninstall convilyn` — the whole environment goes |
| uv, one-off | `uvx --from "convilyn[pdf]" convilyn local convert a.pdf --to md` | nothing was installed |
| pip | `pip install "convilyn[pdf]"` | `pip uninstall convilyn` leaves the extra's packages behind |

## 1b. Convert a file offline (no API key)

The fastest useful thing this package does, and the only part that needs no
account:

```bash
convilyn local convert report.docx --to md      # → report.md
convilyn local convert photo.png --to webp      # → photo.webp
convilyn local convert clip.mov --to mp4        # → clip.mp4 (needs FFmpeg)
convilyn local batch 'docs/*.pdf' --to md --out-dir build/
convilyn local formats                          # what this machine can do
convilyn local doctor                           # …and how to extend it
```

Name the target either way — `--to` gives the format, `--out` gives the path and
the format is read from its suffix. `--out-dir` writes into a directory and keeps
the source's name, on `convert` as well as `batch`:

```bash
convilyn local convert logo.svg --out logo.png            # png, from the suffix
convilyn local convert report.docx --to md --out-dir build/   # → build/report.md
```

`--out` and `--out-dir` are mutually exclusive: one names a file, the other a
directory, and accepting both would mean silently ignoring one of them.

```python
from convilyn import local

result = local.convert("report.docx", to="md")
print(result.output)  # report.md
print(result.warnings)  # anything the extractor had to guess

local.convert("photo.png", to="jpg")  # images too — see §1b, Images
```

#### Choosing where the output goes: `to=` or `out=`, never both

`to=` names the **format**, and the output lands beside the input. `out=` names
the **path**, and the format is read from its suffix:

```python
local.convert("report.docx", to="md")  # → report.md
local.convert("report.docx", out="build/report.md")  # → build/report.md
local.convert("logo.svg", out="logo.png")  # png, from the suffix
```

The keyword is `out=`, not `output=`, and **exactly one of the two is required** —
passing neither raises rather than defaulting, because guessing what you meant is
how a converter writes over the wrong file:

```python
local.convert("report.docx")
# ValueError: pass `to=` to name the target format, or `out=` to name the file
```

`plan()` takes the same pair, so you can ask what a conversion *would* do before
doing it:

```python
route = local.plan("logo.svg", out="logo.png")
route.target_format  # 'png'
route.available  # False here — and route.unavailable_reason says what to install
```

#### Re-running: `overwrite=`

Conversion refuses to replace an existing output. A script run twice raises the
second time:

```python
local.convert("report.docx", to="md")
local.convert("report.docx", to="md")
# FileExistsError: report.md exists; pass overwrite=True to replace it
```

That is the same position the whole package takes — see §4 for the cloud half —
and the way through is one keyword:

```python
local.convert("report.docx", to="md", overwrite=True)
local.convert_many(sources, to="md", out_dir="build/", overwrite=True)
```

On the command line it is `--overwrite`, on `convert` and `batch` alike.

Nothing here opens a connection, reads `CONVILYN_API_KEY`, or consumes quota.
Structure survives the conversion: headings stay headings, lists stay lists,
tables become GitHub-Flavoured Markdown tables, and embedded images are written
to an `assets/` directory beside the Markdown so their links resolve.

#### When → Markdown is the wrong move

**CSV, XML and plain text are already text, and rendering them as Markdown makes
them bigger.** Measured on a real corpus:

| source | as-is | → Markdown | change |
| --- | ---: | ---: | ---: |
| CSV | 182,588 tokens | 188,335 | **+3.1%** |
| XML | 1,121 tokens | 1,493 | **+33.2%** |

A Markdown table's `|` and `---` are pure overhead when the source had no
container to open. If you are feeding a model, read those files directly.

This conversion earns its keep on formats whose structure is *locked inside a
binary container* — `.docx`, `.pdf`, `.pptx`, `.xlsx` — where getting the text
and the tables out is the whole job. The tool will happily convert a `.csv`
because the route is real; it just costs you tokens rather than saving them.

#### Very large CSVs: `max_rows`

Row-based sources are capped by default, so a million-row export does not become
a million-row Markdown table by accident. The conversion says so in
`result.warnings` when it truncates:

```
truncated: only the first 5000 data rows were converted
```

Raise the cap, or remove it entirely with `0`:

```python
local.convert("export.csv", to="md", max_rows=50_000)
local.convert("export.csv", to="md", max_rows=0)  # the whole file
```

```bash
convilyn local convert export.csv --to md --max-rows 50000
```

The count is **data rows** — the header is not charged to it. Passing it for a
source that has no rows (`max_rows` on a `.docx`) is an error rather than a
silent no-op, so a cap you believed was applied never quietly wasn't.

#### Where images land

Converting one file puts them straight into `assets/`:

```
report.md            ![…](assets/img-0001.png)
assets/img-0001.png
```

A **batch** gives each document its own directory under `assets/`, named after the
source file:

```
build/
  report.md          ![…](assets/report/img-0001.png)
  deck.md            ![…](assets/deck/img-0001.png)
  assets/
    report/img-0001.png
    deck/img-0001.png
```

The extra level is not decoration. Assets are numbered per document, so every
document in a batch has an `img-0001.png`; flat, they would overwrite one another
and leave each Markdown file linking to a picture from whichever document was
converted last. The links are written to match the layout, so a document and its
images stay a valid pair wherever you move them.

### Rearranging PDFs

Page operations are their own namespace, because they are not conversions — a
PDF goes in and a PDF comes out, with the pages rearranged:

```python
from convilyn.local import pdf

pdf.merge(["a.pdf", "b.pdf"], "combined.pdf")
pdf.select("report.pdf", "summary.pdf", pages="1-3,10")
pdf.burst("scan.pdf", "pages/")  # one file per page
pdf.rotate("sideways.pdf", "upright.pdf", degrees=270)
pdf.encrypt("draft.pdf", "sealed.pdf", password="…")
```

```bash
convilyn local pdf merge a.pdf b.pdf -o combined.pdf
convilyn local pdf select report.pdf summary.pdf --pages 1-3,10
convilyn local pdf split scan.pdf --out-dir pages/
convilyn local pdf protect draft.pdf sealed.pdf     # prompts for the password
convilyn local pdf info report.pdf --text
```

Page numbers are 1-based, as printed. `--pages` takes single pages, inclusive
ranges, or both: `3`, `1-5`, `1-3,7,10-12`. Everything needs `convilyn[pdf]`.

`protect` and `unlock` prompt when `--password` is omitted, so the password
stays out of your shell history.

**Ask before you convert.** A missing dependency is reported, never guessed at:

```python
route = local.capabilities().can("odt", "md")
if not route.available:
    print(route.unavailable_reason)
    # Converting odt needs LibreOffice, which was not found on PATH or in the
    # standard install location for this platform. Install LibreOffice from
    # https://www.libreoffice.org/download/ (provides `soffice`). Formats that
    # need no external program: csv, docx, pdf, pptx, txt, xlsx, xml.
```

## 2. Get an API key

Sign up at <https://convilyn.com>, then mint your `ck_…` API key from
**Settings → API** (the API Console) at
<https://convilyn.com/en/settings/api> — the page is auth-gated and is
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
✓ [OK] CONVILYN_BASE_URL: https://api.convilyn.com (default)
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
2. `convert.create_and_wait` derived the processor from the two
   formats — `docx → pdf` is a `document_conversion` — started the job
   and polled until it finished (or failed).
3. `convert.download_to` fetched the presigned download URL from the
   completed job and wrote the bytes to disk.

### Re-running: downloads do not replace files

`download_to` refuses a destination that already exists, so running the snippet
above twice raises on the second run:

```
FileExistsError: report.pdf exists; pass overwrite=True to replace it
```

That is deliberate, and it is the same answer `convilyn local convert` gives:
guessing what you wanted is how a converter overwrites the wrong file. To re-run
over the previous result, say so:

```python
client.convert.download_to(job, to="report.pdf", overwrite=True)
```

The same applies to `client.goals.download_artifact_to(...)`, and to the CLI:

```bash
convilyn convert report.docx --to pdf --overwrite
```

One thing `overwrite=True` does **not** do: write through a symlink. A symlink at
the destination is refused either way, because following it would put the bytes
somewhere you did not name.

If any step fails (auth, transport, conversion error) you get a typed exception.
Every one of them descends from `ConvilynError`, so one `except` handles them
all:

```python
from convilyn import Convilyn, ConvilynError

try:
    job = client.convert.create_and_wait(file=f, target_format="mp3")
except ConvilynError as exc:  # covers every type listed below
    print("conversion failed:", exc)
```

Catch a specific one when you can actually do something different about it —
top up on `InsufficientCreditsError`, back off on `RateLimitError`, fall back to
file conversion on `UnderstandUnavailableError`.

**`QuotaExceededError` and `InsufficientCreditsError` are both HTTP 402 and they
are not the same thing.** A quota is a ceiling you were given and it resets at
the next period; a balance is money you hold and it does not refill on its own.
So they are separate types rather than one type you branch on by `code`:

```python
from convilyn import InsufficientCreditsError, QuotaExceededError

try:
    result = client.goals.run(goal_text="Summarise this", files=[file_id])
except InsufficientCreditsError as exc:
    # `shortfall_credits` is None when the server did not send the operands —
    # read that as unknown, never as zero.
    print(f"top up: short by {exc.shortfall_credits} credits")
except QuotaExceededError:
    print("allowance spent — wait for the next period, or upgrade")
```

The billing path refuses on three other statuses too, each wanting a different
next step: `FreeTierBlockedError` (403 — leave the Free plan),
`ChargeUnavailableError` (409 — transient, retry later) and `SpecNotPricedError`
(409 — permanent for that workflow, retrying will not help). A refusal code this
build does not model still arrives as a plain `APIError` with `code` and
`details` intact, so a new server signal never becomes an unhandled crash.

**Importable from `convilyn`:**

<!-- exceptions:cloud:begin — pinned by tests/integration/test_quickstart_exception_list.py -->
| | |
|---|---|
| `ConvilynError` | the base; every type below is a subclass |
| `AuthError` | the key is missing, malformed, or rejected |
| `APIError` | the API answered with an error status |
| `RateLimitError` | too many requests — back off and retry |
| `QuotaExceededError` | the plan's allowance for this call is used up |
| `InsufficientCreditsError` | your **balance** cannot fund this run — carries `required_credits` / `available_credits` / `shortfall_credits` |
| `PlanRequiredError` | the call needs a tier this account is not on |
| `FreeTierBlockedError` | a Free-plan gate refused the run — this workflow is not on Free, or Free's monthly cap is spent |
| `SpecNotPricedError` | this workflow has no price configured; retrying will not help |
| `ChargeUnavailableError` | billing could not record the charge right now — transient, retry later |
| `RetryExhaustedError` | retried to the configured limit and still failing |
| `S3UploadError` | the upload itself failed, before any job existed |
| `JobFailedError` | a conversion job finished with `status=failed` |
| `JobTimeoutError` | `create_and_wait` gave up before the job finished |
| `GoalJobFailedError` | the workflow-lane equivalent of `JobFailedError` |
| `GoalJobTimeoutError` | the workflow-lane equivalent of `JobTimeoutError` |
| `GoalArtifactUnusableError` | the workflow **succeeded** and its output cannot be handed back — no artifact of the kind you asked for, an unreadable one, or one over this method's in-memory cap. Branch on `reason` (`missing` / `unparsable` / `too_large`); carries `job_spec_id` / `artifact_id` |
| `UnderstandUnavailableError` | `goals.understand` / `goals.to_markdown` is not served by the connected platform |
<!-- exceptions:cloud:end -->

**Importable from `convilyn.local`** — the offline engine's own refusals. They
are still `ConvilynError` subclasses (via `LocalError`), so the `except` above
catches them too; only the import path differs:

<!-- exceptions:local:begin — pinned by tests/integration/test_quickstart_exception_list.py -->
| | |
|---|---|
| `convilyn.local.LocalError` | the base for this namespace |
| `convilyn.local.UnsupportedRouteError` | the two formats name no conversion this engine performs |
| `convilyn.local.MissingDependencyError` | the route works, but an extra or a system tool is not installed |
| `convilyn.local.ConversionFailedError` | the conversion was attempted and did not produce a usable result |
| `convilyn.local.PdfOperationError` | a `convilyn.local.pdf` operation was refused |
<!-- exceptions:local:end -->

`from convilyn import UnsupportedRouteError` raises `ImportError`, and that is
deliberate rather than an oversight: it is the *offline engine's* answer about
what this machine can convert, and the offline engine ships behind extras
(`convilyn[pdf]` and friends). Hoisting it into the top-level namespace would
put a name there that means nothing on a bare install.

Three things this taxonomy deliberately does **not** cover, because they are not
the API's answer to anything:

- `FileExistsError` from a download whose destination already exists — the
  destination's problem, and `overwrite=True` is the fix.
- `ValueError` / `TypeError` from arguments that do not make sense — `upload()`
  with neither a path nor content, `create(file="report.pdf")` when `file=`
  wants an uploaded `File`, a `page_range` on an image conversion, an empty
  `files` list. Those mean the call is wrong, not that the platform refused it,
  and Python already has names for them. The dividing question is what the SDK
  is telling you about: **your arguments** (builtin) or **what a finished job
  produced** (`GoalArtifactUnusableError`).
- `JobError` is **not** an exception, despite the name and despite being
  exported from `convilyn`. It is the model behind `job.error` — a `code` and a
  `message` read off a failed job. `except JobError` is a `TypeError` at
  runtime.

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
▶ [dry-run] Would POST /api/v1/jobs: {processor_type=document_conversion, target_format=pdf, source_format=docx}
↓ [dry-run] Would download to: report.pdf
[dry-run] No API calls made.

$ convilyn convert clip.mp4 --to mp3 --dry-run
↑ [dry-run] Would upload: clip.mp4 (8300124 B, video/mp4)
▶ [dry-run] Would POST /api/v1/jobs: {processor_type=media_processing, output_format=mp3}
↓ [dry-run] Would download to: clip.mp3
[dry-run] No API calls made.
```

The `processor_type` line is the point: it is derived from the two
formats, and every processor `convert` can reach is free. There is no
flag to read to find out which lane you are on.

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

### Windows: Git Bash rewrites the path before the CLI sees it

On Git Bash / MSYS2, an argument that looks like a Unix path is converted to a
Windows one **by the shell**, before any program runs:

```console
$ python -c "import sys; print(sys.argv[1:])" /api/v1/health
['C:/Program Files/Git/api/v1/health']
```

So `convilyn api GET /api/v1/health` asks for an endpoint that does not exist and
gets a **404** — which reads exactly like a broken backend, and has been
misdiagnosed as one. Turn the rewriting off for the session:

```bash
export MSYS2_ARG_CONV_EXCL='*'
```

This is shell behaviour, not an SDK bug: every program invoked that way sees the
same rewritten argument. It is called out here because `api` is the one command
whose arguments are always paths, so it is the one that always trips over it.
PowerShell, `cmd.exe`, WSL, macOS and Linux are unaffected.

## 7. AI workflows (`client.goals`)

The AI workflow runs **agentic** workflows: the backend assembles a
multi-step plan, calls MCP tools, and may stop to ask the user for
clarification mid-flight. Surface mirrors the conversion API but adds
HITL (`fill_slot` / `confirm`). Progress is observed by polling — see
§7.3 for why there is no event stream.

### 7.1 Five-line hello-world

```python
from convilyn import Convilyn

client = Convilyn()
job = client.goals.run(workflow_id="goal_lane.content_to_multipost", files=["file_abc"])
print(job.status, job.progress)
```

`run()` is shorthand for `start()` → `wait()`. It returns when the job
reaches a terminal status **or** stops for HITL (`slots_pending`).

### 7.2 Human-in-the-loop (slot filling)

When the agent needs more information, `wait()` returns with
`job.needs_input == True` and one or more `PendingSlot` entries:

```python
job = client.goals.run(workflow_id="goal_lane.content_to_multipost", files=["file_abc"])
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

**File-type slots.** When a `PendingSlot` has `slot_type == "file"`, the
answer is a `file_id` (from `client.files.upload(...)`), not free text — pass
one id, or a **list** for a slot that takes several files. Each id must be
owned by the same account/key running the job (unowned ids are rejected 403):

```python
doc = client.files.upload(path="claim.pdf")
job = client.goals.fill_slot(job.job_spec_id, slot_id="claim_doc", value=doc.file_id)

# a multi-file slot takes a list of ids
a, b = client.files.upload(path="r1.jpg"), client.files.upload(path="r2.jpg")
job = client.goals.fill_slots(job.job_spec_id, {"receipts": [a.file_id, b.file_id]})
```

### 7.3 Async

Every `client.goals` method has an async twin on `AsyncConvilyn`. Use it when
you are already inside an event loop; the sync `Convilyn` is a thin wrapper
around it.

```python
import asyncio
from convilyn import AsyncConvilyn


async def main() -> None:
    async with AsyncConvilyn() as client:
        job = await client.goals.start(
            workflow_id="goal_lane.content_to_multipost",
            files=["file_abc"],
        )
        job = await client.goals.wait(job.job_spec_id, timeout=1800)
        print(job.status)


asyncio.run(main())
```

`wait()` polls. There is no WebSocket stream — it was removed in 3.0.0 because
the gateway could not authenticate any consumer key, and making it work would
have required putting your API key in a URL. See
[STABILITY.md](https://github.com/CoreNovus/convilyn-python/blob/main/docs/STABILITY.md).

### 7.4 CLI — `convilyn goals`

The same surface from the shell:

```bash
# Dry-run preview (no network)
$ convilyn goals start --workflow-id goal_lane.content_to_multipost --files file_abc --dry-run --json

# Start and capture the id
$ JOB_ID=$(convilyn goals start --workflow-id goal_lane.content_to_multipost --files file_abc --json \
  | jq -r '.job_spec_id')

# Answer a slot the agent is waiting on
$ convilyn goals fill-slot "$JOB_ID" --slot-id target_platform --value '"linkedin"'

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
* **Progress is polled, not streamed.** `goals.wait(...)` is the
  supported way to follow a run. WebSocket streaming was removed in
  3.0.0 — it could not authenticate for any consumer key, and the
  only way to make it work would have put your API key in a URL.

### 7.6 Manage the workflows you author (`client.user_workflows`)

Workflows you build in the chat Builder (`client.builder`, or the web
app) are `uw_…` workflows you own. `client.user_workflows` is the typed
management namespace for them — no escape hatch needed:

```python
page = client.user_workflows.list()  # your workflows, cursor-paged
wf = client.user_workflows.get(page.items[0].workflow_id)

job = client.goals.run(user_workflow_id=wf.workflow_id, files=["report.pdf"])

runs = client.user_workflows.runs(wf.workflow_id)  # recent runs
backup = client.user_workflows.export(wf.workflow_id)  # portable JSON document
client.user_workflows.delete(wf.workflow_id)  # 409 while public — archive first
```

Editing / validating / publishing stay in the web Builder — the SDK is
a thin data-plane client for the verticals you ship, not a second
builder UI. Community workflows by *other* authors live under
`client.workflows` (search / fork / like).

### 7.7 Extraction — `goals.understand()` and `goals.to_markdown()`

These two are for content that has to be **extracted** before it can be read:
a scanned page with no text layer, a figure that needs describing. That work
calls per-unit billed third-party services, so unlike `client.convert` it
**costs credits**.

`understand()` returns data in a shape you specify, and the platform grounds
every value against the input before returning it:

```python
result = client.goals.understand(
    ["file_abc"],
    schema={  # a JSON Schema dict — no extra dependency
        "type": "object",
        "properties": {
            "invoice_no": {"type": "string"},
            "total": {"type": "number"},
        },
        "required": ["invoice_no", "total"],
    },
    instructions="The total is the figure after tax.",  # optional steer
)
```

`to_markdown()` is the unstructured sibling — it returns a Markdown string.

**Before reaching for `to_markdown()`, check you actually need it.** Rendering a
`.docx` / `.pdf` / `.pptx` / `.xlsx` to Markdown is deterministic file
conversion, which is free on every plan and is what `client.convert` (or
`convilyn local convert`) does. `to_markdown()` earns its cost only when the
content is not there to be read — a scan, an image, a diagram.

`to_markdown()` takes **one file** and routes on what you uploaded — a
document, an image, an audio file or a video file each has its own pipeline.
A kind of file no pipeline serves raises `UnderstandUnavailableError`, and the
message names the free alternative:

```python
try:
    md = client.goals.to_markdown(["file_abc"])
except convilyn.UnderstandUnavailableError:
    job = client.convert.create_and_wait(file=f, target_format="md")
```

That error is raised **before** any credit is spent, and it is never substituted
with a differently-shaped result: an answer the platform did not ground is not
returned as though it had been. `client.account.get_quota()` (§8.2) prices a run
before you start it.

**When the run finishes and there is still nothing to return.** All three of
these methods promise a shape, and a job can succeed without producing it — most
often a `partial` run, where some tasks failed and the platform reports the
result it does have rather than throwing your money away. That is
`GoalArtifactUnusableError`, not a `ValueError`: you passed nothing wrong and the
run was charged. Branch on `reason`:

```python
from convilyn import GoalArtifactUnusableError

try:
    result = client.goals.understand(["file_abc"], schema=schema)
except GoalArtifactUnusableError as exc:
    if exc.reason == "too_large":
        # The artifact is fine — just bigger than this method buffers. Both
        # arguments you need are on the exception.
        client.goals.download_artifact_to(exc.job_spec_id, exc.artifact_id, to="out.json")
    elif exc.reason == "unparsable":
        report(exc.artifact_id, exc.detail)  # retrying will produce the same bytes
    else:  # "missing"
        print(f"nothing of that kind was produced; job status: {exc.job_status}")
```

**A failed or unusable run does not have to be paid for twice.**
`client.goals.retry(job_spec_id)` reuses the same job spec and costs **no
credits and no quota**; calling `understand()` again creates a new job spec and
is charged again. Ask the exception which one applies rather than guessing from
the message:

```python
except convilyn.GoalJobFailedError as exc:
    if exc.retryable:
        job = client.goals.retry(exc.job_spec_id)   # default mode: no new credits
    else:
        print(exc.suggested_action)   # "upgrade" | "login" | "contact_support" | "none"
```

`exc.suggested_action` is the server's own next step for this failure, so you
never keep a second copy of the code-to-action mapping; `exc.retryable` is just
`suggested_action == "retry"`. They are not the same question: a plan ceiling is
**not** retryable but **is** actionable, which is why a plain boolean is not what
the API sends.

**`exc.detail` says which limit, when there was one.** `PROCESSING_LIMIT` covers
four unrelated ceilings, and the message is the same canned sentence for all of
them. The operands let you tell them apart — and decide whether changing the
input would help at all:

```python
except convilyn.GoalJobFailedError as exc:
    if exc.detail and exc.detail.reason == "ITERATION_LIMIT":
        print(f"stopped after {exc.detail.reached} of {exc.detail.limit} steps")
```

`reason` is one of `ITERATION_LIMIT`, `TOKEN_BUDGET`, `REPEATED_TOOL_CALL`,
`SCRATCHPAD_READ_BUDGET`. `detail` is `None` on most failures — the code says
everything there is to say — and `limit` / `reached` are `None` rather than `0`
when a resumed run has no counter, so a missing number never reads as a real one.
Branch on the reasons you handle and treat an unfamiliar one as absent: the
server may know a ceiling your installed version does not.

## 8. Check your plan + quota before running (`client.account`)

Some Convilyn endpoints (fork a public workflow, publish your own,
run an expensive AI workflow) require Pro tier. The SDK
surfaces the platform's tier + quota model so you can pre-flight a
call without parsing raw HTTP errors.

### 8.1 What tier am I on?

```python
plan = client.account.get_plan()
print(plan.tier)  # "free" | "pro"
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
    client.workflows.fork(source_spec_id="goal_lane.content_to_multipost")
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
| installing `convilyn` | ✅ free | ✅ free |
| Call any API (with valid `ck_` key) | ✅ free up to monthly cap | ✅ higher cap |
| `client.convert` (file conversion) | ✅ within cap | ✅ within cap |
| `client.goals` (agentic workflow run) | ✅ within cap | ✅ higher cap + soft-limit warning past 100% |
| `client.workflows.fork` (private copy of a public workflow) | ❌ raises `PlanRequiredError` | ✅ |
| `client.workflows.publish` (make your workflow public) | ❌ raises `PlanRequiredError` | ✅ |

## 9. Data handling & privacy

Convilyn keeps only what a run needs. Full details — retention windows,
processing regions, encryption, and the AI-training stance — are on the
[Data handling & retention](https://docs.convilyn.com/en/data-handling/)
page. The essentials for SDK callers:

* **Uploads are ephemeral by default.** Input files are deleted automatically
  ~1 hour after upload (a workflow still using a file keeps it until the run
  finishes). You do not opt in to ephemeral processing — it is the default.
* **Delete on demand.** Don't want to wait for the sweep? Remove the cloud
  copy the moment a run finishes:

  ```python
  client.files.delete(file_id)  # async: await client.files.delete(...)
  ```

* **Your content is not used for AI training.** Every run started with a
  `ck_` API key is structurally excluded from Convilyn's training pipeline
  (excluded by default, enforced in code), and the underlying model provider is
  contractually barred from training on it either.
* **List what's stored.** `client.files.list()` returns your *durable* files
  (e.g. emailed-in attachments) plus a storage-usage summary. Ephemeral
  uploads are not listed — they are already on the 1-hour sweep.

**Honest limits — privacy-sensitive / edge callers, read this:**

* **Processing region is fixed, not per-request.** Files are processed in AWS
  Tokyo (inference in us-east-1); you cannot pin a region per call today. If
  data residency is a hard requirement, contact us.
* **Sensitive-value masking is per-workflow best-effort, not a platform
  guarantee.** Some workflows (e.g. the personal-document workflow) mask
  identifiers to their last 4 digits as output hygiene, but there is no
  platform-wide PII-redaction step. If your documents must never leave the
  device, process them locally — the cloud path fits content where ephemeral
  storage plus deletion control is sufficient.

## 10. Going further

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
  SDK with `uv add convilyn-author` (a separate package — see
  [`sdk/author-python/docs/README.md`](../../author-python/docs/README.md)).
* **Contributing**: read [`../AGENT.md`](../AGENT.md) for the SOLID
  seams (`RetryPolicy`, `OutputRenderer`, `_build_client` factory)
  before extending the SDK.

## 11. Common questions

**Where can I see all the supported formats?**
Ask the backend — it publishes the producible pairs per family at
`GET /api/v1/document/support`, `GET /api/v1/image/support` and
`GET /api/v1/media/support`. (`convilyn api GET /api/v1/media/support
--json` prints one from the shell.) This page deliberately does not
reproduce those lists: a copy of a matrix is stale the moment the
matrix moves.

`convert` covers all three of those families and picks the processor
from your two formats, so documents, images and media transcodes are
one verb. `--dry-run` prints which one a given call would reach.
Processors that are *not* reachable from `convert` are the metered
ones — OCR and transcription — because it is the verb that does not
spend credits; see `goals.understand()`.

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
