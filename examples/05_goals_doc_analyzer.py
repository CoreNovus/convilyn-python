"""AI workflow hello-world — synchronous, with HITL slot filling.

Runs an AI workflow (``doc_analyzer``) end-to-end from a sync
caller. If the agent stops to ask for clarification (``slots_pending``
status), this example answers the first pending slot with a stub
response and continues to terminal status.

Usage:
    export CONVILYN_API_KEY=ck_...
    python examples/05_goals_doc_analyzer.py path/to/file_id

The positional argument is a Convilyn ``file_id`` you previously
uploaded via ``client.files.upload(...)`` (see example 01).
"""

from __future__ import annotations

import sys

from convilyn import Convilyn, GoalJobFailedError


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: 05_goals_doc_analyzer.py <file_id>", file=sys.stderr)
        return 1
    file_id = sys.argv[1]

    with Convilyn() as client:
        job = client.goals.run(workflow_id="doc_analyzer", files=[file_id])

        # `run` returns at the first stopping condition. For HITL workflows
        # that means we may need to answer slots before re-waiting.
        while job.needs_input and job.pending_slots:
            slot = job.pending_slots[0]
            print(f"agent asks: {slot.question}")
            # Stub response: pick the first option, or echo the question.
            answer = slot.options[0] if slot.options else "no preference"
            job = client.goals.fill_slot(job.job_spec_id, slot_id=slot.slot_id, value=answer)
            # After every slot answer, server transitions back to executing.
            job = client.goals.confirm(job.job_spec_id)
            job = client.goals.wait(job.job_spec_id)

        if job.status == "completed":
            print(f"OK: {job.job_spec_id} ({job.progress}%)")
            return 0
        print(f"FAILED: status={job.status} msg={job.error_message}")
        return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GoalJobFailedError as exc:
        print(f"goal job failed: {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
