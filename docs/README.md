<p align="center">
  <img src="https://raw.githubusercontent.com/CoreNovus/convilyn-python/main/docs/assets/corenovus-community-banner.png" alt="CoreNovus — Connected AI Workflows" />
</p>

# convilyn

[![PyPI](https://img.shields.io/pypi/v/convilyn.svg)](https://pypi.org/project/convilyn/)
[![Python](https://img.shields.io/pypi/pyversions/convilyn.svg)](https://pypi.org/project/convilyn/)
[![Licence](https://img.shields.io/pypi/l/convilyn.svg)](https://github.com/CoreNovus/convilyn-python/blob/main/LICENSE)

Convert files on your own machine, or run AI workflows on the
[Convilyn](https://convilyn.corenovus.com) platform — one package, one CLI.

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

local.convert("report.pdf", to="md")                       # one file
local.convert_many(["a.docx", "b.pptx"], out_dir="out/")   # many, one call
local.convert("photo.png", to="webp")                      # images too

local.convert("report.pdf", out="build/report.md")         # or name the path…
local.convert("report.pdf", to="md", overwrite=True)       # …and re-run over it
```

`to=` names the format and writes beside the input; `out=` names the path and
reads the format from its suffix. Pass exactly one. Nothing replaces an existing
file unless you say `overwrite=True` — guessing what you wanted is how a converter
writes over the wrong one.

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
simply cannot write that format. No silent fallbacks, no partly-converted files.

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
pdf.burst("scan.pdf", "pages/")     # one file per page — `split` on the CLI
```

Also on the CLI: `convilyn local pdf {merge,select,split,rotate,compress,protect,unlock,info}`.
`protect` and `unlock` prompt for the password when you omit it, so it stays out
of your shell history.

## The platform half — AI workflows

With an API key, the same package reaches the hosted workflows: conversions that
run on our infrastructure, and agentic workflows that ask you for what they are
missing.

```python
from convilyn import Convilyn

client = Convilyn()                     # reads CONVILYN_API_KEY from env
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
convilyn goals start "summarise these contracts" --dry-run
```

## Free to install, metered to use

`pip install convilyn` is free, and everything under `convilyn local` stays free
and unlimited — it runs on your hardware. Platform calls draw on your plan's
quota, and the SDK raises typed `PlanRequiredError` / `QuotaExceededError`
(both `APIError`) rather than failing opaquely. Check first with
`client.account`.

## Known limits

- **Goal progress is polling-only.** Follow a run with `client.goals.wait(...)`
  or `retrieve(...)`. The WebSocket gateway does not accept consumer `ck_` keys
  yet, so `goals.events()` raises with guidance pointing back at polling.
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
- [Full documentation](https://docs.convilyn.corenovus.com)
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
