"""A durable app session in ~60 lines — copy into your project and own it.

The platform is the source of truth for job STATE
(``client.goals.retrieve``); your app still needs restart-safe job
MEMBERSHIP — "these are the jobs *I* submitted". This helper persists
that set in one JSON file, written atomically (tmp + ``os.replace``) so
a crash mid-write never truncates existing state.

Deliberately an example, not an SDK API: real apps quickly diverge here
(SQLite, keychain, per-tenant stores) — copy and adapt.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PENDING = "pending"
DONE = "done"
FAILED = "failed"


@dataclass(frozen=True)
class TrackedJob:
    job_spec_id: str
    status: str
    created_at: float


class AppSession:
    """Restart-safe record of the jobs this app submitted."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    def jobs(self) -> tuple[TrackedJob, ...]:
        rows = self._load().get("jobs", {})
        tracked = (
            TrackedJob(job_spec_id=jid, status=r["status"], created_at=r["created_at"])
            for jid, r in rows.items()
        )
        return tuple(sorted(tracked, key=lambda j: j.created_at))

    def pending(self) -> tuple[TrackedJob, ...]:
        return tuple(j for j in self.jobs() if j.status == PENDING)

    def track(self, job_spec_id: str) -> None:
        document = self._load()
        document["jobs"].setdefault(job_spec_id, {"status": PENDING, "created_at": time.time()})
        self._save(document)

    def mark(self, job_spec_id: str, status: str) -> None:
        document = self._load()
        document["jobs"][job_spec_id] = {**document["jobs"][job_spec_id], "status": status}
        self._save(document)

    def _load(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "jobs": {}}

    def _save(self, document: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
