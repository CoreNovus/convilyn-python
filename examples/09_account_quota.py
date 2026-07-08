"""Check your billing tier and pre-flight a workflow's cost before running it.

The `client.account` resource lets a caller ask two questions:

* What tier am I on? (`get_plan()`)
* Would a workflow with this tool palette fit my plan? (`get_quota()`)

Both are read-only. The quota result is the same verdict the
``/cost-preview`` backend endpoint returns to the in-app confirm modal,
so what the SDK sees is identical to what the UI shows.

Usage::

    export CONVILYN_API_KEY=ck_...
    python examples/09_account_quota.py
"""

from __future__ import annotations

from convilyn import Convilyn, PlanRequiredError, QuotaExceededError


def main() -> int:
    with Convilyn() as client:
        plan = client.account.get_plan()
        print(f"current tier: {plan.tier}")

        # Pre-flight check for a hypothetical doc-analysis workflow.
        estimate = client.account.get_quota(
            tools=["pdf-mcp:extract_text", "openai-mcp:summarise"],
            max_iterations=25,
        )

        usage_pct = (
            100 * estimate.estimated_micro_u / max(estimate.quota_check.threshold_micro_u, 1)
        )
        print(
            f"estimated cost: {estimate.estimated_micro_u} micro-U "
            f"(${estimate.estimated_usd:.4f}) — "
            f"{usage_pct:.1f}% of your {estimate.quota_check.tier} cap"
        )

        match estimate.quota_check.state:
            case "ok":
                print("✓ quota check passed — safe to run.")
                return 0
            case "soft_limit":
                print(
                    "⚠ soft limit hit — proceed with caution. "
                    f"top-up at {estimate.quota_check.upgrade_url}"
                )
                return 0
            case "quota_exceeded":
                print(
                    "✗ quota exceeded — running this workflow would "
                    f"be blocked. upgrade at {estimate.quota_check.upgrade_url}"
                )
                return 1

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PlanRequiredError as exc:
        print(f"plan upgrade required: {exc.message} ({exc.upgrade_url})")
        raise SystemExit(1) from exc
    except QuotaExceededError as exc:
        print(f"quota exceeded: {exc.estimated_micro_u}/{exc.threshold_micro_u} micro-U")
        raise SystemExit(2) from exc
