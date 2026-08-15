"""AI workflow async usage + WebSocket event streaming.

Subscribes to the AI workflow WebSocket events stream and prints each
event as it arrives. The async API is the primary surface — streaming
is **async-only**: the sync
``client.goals`` does NOT expose ``events()`` because bridging a
WebSocket through ``asyncio.run`` per call is ergonomically and
performance-wise wrong.

Requires both ``CONVILYN_API_KEY`` (HTTP) and ``CONVILYN_WS_URL``
(WebSocket endpoint, e.g. ``wss://ws.convilyn.com``) to be set, or pass
``ws_url=`` directly. When neither is configured the SDK raises
:class:`WebSocketError`.

Usage:
    export CONVILYN_API_KEY=ck_...
    export CONVILYN_WS_URL=wss://ws.convilyn.com
    python examples/06_goals_async_events.py <file_id>
"""

from __future__ import annotations

import asyncio
import os
import sys

from convilyn import AsyncConvilyn, WebSocketError


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: 06_goals_async_events.py <file_id>", file=sys.stderr)
        return 1
    if not os.environ.get("CONVILYN_WS_URL"):
        print(
            "warning: CONVILYN_WS_URL is unset; SDK will raise WebSocketError "
            "when subscribing to events. Set it to your environment's WS endpoint.",
            file=sys.stderr,
        )

    file_id = sys.argv[1]

    async with AsyncConvilyn() as client:
        # 1. Start the job (no `wait` — we listen on the WS instead).
        job = await client.goals.start(workflow_id="doc_analyzer", files=[file_id])
        print(f"started: {job.job_spec_id}")

        # 2. Stream events. Subscribe-then-start ordering is preserved
        #    because we already created the job above; the WS handshake
        #    happens just before the first frame is received.
        try:
            async for event in client.goals.events(job.job_spec_id):
                print(f"  {event.type:<22}  seq={event.seq}  data={event.data}")
                if event.is_terminal:
                    break
        except WebSocketError as exc:
            print(f"event stream error: {exc}", file=sys.stderr)
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
