# Running a chat-built workflow at the edge

You author a workflow by chatting with the Builder. The result is a **cloud**
`uw_*` UserWorkflow spec that runs in the Convilyn cloud. On the edge, the
`convilyn-edge` SDK runs a workflow as a composition of **typed operators**, where
the model step is a [`ModelOperator`](https://pypi.org/project/convilyn-edge/) —
"run typed, schema-constrained inference at a chosen placement", returning a
validated `ModelResult`, **never a bare string**.

Those are two different representations. This page documents the **first, low-cost
bridge between them** — Path A — which needs **zero platform change**.

## Two paths

| | Path A (this page) | Path B (follow-on) |
|---|---|---|
| **What runs the model** | The published cloud workflow, over the consumer SDK | A device-local SLM, via the on-device compute interface |
| **Placement** | `"cloud"` | `"edge"` |
| **Platform change** | **None** — the `uw_*` already runs | Compile `uw_*` → an edge-deployable bundle |
| **When** | Available today | Not yet generally available — see [Path B](#path-b--the-cloudevice-bundle-delivery-chain) |

Path A is the recommended starting point: ship the workflow to a device today by
wrapping it, and move the model on-device later without changing the workflow.

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

## Path B — the cloud→device bundle delivery chain

Path B compiles the same chat-built `uw_*` into an **edge-deployable bundle** the
device pulls, verifies, and runs with a local model. The platform half is shipped
(dark — every step sits behind `edge_push_transport_enabled`, default OFF); the
device-run half is hardware-gated. The actual API sequence:

1. **Author** — build the workflow by chat as usual. To be edge-compilable it must
   declare an author intent, e.g.
   `agent_config.provider_intent = {"reason": {"edge_eligible": true, "residency": "edge"}}`
   (the intent says *what you need* — never a model id or silicon).
2. **Compile + persist** — an operator seeds the bundle:
   `POST /api/v1/edge/bundles {"spec_id": "uw_…", "device_profile": "jetson_orin_8gb"}`
   (optionally a `device_manifest` the device itself reported). Server-side this is
   a **deterministic compiler** — the single provider resolver + an engine→format
   registry; same spec + same device always compile a byte-identical
   references-only manifest → `{"bundle_id": "bundle-…"}`.
3. **Stage assets** — the same call uploads the bundle's weight artifacts
   (GGUF / ONNX / TensorRT) to the edge-asset bucket, keyed exactly as the push
   endpoint signs, with a `content-sha256` integrity pin as object metadata.
4. **Device pull** — the device calls
   `POST /api/v1/edge/push {"bundle_id": …, "device_id": …}` and receives a
   `DeviceInstallablePayload`: every reference resolved to a short-TTL signed
   URL, references only, never secrets or raw bytes.
5. **Verify + install + run offline** — the device fetches each URL, **re-hashes
   against the manifest's digest** (the device is the integrity authority),
   installs, and runs the workflow fully offline through the on-device
   mini-orchestrator — the *same* graph and the same seven deterministic safety
   gates the server runs, re-bound locally. Results queue and reconcile
   exactly-once when connectivity returns.

Real-hardware acceptance for steps 4-5 lives in `backend-api/tests/e2e/edge/`
(`jetson_hardware`-gated; arms with `RUN_JETSON_HARDWARE_E2E=true` + a plugged-in
device — the env contract is in that directory's `conftest.py`).
