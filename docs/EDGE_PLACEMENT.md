# Running a chat-built workflow at the edge

You author a workflow by chatting with the Builder. The result is a **cloud**
`uw_*` UserWorkflow spec that runs in the Convilyn cloud. On the edge, the
`convilyn-edge` SDK runs a workflow as a composition of **typed operators**, where
the model step is a [`ModelOperator`](https://pypi.org/project/convilyn-edge/) —
"run typed, schema-constrained inference at a chosen placement", returning a
validated `ModelResult`, **never a bare string**.

Those are two different representations. This page documents the **bridge between
them**, which needs **zero platform change**.

## What the bridge is

| | |
|---|---|
| **What runs the model** | The published cloud workflow, over the consumer SDK |
| **Placement** | `"cloud"` |
| **Platform change** | **None** — the `uw_*` already runs |

The workflow ships to the device today by wrapping it: the device holds the
operator graph and its fallback behaviour, and the model step reaches the cloud
workflow you already published.

## The pattern

A `ModelOperator`'s `placement="cloud"` implementation wraps
`client.goals.run(user_workflow_id="uw_…")`:

```python
job = await client.goals.run(user_workflow_id="uw_acme.pos_error_explainer", slots={...})
# terminal + message → ModelResult(status="success", output=..., evidence=(job cite,))
# failed / timed out  → ModelResult(status="unavailable")   # offline-first fallback
```

> Discover / manage the `uw_` ids you own with the typed
> `client.user_workflows` namespace (`list` / `get` / `runs` / `export`
> / `delete`) — no raw endpoint calls needed.

- **Generic reference:** [`examples/10_uw_as_edge_operator.py`](../examples/10_uw_as_edge_operator.py)
  — a scenario-free `UserWorkflowModelOperator` with a self-verifying offline demo.

## Boundary — what the adapter must NOT do

The bridge is deliberately thin. The **server holds every deterministic safety
gate** — redaction, budget, retry, cycle detection, tool permission — and
**re-grounds every value** the workflow returns. The adapter only *submits* a job
and *reads* the typed result; it re-implements none of those gates.

## Offline-first

Any failure / timeout / non-terminal outcome maps to `status="unavailable"` (or
`"uncertain"`) — **never raises past the model boundary** — so the workflow takes
its fixed fallback path and the device keeps working when the cloud is unreachable.
The finished cloud job is cited as `Evidence` (`convilyn://jobs/<id>`) so an auditor
can pull its full trace; the device is never a second source of truth.
