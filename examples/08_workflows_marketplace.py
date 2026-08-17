"""Browse the community marketplace, fork, like, and publish.

Demonstrates the ``client.workflows`` resource:

* ``search`` — browse public workflows (no auth required)
* ``get``    — fetch a specific workflow
* ``fork``   — make a private copy of a public/curated workflow (Pro tier)
* ``publish`` — flip your own private workflow to public (Pro tier)
* ``like``   — toggle the like on a public workflow

Usage::

    export CONVILYN_API_KEY=ck_...
    python examples/08_workflows_marketplace.py
"""

from __future__ import annotations

from convilyn import Convilyn


def main() -> int:
    with Convilyn() as client:
        # 1. Browse the public listing (anonymous reads work too, but
        #    we attach the auth header so a single client instance can
        #    mix anonymous and authenticated calls).
        page = client.workflows.search(tag=None, sort="popular", limit=5)
        print(f"found {len(page.items)} workflows (cursor={page.cursor!r})")
        if not page.items:
            print("no public workflows yet; nothing to fork.")
            return 0

        # 2. Pick the top result, fetch the full detail row.
        top = page.items[0]
        print(f"  top: {top.name} (likes={top.stats.like_count})")
        detail = client.workflows.get(top.workflow_id)
        print(f"  fetched detail: spec_id={detail.spec_id}")

        # 3. Toggle a like — idempotent, returns the post-call state.
        liked = client.workflows.like(top.workflow_id)
        print(f"  like state: {liked.liked} (count={liked.like_count})")

        # 4. (Pro tier only) Fork the workflow into a private copy.
        #    Catch APIError to surface 402 / 409 from the backend
        #    gracefully — free-tier callers will fail this step.
        try:
            forked = client.workflows.fork(
                source_spec_id=detail.spec_id,
                name=f"My fork of {detail.name}",
            )
            print(f"  forked → {forked.workflow_id} ({forked.visibility})")
        except Exception as exc:
            # APIError or ConvilynError — print and continue.
            print(f"  fork skipped: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
