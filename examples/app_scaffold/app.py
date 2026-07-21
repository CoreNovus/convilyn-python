"""Restartable consumer app — the shape real verticals start from.

Every run: reconcile pending jobs from previous runs, then submit new
work. Kill it any time; the next run picks up where it left off.

Usage:
    export CONVILYN_API_KEY=ck_...
    python app.py
"""

from __future__ import annotations

from app_session import DONE, FAILED, AppSession

from convilyn import Convilyn


def main() -> None:
    client = Convilyn()
    session = AppSession("~/.convilyn-app-scaffold/session.json")

    # 1. Reconcile jobs from previous runs (restart-safe).
    for tracked in session.pending():
        job = client.goals.retrieve(tracked.job_spec_id)
        if job.status == "completed":
            for artifact in client.goals.artifacts(tracked.job_spec_id):
                client.goals.download_artifact_to(
                    tracked.job_spec_id, artifact.artifact_id, f"./out/{artifact.filename}"
                )
            session.mark(tracked.job_spec_id, DONE)
        elif job.status == "failed":
            session.mark(tracked.job_spec_id, FAILED)

    # 2. Submit new work. Swap in a vertical you authored via the Builder
    #    (client.builder / web) — discover its id with
    #    client.user_workflows.list(), then run it:
    #    client.goals.run(user_workflow_id="uw_...", files=[...])
    job = client.goals.run(
        goal_text="Summarise the attached document into key decisions",
        files=["./inbox/example.pdf"],
    )
    session.track(job.job_spec_id)

    print(f"{len(session.pending())} job(s) pending; run me again to reconcile.")


if __name__ == "__main__":
    main()
