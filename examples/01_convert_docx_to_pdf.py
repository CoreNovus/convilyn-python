"""Convert a file to PDF — the five-line hello-world.

Usage:
    export CONVILYN_API_KEY=ck_...
    python examples/01_convert_docx_to_pdf.py

Substitute any document path on disk; the SDK auto-detects the source
format from the filename extension (DOCX / PDF / PPTX / TXT / HTML / MD).
"""

from __future__ import annotations

from pathlib import Path

from convilyn import Convilyn

INPUT_FILE = Path(__file__).parent / "sample.txt"
OUTPUT_FILE = Path(__file__).parent / "sample.pdf"


def main() -> None:
    client = Convilyn()  # reads CONVILYN_API_KEY from env
    file = client.files.upload(INPUT_FILE)
    job = client.convert.create_and_wait(file=file, target_format="pdf")
    written = client.convert.download_to(job, to=OUTPUT_FILE)
    print(f"OK: job={job.job_id} status={job.status} → {written}")


if __name__ == "__main__":
    main()
