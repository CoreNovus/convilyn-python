<!-- mcp-name: io.github.CoreNovus/convilyn -->

<p align="center">
  <img src="https://raw.githubusercontent.com/CoreNovus/convilyn-python/main/docs/assets/corenovus-community-banner.png" alt="CoreNovus — Connected AI Workflows" />
</p>

# convilyn

[![PyPI](https://img.shields.io/pypi/v/convilyn.svg)](https://pypi.org/project/convilyn/)
[![Python](https://img.shields.io/pypi/pyversions/convilyn.svg)](https://pypi.org/project/convilyn/)
[![Licence](https://img.shields.io/pypi/l/convilyn.svg)](https://github.com/CoreNovus/convilyn-python/blob/main/LICENSE)

Convert files on your own machine, or run AI workflows on the
[Convilyn](https://convilyn.com) platform — one package, one CLI.

## Convert a file with no account, no key, no network

```bash
pip install "convilyn[pdf]"
convilyn local convert report.pdf --to md
```

```
▶ Converting report.pdf → md
✓ Wrote report.md
```

`convilyn.local` runs entirely on your machine. Nothing is uploaded, no API key
is read, no quota is touched — and it is the same code whether you call it from
the shell or from Python:

```python
from convilyn import local

local.convert("report.pdf", to="md")  # one file
local.convert_many(["a.docx", "b.pptx"], out_dir="out/")  # many, one call
local.convert("photo.png", to="webp")  # images too

local.convert("report.pdf", out="build/report.md")  # or name the path…
local.convert("report.pdf", to="md", overwrite=True)  # …and re-run over it
```

`to=` names the format and writes beside the input; `out=` names the path and
reads the format from its suffix. Pass exactly one. Nothing replaces an existing
file unless you say `overwrite=True` — guessing what you wanted is how a converter
writes over the wrong one.

## How good is the conversion?

Every converter claims fidelity. We publish the measuring instrument instead.
[**doc-eval**](https://github.com/CoreNovus/doc-eval) is Apache-2.0, has no model,
no network and no API key in its scoring path, and gives the same score for the
same input on any machine — including yours, against your own documents.

On `synth-v1`, scoring the offline path this package ships:

| axis | measured |
|---|---|
| Text fidelity — normalised edit distance | **0.9664** (n=20) |
| Table structure — TEDS-Struct | **0.9333** (n=10) |
| Reading order | **1.0000** (n=8) |
| Inline formatting — F1 | **1.0000** (n=20) |

Then the part most benchmarks leave out — **657 real-world PDFs**, of which 650
converted. 145 of those 650 have no text layer at all: they are page images, and
this engine does no OCR by design. On the pages that do have text, reading order
holds at the control's level for ordinary multi-column layout (86.8% median
against an 85.4% control) and degrades on dense small-type pages — dictionaries,
newspapers — **by re-ordering rather than by losing text**: the word count still
matches the PDF's text layer at 1.00×.

Full results, corpora, metric definitions and known limitations:
[**What has been measured**](https://github.com/CoreNovus/convilyn-python/blob/main/docs/MEASURED-2026-09-08.md).
That file is the source of these figures and carries its measurement date in its
name, so an older report cannot be mistaken for the current one.

### Why a number can lie

One of doc-eval's rules exists because of a measured case: markitdown produced
**0 bytes** for all 98 documents in olmOCR-Bench's `old_scans` slice and still
scored **12.7%** — an empty file passes every "must not contain" check for free.

So doc-eval scores a blank prediction as zero rather than as a pass, records a
missing one instead of quietly dropping it, prints the `n` behind every mean, and
labels which metrics are *published* (comparable with the citing paper) and which
are its own. Those rules are the difference between a score and a claim.

None of this is a head-to-head: these are our numbers on our corpora. What we are
offering for comparison is the **method** — run the same tool over your own
documents and see what any converter, this one included, actually does.

## It tells you what it can do, and never guesses

```bash
convilyn local doctor
```

```
✓ pdfplumber: installed
✓ PIL: installed
! libreoffice: missing — Install LibreOffice from https://www.libreoffice.org/download/ (provides `soffice`).
! ffmpeg: missing — Install FFmpeg from https://ffmpeg.org/download.html (provides `ffmpeg`).
280 of 667 conversions available. Run `convilyn local formats` for the per-format detail.
```

Every route that is unavailable says **why**, and whether installing something
fixes it — a missing extra, a Pillow plugin we do not ship, or a build that
simply cannot write that format. Offline, that means no silent fallbacks and no
partly-converted files: a route either runs or refuses.

The scope of that sentence is deliberate. It is a property of the engine in this
package, which is why `convilyn local doctor` can enumerate it. The hosted
conversion API is a different codebase with its own quality labels — it publishes
a `qualityMode` per route at `GET /api/v1/{document,image,media}/support`, and a
`best_effort` route is telling you in advance that something is dropped or
flattened. Read that field before assuming a hosted conversion is lossless.

## What runs offline

| Conversion | Install |
| --- | --- |
| Plain text, CSV → Markdown | `convilyn` |
| PDF → Markdown, and PDF page operations | `convilyn[pdf]` |
| Word `.docx` → Markdown | `convilyn[docx]` |
| PowerPoint `.pptx` → Markdown | `convilyn[pptx]` |
| Excel `.xlsx` → Markdown | `convilyn[xlsx]` |
| XML → Markdown | `convilyn[xml]` |
| Images — PNG, JPEG, WebP, AVIF, TIFF, PSD, … and image → PDF | `convilyn[images]` |
| Everything above | `convilyn[all]` |

Legacy Office (`.doc`, `.xls`, `.ppt`), OpenDocument and ebook formats work too
when LibreOffice or Calibre is on your `PATH`; `doctor` names the one you need.

**Video and audio** — `.mov`, `.mp4`, `.webm`, `.avi`, `.mkv` and `.mp3`,
`.wav`, `.ogg`, `.m4a`, `.flac` — convert into one another, and a video converts
into an audio file, when **FFmpeg** is on your `PATH`:

```bash
convilyn local convert clip.mov --to mp4
convilyn local convert talk.mp4 --to mp3        # just the audio
```

Like the two above it is a program rather than a package, so no extra installs
it. Transcription is deliberately absent: it calls a paid service, and nothing
under `convilyn local` does.

**PDF page operations** are a separate namespace, because a PDF goes in and a
PDF comes out — rearranged, not converted:

```python
from convilyn.local import pdf

pdf.merge(["a.pdf", "b.pdf"], "combined.pdf")
pdf.select("report.pdf", "summary.pdf", pages="1-3,10")
pdf.burst("scan.pdf", "pages/")  # one file per page — `split` on the CLI
```

Also on the CLI: `convilyn local pdf {merge,select,split,rotate,compress,protect,unlock,info}`.
`protect` and `unlock` prompt for the password when you omit it, so it stays out
of your shell history.

## Working with an AI coding assistant

If you use Claude Code or Codex, one command lets the assistant do the
conversions itself instead of asking you to paste text:

```bash
uv tool install "convilyn[all,mcp]"   # or: pip install --user "convilyn[all,mcp]"
convilyn agent install
```

**Install it where your editor can find it.** The MCP server is started by the
editor, not by your shell, so it has to reach `convilyn` on `PATH` — a project
virtualenv is not on the editor's `PATH`. `uv tool install` and
`pip install --user` both put it somewhere that works.

That installs a skill describing when local conversion helps — and, just as
importantly, when reading the file directly is the better move — and registers
an MCP server offering five tools: `convert`, `capabilities`
and `pdf` (local, free), `quota`, and `understand`
(hosted, spends credits, and says so where the assistant reads it).

Each host looks in its own place, so the command writes to both:

| Host | What it gets | Where |
|---|---|---|
| Claude Code | a plugin carrying the skill and the MCP server, loaded with no marketplace and no install step | `~/.claude/skills/convilyn/` |
| Codex | the skill, and an `[mcp_servers.convilyn]` table merged into your config | `~/.agents/skills/convilyn/`, `~/.codex/config.toml` |

Claude Code picks it up on the next session (or `/reload-plugins` now); Codex on
the next run. It merges into your existing config rather than replacing it, is
safe to re-run, and takes `--dry-run`. **No API key is written into any config
file** — `convilyn setup` already stores it where the CLI looks.

Only two of the five tools need an account: `understand` and
`quota` reach the platform. The three local ones work with no `convilyn
setup` at all.

To hand the same thing to a team from a marketplace instead:

```
/plugin marketplace add CoreNovus/convilyn-python
/plugin install convilyn@convilyn
```

## The platform half — AI workflows

With an API key, the same package reaches the hosted workflows: conversions that
run on our infrastructure, and agentic workflows that ask you for what they are
missing.

```python
from convilyn import Convilyn

client = Convilyn()  # reads CONVILYN_API_KEY from env
file = client.files.upload("report.docx")
job = client.convert.create_and_wait(file=file, target_format="pdf")
client.convert.download_to(job, to="report.pdf")
```

- `client.files` · `client.convert` — upload, convert, download
- `client.goals` — agentic workflows, with human-in-the-loop slot filling
- `client.workflows` · `client.user_workflows` — the community library, and the
  ones you authored
- `client.builder` — build a workflow by chatting to it
- `client.account` — your tier, and what a run will cost *before* you start it

`AsyncConvilyn` is the same surface, awaitable. Both retry 5xx / 429 / 408 with
exponential backoff and jitter, stamp `Idempotency-Key` on mutating verbs, and
honour `Retry-After`.

**Built for scripts and agents.** Every command takes `--json`; the ones that
upload or spend also take `--dry-run`. All of them exit with a pinned code
(0 ok · 1 usage · 2 API error · 3 job failed · 130 interrupted), so a loop can
branch on the result without parsing English:

```bash
convilyn account quota --tool pdf-mcp:extract_text --json | jq .estimated_usd
convilyn goals start --goal-text "summarise these contracts" --files file_abc --dry-run
```

## Free to install, metered to use

`pip install convilyn` is free, and everything under `convilyn local` stays free
and unlimited — it runs on your hardware. Platform calls draw on your balance and
your plan, and every refusal is a typed `APIError` subclass rather than an opaque
failure: `InsufficientCreditsError` (your balance cannot fund this run — it
carries `shortfall_credits`), `QuotaExceededError` (an allowance is spent),
`PlanRequiredError` and `FreeTierBlockedError` (this needs a different plan).
Check first with `client.account`.

## Known limits

- **Goal progress is polling-only.** Follow a run with `client.goals.wait(...)`
  or `retrieve(...)`. WebSocket streaming was removed in 3.0.0: the gateway
  authenticates no credential this SDK can hold, and the only way to change
  that would have put your API key in a URL query string — a WebSocket
  handshake carries no headers. See
  [STABILITY.md](https://github.com/CoreNovus/convilyn-python/blob/main/docs/STABILITY.md).
- **Beta.** The public surface and its SemVer promise are written down in
  [STABILITY.md](https://github.com/CoreNovus/convilyn-python/blob/main/docs/STABILITY.md);
  anything not listed there may move.

## Authoring workflows? Different package

`convilyn` is the **consumer** SDK — you call the API with it. To *build* a tool
server or author a workflow spec, install
[`convilyn-author`](https://github.com/CoreNovus/convilyn-author-python):

```bash
pip install convilyn-author
convilyn-author init my-server
```

They are deliberately separate so consumers never pay the FastAPI / uvicorn
dependency cost.

## Documentation

- [Quickstart](https://github.com/CoreNovus/convilyn-python/blob/main/docs/QUICKSTART.md)
  — 5 minutes, covering offline conversion, goals, workflows and quota
- [What has been measured](https://github.com/CoreNovus/convilyn-python/blob/main/docs/MEASURED-2026-09-08.md) — full results, measured 2026-08-28
  — conversion and extraction scored on three named corpora (685 documents),
  including where it falls short
- [Full documentation](https://docs.convilyn.com)
- [Examples](https://github.com/CoreNovus/convilyn-python/tree/main/examples)
  — runnable Python and shell scripts
- [Changelog](https://github.com/CoreNovus/convilyn-python/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/CoreNovus/convilyn-python/blob/main/CONTRIBUTING.md)
  — DCO (`git commit -s`), no CLA; contributions land in the shipped package
- [AGENT.md](https://github.com/CoreNovus/convilyn-python/blob/main/AGENT.md)
  — for AI coding agents working on this SDK

Report a vulnerability privately via
[SECURITY.md](https://github.com/CoreNovus/convilyn-python/blob/main/SECURITY.md)
— never in a public issue.

## Licence

Apache-2.0. See
[LICENSE](https://github.com/CoreNovus/convilyn-python/blob/main/LICENSE).
