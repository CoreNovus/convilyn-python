"""Every figure the README quotes must appear in the report it cites.

``docs/README.md`` is what PyPI renders, so its benchmark table is the most-read
quality claim this package makes. The figures come from
``docs/MEASURED-2026-09-08.md``, which ships in the same sdist.

That is two copies of a number, and this repo's standing answer to two copies is
a checker rather than a refactor: the README needs prose around the figures and
the report needs the full context, so neither can be generated from the other.
What must never happen is that they disagree — the failure mode is silent, since
a copy of a number cannot fail, it just stops being true.

**There were two published figure sets for the same corpus and the same `n`,
and that gap is now closed rather than filed.** The shipped report said NED
0.9634 / heading-tree F1 0.9091 (2026-08-28, doc-eval 0.2.0) while
`sdk/doc-eval/docs/sdk_launch_progress.md`'s P1 rows said 0.9664 / 1.0000
(2026-08-30, doc-eval 1.0.0). Quoting the newer pair here would have put a THIRD
set of numbers on the most-read surface, disagreeing with the report linked two
paragraphs below it, so the README quoted the older shipped report until the
disagreement was settled.

It was settled by measuring rather than by choosing: `doc_eval/a1/headings.py`
has one commit in its entire history, and 1.0.0 repointed `reading_order` and
ANLS only — so the heading metric never changed definition and the two figures
are comparable. The engine improved, in the 2026-08-29 conversion fixes. A fresh
offline run reproduced the 2026-08-30 pair exactly, so the README, the report it
cites, and the launch bar now agree on one set of numbers.

Not a cross-tree read, deliberately: both files live in this SDK. A test reaching
into `sdk/doc-eval/` would pass here and fail in the public mirror, which ships
`tests/` and runs its own CI without any sibling tree beside it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
README = REPO / "docs" / "README.md"
REPORT = REPO / "docs" / "MEASURED-2026-09-08.md"

#: Figures the README states, each of which must appear in the report.
#:
#: Written as (label, literal) rather than parsed out of the README's table, so
#: that deleting a row is a failure too. A scraper over whatever the README
#: happens to contain would go green on a table that had quietly emptied.
QUOTED_FIGURES: tuple[tuple[str, str], ...] = (
    ("normalised edit distance", "0.9664"),
    ("TEDS-Struct", "0.9333"),
    ("reading order", "1.0000"),
    ("real-world PDFs converted", "650"),
    ("pages with no text layer", "145"),
    ("multi-column reading order", "86.8%"),
    ("control reading order", "85.4%"),
)


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def report() -> str:
    return REPORT.read_text(encoding="utf-8")


def test_both_documents_exist_and_are_substantial(readme: str, report: str) -> None:
    """Boundary: every assertion below is containment, and containment in an
    empty string is the one way they could all pass having checked nothing."""
    assert len(readme) > 2000
    assert len(report) > 2000


def test_the_readme_has_the_benchmark_section(readme: str) -> None:
    """Boundary: the figures could all still be 'present' in a README that no
    longer shows them in a table anybody reads."""
    assert "## How good is the conversion?" in readme


@pytest.mark.parametrize(("label", "figure"), QUOTED_FIGURES)
def test_the_readme_states_the_figure(readme: str, label: str, figure: str) -> None:
    assert figure in readme, f"README no longer states {label} ({figure})"


@pytest.mark.parametrize(("label", "figure"), QUOTED_FIGURES)
def test_the_report_backs_the_figure(report: str, label: str, figure: str) -> None:
    assert figure in report, (
        f"README quotes {label} as {figure}, which is not in "
        f"{REPORT.name} — the report is the source, so change it there first"
    )


def test_the_readme_links_the_report_it_quotes(readme: str) -> None:
    """An absolute URL, not a relative path: PyPI renders this file outside the
    repository, where `docs/MEASURED-...md` resolves to nothing.
    """
    assert (
        "https://github.com/CoreNovus/convilyn-python/blob/main/docs/MEASURED-2026-09-08.md"
        in readme
    )


def test_the_report_name_and_the_link_agree(readme: str) -> None:
    """The report carries its measurement date in its filename on purpose, so a
    newer report cannot be mistaken for this one. That only works while the link
    names the file that actually exists.
    """
    linked = set(re.findall(r"docs/(MEASURED-\d{4}-\d{2}-\d{2}\.md)", readme))

    assert linked == {REPORT.name}


def test_the_readme_does_not_claim_a_head_to_head(readme: str) -> None:
    """The one claim these numbers cannot support.

    They are this package's scores on its own corpora. doc-eval's whole design
    argument is that a house metric is not comparable with a paper's figure, so
    a README advertising that tool must not quietly do the thing the tool exists
    to prevent.
    """
    section = readme.split("## How good is the conversion?", 1)[1]
    section = section.split("## It tells you what it can do", 1)[0]

    assert "head-to-head" in section, "the disclaimer that keeps this honest has been removed"
