"""Counting the calls to model services a bench run would make, before it runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.rag import bench, estimate_tokens
from convilyn.rag.bench._estimate import Calls, calls

QUESTIONS = (
    bench.Question(id="a", question="When does the shop open?"),
    bench.Question(id="b", question="書店幾點開門？"),
    bench.Question(id="c", question="Can I return a book?"),
    bench.Question(id="d", question="Not ready yet", status="draft"),
)
READY_TOKENS = sum(estimate_tokens(q.question) for q in QUESTIONS[:3])


def settings(directory: Path, text: str) -> Path:
    path = directory / "rag.toml"
    path.write_text('[store]\nkind = "sqlite"\npath = "kb.sqlite3"\n' + text, encoding="utf-8")
    return path


EMBED = '\n[embed]\nprovider = "openai"\nmodel = "text-embedding-3-small"\n'
RERANK = '\n[rerank]\nprovider = "cohere"\nmodel = "rerank-v3.5"\n'


class TestCounts:
    def test_keyword_search_makes_no_calls(self, tmp_path: Path) -> None:
        assert calls(settings(tmp_path, ""), QUESTIONS) == Calls(0, 0, 0, 0)

    def test_an_embedder_is_called_once_per_ready_question(self, tmp_path: Path) -> None:
        assert calls(settings(tmp_path, EMBED), QUESTIONS) == Calls(3, READY_TOKENS, 0, 0)

    def test_a_reranker_takes_the_candidates_in_one_call_each(self, tmp_path: Path) -> None:
        assert calls(settings(tmp_path, RERANK), QUESTIONS) == Calls(0, 0, 3, 300)

    def test_both_are_counted_together(self, tmp_path: Path) -> None:
        assert calls(settings(tmp_path, EMBED + RERANK), QUESTIONS) == Calls(
            3, READY_TOKENS, 3, 300
        )

    @pytest.mark.parametrize(
        ("max_documents", "per_question"),
        [(30, 4), (50, 2), (100, 1)],
        ids=["remainder", "exact-split", "exactly-one"],
    )
    def test_a_smaller_batch_splits_each_rerank_into_calls(
        self, tmp_path: Path, max_documents: int, per_question: int
    ) -> None:
        config = settings(tmp_path, RERANK + f"max_documents = {max_documents}\n")

        assert calls(config, QUESTIONS).rerank_calls == 3 * per_question

    def test_a_limit_above_the_candidates_sets_the_documents(self, tmp_path: Path) -> None:
        config = settings(tmp_path, RERANK + "\n[recipe.search]\nlimit = 150\n")

        assert calls(config, QUESTIONS).rerank_documents == 3 * 150

    def test_fewer_candidates_mean_fewer_documents(self, tmp_path: Path) -> None:
        config = settings(tmp_path, RERANK + "\n[recipe.search]\nlimit = 5\ncandidates = 20\n")

        assert calls(config, QUESTIONS).rerank_documents == 3 * 20

    def test_no_ready_questions_make_no_calls(self, tmp_path: Path) -> None:
        assert calls(settings(tmp_path, EMBED + RERANK), QUESTIONS[3:]) == Calls(0, 0, 0, 0)


class TestRefusals:
    def test_an_embed_provider_that_does_not_embed_is_refused(self, tmp_path: Path) -> None:
        config = settings(tmp_path, '\n[embed]\nprovider = "cohere"\nmodel = "m"\n')

        with pytest.raises(ValueError, match=r"embed\.provider: no provider of that name"):
            calls(config, QUESTIONS)

    def test_a_rerank_provider_that_does_not_rerank_is_refused(self, tmp_path: Path) -> None:
        config = settings(tmp_path, '\n[rerank]\nprovider = "openai"\nmodel = "m"\n')

        with pytest.raises(ValueError, match=r"rerank\.provider: no provider of that name"):
            calls(config, QUESTIONS)

    def test_the_refusal_does_not_repeat_the_provider_named(self, tmp_path: Path) -> None:
        config = settings(tmp_path, '\n[rerank]\nprovider = "PLANTED-NAME"\nmodel = "m"\n')

        with pytest.raises(ValueError) as caught:
            calls(config, QUESTIONS)

        assert "PLANTED-NAME" not in str(caught.value)

    @pytest.mark.parametrize(
        ("table", "setting", "value"),
        [
            (RERANK, "rerank.max_documents", 0),
            (RERANK, "rerank.max_documents", -5),
            (EMBED, "embed.max_inputs", 0),
            (EMBED, "embed.max_input_tokens", -1),
        ],
        ids=["zero-documents", "negative-documents", "zero-inputs", "negative-tokens"],
    )
    def test_a_limit_below_one_is_refused(
        self, tmp_path: Path, table: str, setting: str, value: int
    ) -> None:
        config = settings(tmp_path, table + f"{setting.split('.')[1]} = {value}\n")

        with pytest.raises(ValueError, match=rf"{setting}: Input should be greater than 0"):
            calls(config, QUESTIONS)

    def test_a_settings_file_rag_open_would_refuse_is_refused(self, tmp_path: Path) -> None:
        config = settings(tmp_path, "\n[unknown]\n")

        with pytest.raises(ValueError, match="unknown table"):
            calls(config, QUESTIONS)
