# App scaffold — restartable consumer app (copy-paste example)

A minimal, **copy-paste** starting shape for a consumer app that must
survive restarts: a durable app-side session (which jobs did *I*
submit?) reconciled against the platform on every run.

This lives in `examples/` **on purpose** — it is not a package and not
part of the SDK's public surface. Convenience app-layer helpers ship as
examples/templates, not as libraries, until the same shape has been
hand-copied by enough real verticals to justify an API (the repo's
package-addition gate: see `docs/architecture/sdk-repo-architecture.md`,
"Package-addition gate (the Stripe test)"). Copy these files into your
project and adapt freely.

| File | What it shows |
|---|---|
| [`app_session.py`](./app_session.py) | A ~60-line durable session helper: atomic JSON state file + immutable job records. Self-contained (stdlib only) — yours to own and modify. |
| [`app.py`](./app.py) | The restartable main loop: submit via `client.goals.run`, track locally, reconcile pending jobs and download artifacts on the next run. |

Honest division of labour: understanding, grounding, and schema
conformance are **platform** guarantees; `convilyn` is the thin
data-plane client; the session memory here is plain app plumbing.

```bash
uv add convilyn          # or: pip install convilyn
export CONVILYN_API_KEY=ck_...
python app.py
```

Need a device-side durable queue instead (offline kiosk / register)?
That is the Edge SDK's territory — `convilyn_edge.offline` — not an
app-layer copy; don't duplicate it here.
