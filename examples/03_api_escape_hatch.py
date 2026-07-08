"""Call an unwrapped backend endpoint via the SDK's transport.

This mirrors what the ``convilyn api`` CLI does. Useful when the
backend ships a new endpoint and the SDK has not wrapped it as a
first-class resource yet — you still want the SDK's auth, retry,
and ``Idempotency-Key`` behaviour rather than dropping to raw
``httpx`` + handcrafted headers.

Usage:
    export CONVILYN_API_KEY=ck_...
    python examples/03_api_escape_hatch.py
"""

from __future__ import annotations

import asyncio

from convilyn import AsyncConvilyn


async def main() -> None:
    async with AsyncConvilyn() as client:
        # raw_request returns the response on any status (4xx / 5xx
        # included) so callers can inspect the wire body for debugging.
        # See sdk/consumer-python/AGENT.md for the SOLID seam this exposes.
        response = await client._async._http.raw_request(  # noqa: SLF001
            "GET",
            "/api/v1/jobs",
            params={"limit": "5"},
        )

    print(f"status={response.status_code}")
    print(f"headers={dict(response.headers)}")
    if response.status_code < 400:
        print(f"body={response.json()}")
    else:
        print(f"error body={response.text}")


if __name__ == "__main__":
    asyncio.run(main())
