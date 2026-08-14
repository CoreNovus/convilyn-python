"""Async variant of the hello-world.

The async client (``AsyncConvilyn``) is the primary surface — every
SDK method is natively async. ``convilyn.Convilyn`` (used in
``01_convert_docx_to_pdf.py``) is a thin sync wrapper around an
``AsyncConvilyn`` instance.

Use this style when you're already inside an event loop (FastAPI,
async worker, agent runtime) and don't want to thread-trampoline
through ``asyncio.run``.

Usage:
    export CONVILYN_API_KEY=ck_...
    python examples/02_async_convert.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from convilyn import AsyncConvilyn

INPUT_FILE = Path(__file__).parent / "sample.txt"
OUTPUT_FILE = Path(__file__).parent / "sample.pdf"


async def main() -> None:
    async with AsyncConvilyn() as client:
        file = await client.files.upload(INPUT_FILE)
        job = await client.convert.create_and_wait(file=file, target_format="pdf")
        written = await client.convert.download_to(job, to=OUTPUT_FILE)
    print(f"OK: job={job.job_id} status={job.status} → {written}")


if __name__ == "__main__":
    asyncio.run(main())
