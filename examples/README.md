# Convilyn SDK Examples

Runnable snippets you can copy + paste into your project. Each one
assumes `CONVILYN_API_KEY` is exported in the environment.

| File | What it shows |
|---|---|
| [`01_convert_docx_to_pdf.py`](./01_convert_docx_to_pdf.py) | The five-line hello-world. Upload, convert, download. |
| [`02_async_convert.py`](./02_async_convert.py) | Same flow with `AsyncConvilyn` inside an `asyncio` event loop. |
| [`03_api_escape_hatch.py`](./03_api_escape_hatch.py) | Calling an unwrapped endpoint via `client._async._http.raw_request` (matches the `convilyn api` CLI). |
| [`04_convert_cli.sh`](./04_convert_cli.sh) | The equivalent shell session — `convilyn doctor` → `convert` → `api`. |
| [`05_goals_doc_analyzer.py`](./05_goals_doc_analyzer.py) | Run an agentic AI workflow end-to-end (upload → start → drive to terminal). |
| [`06_goals_async_events.py`](./06_goals_async_events.py) | Subscribe to the AI-workflow WebSocket event stream with `AsyncConvilyn`. |
| [`08_workflows_marketplace.py`](./08_workflows_marketplace.py) | Browse and fork community workflows via `client.workflows`. |
| [`09_account_quota.py`](./09_account_quota.py) | Check plan tier and pre-flight cost/quota via `client.account`. |
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
