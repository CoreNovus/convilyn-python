---
name: convilyn
description: >-
  Turn documents into Markdown locally — .docx, .pptx, .xlsx, .odt, .pdf, .epub
  and ~50 more — with no tokens, no network and no model in the path. Use when a
  file is a container rather than text (Office and OpenDocument files are zip
  archives, .doc and .xls are OLE), when a batch of files has to be read at once,
  when the output must be byte-identical on every run, or when structured fields
  have to be pulled out of a document and checked against the source. Do NOT use
  it for files that are already text — .md, .txt, .csv, source code — where
  reading them directly is faster and costs the same nothing.
allowed-tools: Bash(convilyn:*)
license: Apache-2.0
metadata:
  author: CoreNovus
  version: "3.6.1"
---
<!-- Generated from src/convilyn/agent/SKILL.md. Edit that file and run
     python scripts/build_plugin.py -- this copy is checked in CI. -->

# Reading documents without spending tokens

`convilyn` converts documents on the machine it runs on. No API call, no model,
no upload — so a conversion costs zero tokens and returns the same bytes every
time.

## When this beats reading the file yourself

**A container format.** `.docx`, `.pptx`, `.xlsx`, `.odt`, `.odp` are zip
archives; `.doc`, `.xls`, `.ppt` are OLE compound files. Reading one directly
gets you XML fragments or binary, and reassembling it is work with no upside.

**A batch.** Twenty files cost the same zero tokens as one, in one call.

**A large PDF.** Reading a long PDF page by page spends tokens proportional to
its length. Converting spends none — and the Markdown that comes back is what
you then reason over.

**Repeatability.** The output is deterministic, so a diff between two runs means
the input changed, not the weather.

**Formats you cannot open at all.** EPUB, MOBI, RTF, older Office files,
OpenDocument. Some need a helper installed (LibreOffice, Calibre); `convilyn
local doctor` says which, per machine.

## When to just read the file

`.md`, `.txt`, `.csv`, `.json`, source code — already text. Opening them
directly is faster and costs nothing either. A single PDF page you only need to
glance at is also quicker read than converted. **Converting text you can already
read is a step that buys nothing**, and this skill is not asking for it.

## The commands

```bash
convilyn local convert report.docx --to md          # one file
convilyn local batch *.pptx --out-dir ./md          # many, one pass
convilyn local doctor                               # what this machine can do
convilyn local formats                              # every route and its status
convilyn local pdf select in.pdf out.pdf --pages 1-3,7
```

`local convert` takes exactly one file; `local batch` is the one that takes
several. `local pdf` also has `merge`, `split`, `rotate`, `compress`, `protect`,
`unlock` and `info` — `info` is the cheapest way to learn whether a PDF has a
text layer before deciding anything else.

Everything above is local and free. Install the format support once:

```bash
pip install "convilyn[all]"      # or: uv add "convilyn[all]"
```

A conversion that needs something extra fails with the exact install command
for it — you do not have to guess.

## Structured extraction (hosted, and it costs)

Pulling named fields out of a document against a JSON Schema, grounded back to
the source text — an invoice total, a contract's parties, a spec sheet's
ratings.

**This one spends credits**, unlike everything above it. It needs an account
(`convilyn setup`). Price the run first with `convilyn account quota`, and get
the user's agreement before spending on their behalf. Local conversion is free;
this is not, and the difference is worth stating out loud when you offer it.

The MCP tool `convilyn_understand` takes local paths and uploads them for you.
The shell form takes already-uploaded file **ids**, not paths:

```bash
convilyn account quota                              # price it first
convilyn goals understand --files <file-id> --schema-file ./fields.json
```

## Talking to it over MCP instead

`convilyn mcp serve` exposes the same capabilities as MCP tools
(`convilyn_convert`, `convilyn_capabilities`, `convilyn_pdf`,
`convilyn_understand`, `convilyn_quota`). If those tools are already available
to you, that server is running and you can call them directly instead of
shelling out.

Both routes reach the same code, so use whichever your host already speaks. If
the MCP tools are absent, the shell commands above work on their own.

## Credentials

`convilyn setup` writes the key to a file the CLI finds on its own. **Do not put
an API key into an MCP config file or an environment block** — it is not needed
there, and a config file is a worse place for a secret than the credential store
already in use.
