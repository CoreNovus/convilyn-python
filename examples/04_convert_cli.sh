#!/usr/bin/env bash
# Convilyn CLI walkthrough — equivalent to 01_convert_docx_to_pdf.py.
#
# Usage:
#   export CONVILYN_API_KEY=ck_...
#   bash examples/04_convert_cli.sh
#
# Requires the convilyn binary on PATH (`pip install convilyn`).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
INPUT="${HERE}/sample.txt"
OUTPUT="${HERE}/sample.pdf"

echo "── 1. Verify environment ──"
convilyn doctor

echo
echo "── 2. Convert ${INPUT} → ${OUTPUT} ──"
convilyn convert "${INPUT}" --to pdf --output "${OUTPUT}"

echo
echo "── 3. Inspect the resulting job via the escape hatch ──"
# The convert command prints the job ID; in a real script you'd
# capture it. Here we just list recent jobs through the escape hatch.
convilyn api GET /api/v1/jobs --query limit=5 --json | head -c 400
echo

echo
echo "── 4. Done ── Output: $(ls -lh "${OUTPUT}" | awk '{print $5, $9}')"
