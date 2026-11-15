"""The graphs example is run, not only parsed.

It is the starting point a reader copies to try a graph inside their own
application, so each of its three placements, the use-case turn, the fanout
question and the planned task have to produce what they show.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from convilyn import graphs

_EXAMPLE = Path(__file__).parent.parent.parent / "examples" / "12_graphs_in_your_app.py"
_REPLY = "Confirm the refund. Thank you."


@pytest.fixture(scope="module")
def example() -> SimpleNamespace:
    # run_path, not an import: an import would leave a bytecode folder beside
    # the examples, which the examples index then reports as an unlisted entry.
    return SimpleNamespace(**runpy.run_path(str(_EXAMPLE)))


class TestTheThreePlacements:
    def test_as_a_node_it_fills_the_result_and_leaves_the_host_s_key_alone(
        self, example: SimpleNamespace
    ) -> None:
        state = example.as_a_node_with_matching_keys()

        assert state == {"task": "Confirm the refund", "result": _REPLY, "customer": "A-17"}

    def test_renamed_it_writes_the_host_s_own_key(self, example: SimpleNamespace) -> None:
        state = example.as_a_node_with_your_own_keys()

        assert state == {"request": "Confirm the refund", "reply": _REPLY}

    def test_as_a_tool_it_answers_the_call_with_json(self, example: SimpleNamespace) -> None:
        reply = json.loads(example.as_a_tool_for_an_agent())

        assert reply == {"result": _REPLY, "accepted": True, "feedback": None, "rounds": 2}

    def test_the_script_prints_one_line_per_graph_and_per_demonstration(
        self, example: SimpleNamespace, capsys: pytest.CaptureFixture[str]
    ) -> None:
        demonstrations = 6
        example.main()

        lines = capsys.readouterr().out.splitlines()

        assert len(lines) == len(graphs.available()) + demonstrations


class TestTheUseCaseTurn:
    def test_it_routes_collects_and_answers_with_the_tool(self, example: SimpleNamespace) -> None:
        state = example.a_use_case_turn()

        assert state == {
            "outcome": "answered",
            "reply": "Your refund for A-17 has started.",
            "use_case_id": "refund",
            "candidates": ["refund"],
            "collected": {"refund": {"order_id": "A-17"}},
            "missing": [],
            "clarify_turns": 0,
        }


class TestTheFanoutQuestion:
    def test_it_asks_both_sources_and_combines_them_in_order(
        self, example: SimpleNamespace
    ) -> None:
        state = example.a_fanout_question()

        assert state == {
            "answer": "Refunds take five working days. A-17 was refunded yesterday.",
            "results": {
                "docs": "Refunds take five working days.",
                "orders": "A-17 was refunded yesterday.",
            },
        }


class TestThePlannedTask:
    def test_it_carries_out_each_step_in_order_and_answers_from_both(
        self, example: SimpleNamespace
    ) -> None:
        state = example.a_planned_task()

        assert state == {
            "answer": "A-17 was refunded yesterday. Refunds take five working days.",
            "done": [
                {"step": "look up the order", "result": "A-17 was refunded yesterday."},
                {"step": "check the refund policy", "result": "Refunds take five working days."},
            ],
            "answered": True,
        }
