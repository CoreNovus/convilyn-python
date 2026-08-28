# Convilyn SDK Examples

Runnable snippets you can copy + paste into your project. Each one
assumes `CONVILYN_API_KEY` is exported in the environment — except
[`11_local_convert_offline.py`](./11_local_convert_offline.py), which runs
entirely on your machine and reads no key at all.

| File | What it shows |
|---|---|
| [`01_convert_docx_to_pdf.py`](./01_convert_docx_to_pdf.py) | The five-line hello-world. Upload, convert, download. |
| [`02_async_convert.py`](./02_async_convert.py) | Same flow with `AsyncConvilyn` inside an `asyncio` event loop. |
| [`03_api_escape_hatch.py`](./03_api_escape_hatch.py) | Calling an unwrapped endpoint via `client._async._http.raw_request` (matches the `convilyn api` CLI). |
| [`04_convert_cli.sh`](./04_convert_cli.sh) | The equivalent shell session — `convilyn doctor` → `convert` → `api`. |
| [`05_goals_content_to_multipost.py`](./05_goals_content_to_multipost.py) | Run an agentic AI workflow end-to-end (upload → start → drive to terminal). |
| [`08_workflows_marketplace.py`](./08_workflows_marketplace.py) | Browse and fork community workflows via `client.workflows`. |
| [`09_account_quota.py`](./09_account_quota.py) | Check plan tier and pre-flight cost/quota via `client.account`. |
| [`10_uw_as_edge_operator.py`](./10_uw_as_edge_operator.py) | Wrap a Builder-authored `uw_*` workflow as an edge `ModelOperator` (`placement="cloud"`) via `client.goals.run` — the Path-A cloud→edge bridge. See [`docs/EDGE_PLACEMENT.md`](../docs/EDGE_PLACEMENT.md). |
| [`11_local_convert_offline.py`](./11_local_convert_offline.py) | Convert to Markdown with no key, no account and no network — `convilyn.local`, and asking `capabilities()` what this machine can actually do. |
| [`app_scaffold/`](./app_scaffold/) | A restartable consumer-app starting shape: durable app-side session (atomic JSON state) reconciled against the platform each run. Copy-paste, not a package — see its README for why. |
| [`sample.txt`](./sample.txt) | A 1 KiB plain-text sample for the Python examples; convert it to `pdf` or anything else. |

## Running an example

```bash
export CONVILYN_API_KEY=ck_...   # use a ck_ consumer key (author cvl_/cvi_ keys are rejected)
python examples/01_convert_docx_to_pdf.py
```

Or the CLI walkthrough:

```bash
bash examples/04_convert_cli.sh
```

All examples are kept syntactically valid by
[`../tests/integration/test_examples_syntax.py`](../tests/integration/test_examples_syntax.py) —
if a public SDK symbol moves, the test breaks before the docs do.
