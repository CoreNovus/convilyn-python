"""``convilyn rag bench compare``: several rag.toml files against the first."""

from __future__ import annotations

import socket
from importlib import resources
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from convilyn import rag
from convilyn.cli.main import cli
from convilyn.rag import bench

BASE = '[store]\nkind = "sqlite"\npath = "kb.sqlite3"\n'


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shipped demo indexed once, with its questions beside it."""
    directory = tmp_path_factory.mktemp("compare")
    demo = resources.files("convilyn.rag.bench").joinpath("_demo")
    with rag.open(store=rag.store("sqlite", path=directory / "kb.sqlite3")) as opened:
        for entry in sorted(demo.iterdir(), key=lambda item: item.name):
            if entry.name.endswith(".md"):
                opened.add_markdown(entry.read_text(encoding="utf-8"), source_id=entry.name[:-3])
    (directory / "questions.jsonl").write_bytes(demo.joinpath("questions.jsonl").read_bytes())
    return directory


def config(directory: Path, name: str, extra: str = "") -> str:
    path = directory / f"{name}.toml"
    path.write_text(BASE + extra, encoding="utf-8")
    return str(path)


def compare(corpus: Path, *arguments: str) -> Result:
    questions = str(corpus / "questions.jsonl")
    return CliRunner().invoke(
        cli, ["rag", "bench", "compare", "--questions", questions, *arguments]
    )


WORSE = "\n[recipe.search]\nlimit = 1\n"


class TestCompare:
    def test_two_settings_exit_zero(self, corpus: Path, tmp_path: Path) -> None:
        result = compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", str(tmp_path)
        )

        assert result.exit_code == 0

    def test_a_worse_setting_is_named_worse_in_the_summary(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", str(tmp_path)
        )
        summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
        row = next(line for line in summary.splitlines() if line.startswith("| worse |"))

        assert "| **worse** |" in row

    def test_one_comparison_is_made_at_the_confidence_asked(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", str(tmp_path)
        )

        assert "at 95% confidence." in (tmp_path / "summary.md").read_text(encoding="utf-8")

    def test_three_settings_state_the_adjusted_confidence(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        compare(
            corpus,
            config(corpus, "base"),
            config(corpus, "worse", WORSE),
            config(corpus, "fewer", "\n[recipe.search]\nlimit = 3\n"),
            "--out",
            str(tmp_path),
        )

        assert "97.5% confidence each" in (tmp_path / "summary.md").read_text(encoding="utf-8")

    def test_each_variant_report_is_written_in_the_order_given(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", str(tmp_path)
        )
        second = bench.Report.model_validate_json((tmp_path / "report-2.json").read_bytes())

        assert second.metrics["recall@10"] == second.metrics["recall@1"]

    def test_the_verdict_of_each_variant_is_printed(self, corpus: Path, tmp_path: Path) -> None:
        result = compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", str(tmp_path)
        )

        assert result.output.startswith("worse: worse (")


class TestRefusals:
    def test_one_settings_file_is_a_usage_error(self, corpus: Path) -> None:
        result = compare(corpus, config(corpus, "base"))

        assert (result.exit_code, "at least two rag.toml files" in result.output) == (1, True)

    def test_two_files_with_one_name_are_a_usage_error(self, corpus: Path, tmp_path: Path) -> None:
        other = tmp_path / "base.toml"
        other.write_text(BASE.replace("kb.sqlite3", str(corpus / "kb.sqlite3").replace("\\", "/")))

        result = compare(corpus, config(corpus, "base"), str(other))

        assert (result.exit_code, "the same name" in result.output) == (1, True)

    def test_a_settings_file_that_cannot_be_read_is_a_usage_error(self, corpus: Path) -> None:
        result = compare(corpus, config(corpus, "base"), config(corpus, "bad", "\n[unknown]\n"))

        assert (result.exit_code, "unknown table" in result.output) == (1, True)

    def test_too_few_questions_to_compare_is_a_usage_error(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        lines = (corpus / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        few = tmp_path / "few.jsonl"
        few.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
        arguments = ["--questions", str(few), config(corpus, "base"), config(corpus, "w", WORSE)]

        result = CliRunner().invoke(cli, ["rag", "bench", "compare", *arguments])

        assert (result.exit_code, "a comparison needs at least" in result.output) == (1, True)

    def test_too_few_questions_are_refused_before_any_corpus_is_opened(
        self, corpus: Path, tmp_path: Path
    ) -> None:
        """A run can call paid services; the plan is checked first."""
        lines = (corpus / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        few = tmp_path / "few.jsonl"
        few.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
        first = config(tmp_path, "unopenable-1", "\n[unknown]\n")
        second = config(tmp_path, "unopenable-2", "\n[unknown]\n")

        result = CliRunner().invoke(
            cli, ["rag", "bench", "compare", "--questions", str(few), first, second]
        )

        assert "a comparison needs at least" in result.output

    def test_an_output_that_cannot_be_written_exits_3(self, corpus: Path, tmp_path: Path) -> None:
        blocker = tmp_path / "taken"
        blocker.write_text("", encoding="utf-8")

        out = str(blocker / "out")

        result = compare(
            corpus, config(corpus, "base"), config(corpus, "worse", WORSE), "--out", out
        )

        assert result.exit_code == 3


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any socket at all fails the test: an estimate opens nothing."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the estimate opened a connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    for name in ("OPENAI_API_KEY", "COHERE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


MODELS = (
    '\n[embed]\nprovider = "openai"\nmodel = "text-embedding-3-small"\n'
    '\n[rerank]\nprovider = "cohere"\nmodel = "rerank-v3.5"\n'
)


class TestEstimate:
    def test_an_estimate_counts_each_variants_calls(
        self, corpus: Path, tmp_path: Path, no_sockets: None
    ) -> None:
        result = compare(
            corpus, config(tmp_path, "base"), config(tmp_path, "models", MODELS), "--estimate"
        )

        assert result.output.splitlines()[:2] == [
            "base: no calls to a model service",
            f"models: 23 embedding calls (about {self.tokens(corpus):,} input tokens), "
            "23 reranking calls (at most 2,300 documents)",
        ]

    def test_an_estimate_writes_nothing(
        self, corpus: Path, tmp_path: Path, no_sockets: None
    ) -> None:
        out = tmp_path / "out"

        compare(
            corpus,
            config(tmp_path, "base"),
            config(tmp_path, "models", MODELS),
            "--estimate",
            "--out",
            str(out),
        )

        assert not out.exists()

    def test_an_estimate_opens_no_store(
        self, corpus: Path, tmp_path: Path, no_sockets: None
    ) -> None:
        compare(corpus, config(tmp_path, "base"), config(tmp_path, "models", MODELS), "--estimate")

        assert not (tmp_path / "kb.sqlite3").exists()

    def test_an_estimate_naming_an_unknown_provider_is_a_usage_error(
        self, corpus: Path, tmp_path: Path, no_sockets: None
    ) -> None:
        bad = '\n[rerank]\nprovider = "nobody"\nmodel = "m"\n'

        result = compare(
            corpus, config(tmp_path, "base"), config(tmp_path, "bad", bad), "--estimate"
        )

        assert (result.exit_code, "rerank.provider" in result.output) == (1, True)

    @staticmethod
    def tokens(corpus: Path) -> int:
        asked = bench.questions(corpus / "questions.jsonl")
        return sum(rag.estimate_tokens(q.question) for q in asked if q.status == "ready")
