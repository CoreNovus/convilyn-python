"""``rag.open("rag.toml")``: a corpus built from one settings file.

The file names a store, an embedder, a reranker and a recipe; it never holds
a key, and its errors name the setting but never repeat a value from it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
import respx

from convilyn import providers, rag

DOCUMENT = "# Refunds\n\nrefund policy\n\n# Shipping\n\nshipping times\n"
WORDS = ("refund", "money", "shipping", "times", "policy")


def vector_of(text: str) -> list[float]:
    """A vector with one place per known word, so related texts meet."""
    lowered = text.lower()
    vector = [1.0 if word in lowered else 0.0 for word in WORDS]
    return vector if any(vector) else [0.1] * len(WORDS)


def write(directory: Path, text: str) -> Path:
    path = directory / "rag.toml"
    path.write_text(text, encoding="utf-8")
    return path


class Recorded:
    """A stand-in connector that records being closed."""

    def __init__(self, closed: list[str], name: str, *, fail: bool = False) -> None:
        self.model = "words"
        self._closed, self._name, self._fail = closed, name, fail

    def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
        if self._fail:
            raise ConnectionError("the service is down")
        return [vector_of(text) for text in texts]

    def rerank(
        self, query: str, documents: Sequence[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        return [(index, 1.0) for index in range(len(documents))]

    def close(self) -> None:
        self._closed.append(self._name)


class AsyncRecorded(Recorded):
    async def embed(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:  # type: ignore[override]
        return [vector_of(text) for text in texts]

    async def aclose(self) -> None:
        self._closed.append(self._name)


@pytest.fixture
def closed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the providers' factories with recorders; returns what was closed."""
    names: list[str] = []
    monkeypatch.setattr(providers, "embedder", lambda *a, **k: Recorded(names, "embedder"))
    monkeypatch.setattr(providers, "reranker", lambda *a, **k: Recorded(names, "reranker"))
    monkeypatch.setattr(providers, "aembedder", lambda *a, **k: AsyncRecorded(names, "embedder"))
    monkeypatch.setattr(providers, "areranker", lambda *a, **k: AsyncRecorded(names, "reranker"))
    return names


MODELS = """
[embed]
provider = "openai_compatible"
model = "m"

[rerank]
provider = "cohere_compatible"
model = "r"
"""


class TestBuildingFromAFile:
    def test_an_empty_file_opens_the_default_corpus(self, tmp_path: Path) -> None:
        with rag.open(write(tmp_path, "")) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = corpus.search("shipping", limit=1)

        assert [hit.passage.text for hit in result.hits] == ["shipping times"]

    def test_a_relative_store_path_is_read_from_the_file_s_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        config = write(tmp_path, '[store]\nkind = "sqlite"\npath = "kb.sqlite3"\n')

        with rag.open(config) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

        assert (tmp_path / "kb.sqlite3").exists()

    def test_an_absolute_store_path_is_kept(self, tmp_path: Path) -> None:
        index = tmp_path / "index" / "kb.sqlite3"
        index.parent.mkdir()
        config = write(tmp_path, f"[store]\nkind = 'sqlite'\npath = '{index.as_posix()}'\n")

        with rag.open(config) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

        assert index.exists()

    def test_the_recipe_table_sets_the_recipe(self, tmp_path: Path) -> None:
        config = write(tmp_path, "[recipe.search]\nlimit = 1\n")

        with rag.open(config) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = corpus.search("refund shipping")

        assert len(result.hits) == 1

    def test_the_file_s_connectors_reach_their_services(self, tmp_path: Path) -> None:
        def embeddings(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            data = [{"index": n, "embedding": vector_of(t)} for n, t in enumerate(texts)]
            return httpx.Response(200, json={"data": data, "model": "m"})

        def ranking(request: httpx.Request) -> httpx.Response:
            documents = json.loads(request.content)["documents"]
            results = [
                {"index": n, "relevance_score": 1.0 / (n + 1)} for n in range(len(documents))
            ]
            return httpx.Response(200, json={"results": results})

        config = write(
            tmp_path,
            MODELS.replace(
                'model = "m"', 'model = "m"\nbase_url = "https://embed.example.com/v1"'
            ).replace('model = "r"', 'model = "r"\nbase_url = "https://rank.example.com"'),
        )
        with respx.mock() as mock:
            mock.post("https://embed.example.com/v1/embeddings").mock(side_effect=embeddings)
            mock.post("https://rank.example.com/rerank").mock(side_effect=ranking)
            with rag.open(config) as corpus:
                corpus.add_markdown(DOCUMENT, source_id="handbook")

                result = corpus.search("money back", limit=1)

        assert (result.hits[0].passage.text, result.skipped) == ("refund policy", ())

    async def test_aopen_builds_async_connectors(self, tmp_path: Path, closed: list[str]) -> None:
        async with await rag.aopen(write(tmp_path, MODELS)) as corpus:
            await corpus.add_markdown(DOCUMENT, source_id="handbook")

            result = await corpus.search("money", limit=1)

        assert result.skipped == ()

    def test_settings_the_file_gives_are_passed_to_the_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def embedder(*args: object, **kwargs: object) -> Recorded:
            seen.append((args, kwargs))
            return Recorded([], "embedder")

        monkeypatch.setattr(providers, "embedder", embedder)
        config = write(
            tmp_path,
            '[embed]\nprovider = "openai"\nmodel = "m"\ndimensions = 5\ntimeout = 3\n',
        )

        rag.open(config).close()

        assert seen == [(("openai",), {"model": "m", "dimensions": 5, "timeout": 3.0})]


class TestClosing:
    def test_closing_the_corpus_closes_the_connectors_it_built(
        self, tmp_path: Path, closed: list[str]
    ) -> None:
        corpus = rag.open(write(tmp_path, MODELS))

        corpus.close()

        assert closed == ["embedder", "reranker"]

    async def test_closing_an_async_corpus_closes_the_connectors_it_built(
        self, tmp_path: Path, closed: list[str]
    ) -> None:
        corpus = await rag.aopen(write(tmp_path, MODELS))

        await corpus.aclose()

        assert closed == ["embedder", "reranker"]

    def test_a_failure_to_open_closes_the_connectors_it_built(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed: list[str]
    ) -> None:
        monkeypatch.setattr(
            providers, "embedder", lambda *a, **k: Recorded(closed, "embedder", fail=True)
        )

        with pytest.raises(ConnectionError):
            rag.open(write(tmp_path, MODELS))

        assert closed == ["embedder", "reranker"]

    def test_connectors_passed_in_are_not_closed(self, tmp_path: Path) -> None:
        names: list[str] = []

        with rag.open(write(tmp_path, ""), embed=Recorded(names, "mine")):
            pass

        assert names == []


class TestRefusals:
    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("[store\n", "is not valid TOML"),
            ("[stores]\n", r"unknown table \[stores\]"),
            ("store = 1\n", r"store must be a table, written \[store\]"),
            ('[store]\npath = "kb"\n', "store.kind must name a store"),
            ('[store]\nkind = "nosuch"\n', "store.kind must name a store"),
            (
                '[store]\nkind = "sqlite"\nfile = "kb"\n',
                "store.file: Extra inputs are not permitted",
            ),
            ('[embed]\nmodel = "m"\n', "embed.provider: Field required"),
            ("[recipe.search]\nlimt = 5\n", "recipe.search.limt: Extra inputs are not permitted"),
            ('[rerank]\nprovider = "p"\nmodel = "m"\nretry = 3\n', "rerank.retry: Extra inputs"),
        ],
        ids=[
            "not-toml",
            "unknown-table",
            "not-a-table",
            "store-without-kind",
            "unknown-store",
            "unknown-store-setting",
            "missing-provider",
            "misspelled-recipe-setting",
            "setting-only-code-can-give",
        ],
    )
    def test_a_file_that_does_not_describe_a_corpus_is_refused(
        self, tmp_path: Path, text: str, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            rag.open(write(tmp_path, text))

    @pytest.mark.parametrize(
        ("table", "name"),
        [
            ('[embed]\nprovider = "openai"\nmodel = "m"', "api_key"),
            ('[rerank]\nprovider = "cohere"\nmodel = "m"', "key"),
            ('[store]\nkind = "sqlite"', "password"),
            ("[store.auth]", "access_token"),
        ],
        ids=["api-key", "key", "password", "nested-token"],
    )
    def test_a_credential_in_the_file_is_refused_without_repeating_it(
        self, tmp_path: Path, table: str, name: str
    ) -> None:
        # Assembled from pieces: the planted value is no real credential, and
        # the source should not read as one to a secret scanner either.
        text = f'{table}\n{name} = "PLANTED-VALUE"\n'

        with pytest.raises(ValueError, match="credentials are never read from a file") as caught:
            rag.open(write(tmp_path, text))

        assert "PLANTED-VALUE" not in str(caught.value)

    def test_a_wrong_value_is_named_without_repeating_it(self, tmp_path: Path) -> None:
        text = '[embed]\nprovider = "openai"\nmodel = "m"\ndimensions = "PLANTED-VALUE"\n'

        with pytest.raises(ValueError, match="embed.dimensions") as caught:
            rag.open(write(tmp_path, text))

        assert ("PLANTED-VALUE" in str(caught.value), caught.value.__context__) == (False, None)

    def test_a_setting_named_like_a_count_of_tokens_is_not_a_credential(
        self, tmp_path: Path, closed: list[str]
    ) -> None:
        text = '[embed]\nprovider = "openai"\nmodel = "m"\nmax_input_tokens = 100\n'

        rag.open(write(tmp_path, text)).close()

        assert closed == ["embedder"]

    @pytest.mark.parametrize("piece", ["store", "recipe", "embed", "rerank"])
    def test_a_table_and_the_same_piece_as_an_argument_is_refused(
        self, tmp_path: Path, closed: list[str], piece: str
    ) -> None:
        tables = {
            "store": '[store]\nkind = "sqlite"\n',
            "recipe": "[recipe.search]\nlimit = 3\n",
            "embed": '[embed]\nprovider = "p"\nmodel = "m"\n',
            "rerank": '[rerank]\nprovider = "p"\nmodel = "m"\n',
        }
        arguments: dict[str, object] = {
            "store": rag.store("sqlite"),
            "recipe": rag.Recipe(),
            "embed": Recorded([], "mine"),
            "rerank": Recorded([], "mine"),
        }

        with pytest.raises(ValueError, match=rf"has \[{piece}\] and {piece}= was passed"):
            rag.open(write(tmp_path, tables[piece]), **{piece: arguments[piece]})  # type: ignore[arg-type]

    def test_a_missing_file_is_reported_as_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            rag.open(tmp_path / "absent.toml")

    def test_a_recipe_refuses_a_field_it_does_not_have(self) -> None:
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            rag.SearchOptions(limt=5)  # type: ignore[call-arg]


class TestReviewedEdges:
    """Inputs a security review tried against the first version."""

    def test_a_file_that_is_not_utf8_is_refused_without_its_content(self, tmp_path: Path) -> None:
        path = tmp_path / "rag.toml"
        path.write_bytes(b'[embed]\nprovider = "p"\nmodel = "PLANTED-VALUE"\n# caf\xe9\n')

        with pytest.raises(ValueError, match="not UTF-8 text") as caught:
            rag.open(path)

        shown = repr(caught.value) + repr(caught.value.args)
        assert ("PLANTED-VALUE" in shown, caught.value.__context__) == (False, None)

    def test_a_byte_order_mark_is_not_part_of_the_settings(self, tmp_path: Path) -> None:
        path = tmp_path / "rag.toml"
        path.write_bytes(b"\xef\xbb\xbf[recipe.search]\nlimit = 1\n")

        with rag.open(path) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

            assert len(corpus.search("refund shipping").hits) == 1

    @pytest.mark.parametrize(
        ("table", "name"),
        [
            ("[[store.path]]", "api_key"),
            ("[embed]", "apiKey"),
            ("[embed]", "API-KEY"),
            ("[store]", "Authorization"),
        ],
        ids=["inside-an-array-of-tables", "camel-case", "upper-case", "authorization"],
    )
    def test_a_credential_written_any_way_is_refused(
        self, tmp_path: Path, table: str, name: str
    ) -> None:
        text = f'{table}\n"{name}" = "PLANTED-VALUE"\n'

        with pytest.raises(ValueError, match="credentials are never read from a file"):
            rag.open(write(tmp_path, text))

    @pytest.mark.parametrize("value", ["123", "true", '["a", "b"]', '{ a = "b" }'])
    def test_a_store_path_that_is_not_text_is_refused(self, tmp_path: Path, value: str) -> None:
        with pytest.raises(ValueError, match="store.path"):
            rag.open(write(tmp_path, f'[store]\nkind = "sqlite"\npath = {value}\n'))

    def test_an_in_memory_path_is_kept_as_it_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        with rag.open(write(tmp_path, '[store]\nkind = "sqlite"\npath = ":memory:"\n')) as corpus:
            corpus.add_markdown(DOCUMENT, source_id="handbook")

        assert sorted(entry.name for entry in tmp_path.iterdir()) == ["rag.toml"]

    def test_settings_nested_too_deep_are_refused(self, tmp_path: Path) -> None:
        header = "[recipe." + ".".join(["a"] * 40) + "]\nx = 1\n"

        with pytest.raises(ValueError, match="nested more than 32 deep"):
            rag.open(write(tmp_path, header))

    def test_a_file_too_deep_for_the_parser_is_refused(self, tmp_path: Path) -> None:
        text = "x = " + "{ a = " * 2000 + "1" + " }" * 2000 + "\n"

        with pytest.raises(ValueError, match="nested too deeply to read"):
            rag.open(write(tmp_path, text))

    def test_an_unknown_provider_is_refused_without_repeating_its_name(
        self, tmp_path: Path
    ) -> None:
        text = '[embed]\nprovider = "PLANTED-VALUE"\nmodel = "m"\n'

        with pytest.raises(ValueError, match="embed.provider: no provider of that name") as caught:
            rag.open(write(tmp_path, text))

        assert "PLANTED-VALUE" not in str(caught.value)

    def test_a_provider_without_reranking_is_refused_for_rerank(self, tmp_path: Path) -> None:
        text = '[rerank]\nprovider = "gemini"\nmodel = "m"\n'

        with pytest.raises(ValueError, match="rerank.provider: no provider of that name"):
            rag.open(write(tmp_path, text))

    def test_a_connector_s_refusal_names_the_file_and_table(self, tmp_path: Path) -> None:
        text = '[embed]\nprovider = "openai_compatible"\nmodel = "m"\nbase_url = "http://example.com"\n'

        with pytest.raises(ValueError, match=r"rag\.toml: \[embed\]: ") as caught:
            rag.open(write(tmp_path, text))

        assert caught.value.__context__ is None

    def test_a_store_passed_in_is_closed_when_the_file_is_refused(self, tmp_path: Path) -> None:
        from convilyn.rag._stores.sqlite import SqliteStore

        closed: list[bool] = []

        class Watched(SqliteStore):
            async def close(self) -> None:
                closed.append(True)
                await super().close()

        with pytest.raises(ValueError, match="unknown table"):
            rag.open(write(tmp_path, "[stores]\n"), store=Watched())

        assert closed == [True]

    async def test_closing_runs_every_closer_even_when_one_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed: list[str]
    ) -> None:
        class Failing(AsyncRecorded):
            async def aclose(self) -> None:
                raise OSError("could not close")

        monkeypatch.setattr(providers, "aembedder", lambda *a, **k: Failing(closed, "embedder"))
        corpus = await rag.aopen(write(tmp_path, MODELS))

        with pytest.raises(OSError, match="could not close"):
            await corpus.aclose()

        assert closed == ["reranker"]
