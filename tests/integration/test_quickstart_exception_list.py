"""QUICKSTART's typed-exception tables are the exported exceptions, exactly.

`docs/QUICKSTART.md` §4 lists the types a caller can catch. Before this file it
was a hand-maintained sentence, and it had drifted in both directions at once:

* It named `UnsupportedRouteError` in a list introduced by
  `from convilyn import Convilyn, ConvilynError`. That name is **not** a
  top-level export — it lives in `convilyn.local`, and
  `from convilyn import UnsupportedRouteError` is an `ImportError`. A reader who
  wanted to handle "no such route" specifically, rather than catching
  `ConvilynError`, hit that on the first try.
* It listed 6 of the 14 exceptions the package actually exports, omitting
  `UnderstandUnavailableError`, `QuotaExceededError`, `PlanRequiredError`,
  `RateLimitError`, `GoalJobFailedError`, `GoalJobTimeoutError`, `S3UploadError`
  and `WebSocketError`.

A list nobody checks is a list that describes the version it was written
against. This file makes both directions fail: a new exception that is not
documented, and a documented name that is not an exception.

## Why `BaseException`, not "the name ends in Error"

`convilyn.__all__` holds **15** names ending in `Error` and only **14** of them
can be caught. `JobError` is a pydantic `BaseModel` — the type of
`ConvertJob.error`, a `code` and a `message` — and `except JobError` is a
`TypeError` at runtime.

That distinction is the reason this gate resolves each name and asks
`issubclass(obj, BaseException)`. A gate keyed on the *spelling* would have
demanded `JobError` be added to a list of things you can catch, which is how a
check ends up enforcing the defect it was written to prevent. The user report
that prompted this file made exactly that arithmetic error, counting 9 missing
exceptions where there are 8.

## Why HTML comment markers rather than prose scanning

The tables are delimited by `<!-- exceptions:cloud:begin -->` /
`:end` markers. A scanner that instead looked for "typed exception" prose or
swept every backticked token in the section would trip on the surrounding
paragraphs — which legitimately mention `ImportError`, `FileExistsError`,
`ValueError`, `TypeError` and `JobError` in order to say they are *not* part of
this taxonomy. `turbo-lane-cost-classes.md` §"Why this stopped being a grep
one-liner" is the standing lesson: a text scan trips over the document's own
explanation of itself. An explicit delimiter is a contract; a regex over prose
is a guess.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import convilyn
import convilyn.local

QUICKSTART = Path(__file__).parent.parent.parent / "docs" / "QUICKSTART.md"

#: A backticked identifier, optionally namespace-qualified: `AuthError` or
#: `convilyn.local.UnsupportedRouteError`. The digit class is load-bearing —
#: `S3UploadError` is a real export, and an `[A-Za-z]*` body silently skipped it
#: while the region looked complete to a reader.
_BACKTICKED = re.compile(r"`((?:convilyn\.local\.)?[A-Z][A-Za-z0-9]*Error)`")


def _documented(marker: str) -> set[str]:
    """The names inside one `<!-- exceptions:<marker>:begin/end -->` region."""
    text = QUICKSTART.read_text(encoding="utf-8")
    region = re.search(
        rf"<!-- exceptions:{marker}:begin.*?-->(.*?)<!-- exceptions:{marker}:end -->",
        text,
        re.DOTALL,
    )
    assert region is not None, f"QUICKSTART has no exceptions:{marker} region"
    return {name.removeprefix("convilyn.local.") for name in _BACKTICKED.findall(region.group(1))}


def _exported_exceptions(module: object) -> set[str]:
    """Exported names that a caller can actually put in an `except` clause."""
    return {
        name
        for name in getattr(module, "__all__", ())
        if isinstance(getattr(module, name, None), type)
        and issubclass(getattr(module, name), BaseException)
    }


# ── Non-vacuity — both operands must be non-empty, or every set comparison
#    below passes on nothing. Runs first, deliberately. ──────────────────────


class TestTheScanIsNotVacuous:
    def test_quickstart_is_readable(self) -> None:
        assert QUICKSTART.read_text(encoding="utf-8").strip()

    @pytest.mark.parametrize("marker", ["cloud", "local"])
    def test_each_region_documents_something(self, marker: str) -> None:
        assert _documented(marker)

    def test_the_package_exports_exceptions(self) -> None:
        assert len(_exported_exceptions(convilyn)) >= 10

    def test_the_local_namespace_exports_exceptions(self) -> None:
        assert len(_exported_exceptions(convilyn.local)) >= 3

    def test_the_name_filter_is_not_a_spelling_check(self) -> None:
        """`JobError` ends in `Error`, is exported, and is not catchable.

        If this ever stops being true the gate is still correct — but the
        docstring above, and QUICKSTART's own note about it, are not."""
        assert "JobError" in convilyn.__all__
        assert not issubclass(convilyn.JobError, BaseException)
        assert "JobError" not in _exported_exceptions(convilyn)


# ── The pin, both directions ─────────────────────────────────────────────────


class TestQuickstartMatchesTheExports:
    def test_cloud_region_matches_top_level_exports(self) -> None:
        assert _documented("cloud") == _exported_exceptions(convilyn)

    def test_local_region_matches_local_exports(self) -> None:
        assert _documented("local") == _exported_exceptions(convilyn.local)


class TestTheNamespacesAreNotInterchangeable:
    """The original defect: a `convilyn.local` name listed as a top-level one."""

    def test_local_only_names_are_not_importable_from_the_package(self) -> None:
        local_only = _exported_exceptions(convilyn.local) - _exported_exceptions(convilyn)
        assert local_only, "guard: this test says nothing if the namespaces have converged"
        for name in local_only:
            assert not hasattr(convilyn, name), (
                f"{name} became a top-level export; QUICKSTART still files it under "
                "convilyn.local, and the two must move together"
            )

    def test_the_local_region_qualifies_its_names(self) -> None:
        """Written `convilyn.local.X`, so a reader cannot copy a bare import."""
        text = QUICKSTART.read_text(encoding="utf-8")
        region = re.search(
            r"<!-- exceptions:local:begin.*?-->(.*?)<!-- exceptions:local:end -->",
            text,
            re.DOTALL,
        )
        assert region is not None
        bare = _BACKTICKED.findall(region.group(1))
        assert bare and all(name.startswith("convilyn.local.") for name in bare)
