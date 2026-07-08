#!/usr/bin/env bash
# Convilyn `goals` CLI walkthrough — equivalent to 05/06 in shell.
#
# Usage:
#   export CONVILYN_API_KEY=ck_...
#   export CONVILYN_WS_URL=wss://ws.convilyn.com
#   bash examples/07_goals_cli.sh <file_id>
#
# Requires the convilyn binary on PATH (`pip install convilyn`) and `jq`
# for the JSON-piping step.

set -euo pipefail

FILE_ID="${1:?usage: $0 <file_id>}"

echo "── 1. Verify environment ──"
convilyn doctor

echo
echo "── 2. Dry-run preview ──"
convilyn goals start \
  --workflow-id doc_analyzer \
  --files "${FILE_ID}" \
  --dry-run --json | jq .

echo
echo "── 3. Start the job, capture the job_spec_id ──"
JOB_ID=$(convilyn goals start \
  --workflow-id doc_analyzer \
  --files "${FILE_ID}" \
  --json | jq -r '.job_spec_id')
echo "started: ${JOB_ID}"

echo
echo "── 4. Stream events until terminal (Ctrl-C exits 130) ──"
# NDJSON one event per line so jq can stream it.
convilyn goals events "${JOB_ID}" --json | while IFS= read -r line; do
  type=$(echo "${line}" | jq -r '.type')
  echo "  event: ${type}"
  case "${type}" in
    completed|failed|cancelled) break ;;
  esac
done

echo
echo "── 5. Final status snapshot ──"
convilyn goals status "${JOB_ID}" --json | jq '{status, progress}'

echo
echo "── Done ──"
