# Convilyn — local document conversion

Read `.docx`, `.pptx`, `.xlsx`, `.odt`, `.pdf`, `.epub` and ~50 more formats as
Markdown, converted on your own machine. No tokens, no network, no model in the
path — and the same bytes out on every run.

## Install the package first

This plugin drives the `convilyn` command. It does not bundle it:

```bash
uv tool install "convilyn[all,mcp]"   # or: pip install --user "convilyn[all,mcp]"
```

Without that, both halves of the plugin are inert — the skill's commands are not
found, and the MCP server cannot start. `[all]` adds the format libraries;
`[mcp]` adds the MCP server. Neither is in the base install, which is deliberate:
the base package is `httpx` + `pydantic` + `click` and nothing else.

**Install it where your editor can find it.** Your editor starts the MCP server,
not your shell, so it has to reach `convilyn` on `PATH` — and a project
virtualenv is not on the editor's `PATH`. `uv tool install` and
`pip install --user` both work; `uv add` inside a project does not.

> If you are setting convilyn up on your own machine rather than handing it to a
> team, you do not need this plugin at all: `convilyn agent install` writes a
> skills-directory plugin straight into `~/.claude/skills/convilyn/`, which
> Claude Code loads with no marketplace and no install step.

<!-- The three files in this directory are GENERATED from src/convilyn/agent/
     by scripts/build_plugin.py. Edit the canonical copies there; --check runs
     in the standing gate. JSON has no comments, and `claude plugin validate
     --strict` treats an unrecognized key as an error, so this note is the only
     place the marker can live. -->


## What you get

**A skill** that tells the agent when local conversion actually beats reading a
file directly — and, just as importantly, when it does not. Plain text files
(`.md`, `.txt`, `.csv`, source code) are faster read as they are, and the skill
says so rather than routing everything through a converter.

**Five MCP tools**:

| tool | cost |
|---|---|
| `convert` | free, local |
| `capabilities` | free, local |
| `pdf` | free, local |
| `quota` | free, hosted (read-only) |
| `understand` | **spends credits** — structured extraction against a JSON Schema |

## Credentials

The three local tools need no account. For the hosted two, run:

```bash
convilyn setup
```

It saves a key where the CLI looks for it. **The MCP config here carries no
`env` block and no key**, on purpose: a key in a config file is a key in a file
that gets copied, shared and committed.

## Licence

Apache-2.0. Source: <https://github.com/CoreNovus/convilyn-python>
