"""A corpus's settings read from one TOML file: ``rag.open("rag.toml")``.

The file has up to four tables::

    [store]                 # rag.store(kind, **options)
    kind = "sqlite"
    path = "kb.sqlite3"     # relative to the file

    [embed]                 # providers.embedder(provider, ...)
    provider = "openai"
    model = "text-embedding-3-small"

    [rerank]                # providers.reranker(provider, ...)
    provider = "cohere"
    model = "rerank-v4.0-pro"

    [recipe.search]         # rag.Recipe
    limit = 5

A file never holds a credential: a connector's key comes from the
environment variable its provider names, or the caller builds the connector
in code and passes it. The file is still trusted like code: it chooses where
that key is sent (``base_url``) and which files are opened (``path``).

Error messages name the file and the setting and never repeat a setting's
value (the TOML parser may quote the one character it stopped at). They are
raised outside the handler of the error that caused them, because that error
holds the values it was given and ``raise ... from None`` would still keep it
as ``__context__``.
"""

from __future__ import annotations

import inspect
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from convilyn.rag._models import Embedder, Reranker
from convilyn.rag._stores import _FACTORIES
from convilyn.rag._stores import store as create_store
from convilyn.rag._stores.protocol import Store
from convilyn.rag.recipe import Recipe

_TABLES = ("store", "embed", "rerank", "recipe")

# A setting named like a credential, matched on its words (see `_words`), so
# `api_key`, `apiKey`, `API-KEY` and `access_token` match and
# `max_input_tokens` and `tokenizer` do not.
_CREDENTIAL = re.compile(
    r"(?:^|_)(?:api_?key|key|token|secret|password|passwd|passphrase|credentials?"
    r"|auth|authorization|bearer|cookie)$"
)

# No setting is nested this deep; a file that is fails before it is walked.
_DEPTH = 32

_Section = TypeVar("_Section", bound=BaseModel)
_Built = TypeVar("_Built")


class _Embed(BaseModel):
    """The settings of ``providers.embedder`` a file may hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    base_url: str | None = None
    dimensions: int | None = None
    # Limits are positive here, not only when a connector is built, so a
    # file `--estimate` reads is refused exactly as `rag.open` refuses it.
    max_inputs: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    on_overflow: Literal["error", "truncate"] | None = None
    timeout: float | None = None


class _Rerank(BaseModel):
    """The settings of ``providers.reranker`` a file may hold."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    base_url: str | None = None
    max_documents: int | None = Field(default=None, gt=0)
    timeout: float | None = None


@dataclass(frozen=True)
class Settings:
    """What one file sets; ``None`` for a table it does not have."""

    store: tuple[str, dict[str, object]] | None
    embed: _Embed | None
    rerank: _Rerank | None
    recipe: Recipe | None


@dataclass(frozen=True)
class Parts:
    """The pieces a corpus is opened with, and how to close those built here."""

    store: Store | None
    recipe: Recipe | None
    embed: Embedder | None
    rerank: Reranker | None
    closers: tuple[Callable[[], object], ...]


def read(path: str | os.PathLike[str]) -> Settings:
    """The settings in the TOML file at ``path``.

    Raises ``ValueError`` for a file that is not UTF-8 TOML, an unknown table
    or setting, a value of the wrong kind, or anything named like a
    credential, at any depth.
    """
    file = Path(path)
    data = _parsed(file)
    for name in data:
        if name not in _TABLES:
            raise ValueError(
                f"{file}: unknown table [{name}]; "
                "the tables are [store], [embed], [rerank], [recipe]"
            )
        if not isinstance(data[name], dict):
            raise ValueError(f"{file}: {name} must be a table, written [{name}]")
    _refuse_credentials(file, data)
    return Settings(
        store=_store(file, data.get("store")),
        embed=_section(file, "embed", _Embed, data.get("embed")),
        rerank=_section(file, "rerank", _Rerank, data.get("rerank")),
        recipe=_section(file, "recipe", Recipe, data.get("recipe")),
    )


def parts(
    path: str | os.PathLike[str],
    *,
    asynchronous: bool,
    store: Store | None,
    recipe: Recipe | None,
    embed: Embedder | None,
    rerank: Reranker | None,
) -> Parts:
    """The file's pieces, with those passed as arguments for the tables it lacks.

    A table in the file and the same piece passed as an argument is refused:
    which one wins would be a guess. Embedders and rerankers are built
    ``async`` when ``asynchronous``. Nothing built here holds a connection
    yet (a store connects when prepared, a connector on its first request),
    so a failure part way through leaves nothing open.
    """
    file = Path(path)
    settings = read(file)
    given = {"store": store, "recipe": recipe, "embed": embed, "rerank": rerank}
    for name, value in given.items():
        if value is not None and getattr(settings, name) is not None:
            raise ValueError(f"{file} has [{name}] and {name}= was passed as well; use one")
    # Imported here: convilyn.providers imports convilyn.rag.
    from convilyn import providers  # noqa: PLC0415

    rows = providers.available()
    closers: list[Callable[[], object]] = []
    if settings.store is not None:
        kind, options = settings.store
        store = create_store(kind, **options)
    if settings.embed is not None:
        offering = tuple(row.name for row in rows if row.embed_wire is not None)
        if asynchronous:
            async_embedder = _built(file, "embed", providers.aembedder, settings.embed, offering)
            closers.append(async_embedder.aclose)
            embed = async_embedder
        else:
            sync_embedder = _built(file, "embed", providers.embedder, settings.embed, offering)
            closers.append(sync_embedder.close)
            embed = sync_embedder
    if settings.rerank is not None:
        offering = tuple(row.name for row in rows if row.rerank_wire is not None)
        if asynchronous:
            async_reranker = _built(file, "rerank", providers.areranker, settings.rerank, offering)
            closers.append(async_reranker.aclose)
            rerank = async_reranker
        else:
            sync_reranker = _built(file, "rerank", providers.reranker, settings.rerank, offering)
            closers.append(sync_reranker.close)
            rerank = sync_reranker
    return Parts(
        store=store,
        recipe=settings.recipe or recipe,
        embed=embed,
        rerank=rerank,
        closers=tuple(closers),
    )


def check_provider(file: Path, table: str, provider: str, offering: tuple[str, ...]) -> None:
    """Refuse a ``provider`` not in ``offering``, without repeating its name.

    The name is a value from the file; the refusal lists the known ones.
    """
    if provider not in offering:
        raise ValueError(
            f"{file}: {table}.provider: no provider of that name offers this; "
            f"choose one of: {', '.join(offering)}"
        )


def _parsed(file: Path) -> dict[str, Any]:
    raw = file.read_bytes()
    try:
        # `utf-8-sig`: a byte-order mark, as some Windows editors write, is not
        # part of the settings.
        return tomllib.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        # The decode error holds the whole file; only the position is kept.
        problem = f"it is not UTF-8 text (byte {error.start})"
    except tomllib.TOMLDecodeError as error:
        problem = f"it is not valid TOML: {error}"
    except RecursionError:
        problem = "it is nested too deeply to read"
    raise ValueError(f"{file}: {problem}")


def _words(name: str) -> str:
    """``name`` as lowercase words joined by ``_``: ``apiKey`` -> ``api_key``."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower())


def _refuse_credentials(file: Path, data: Mapping[str, object]) -> None:
    """Refuse any setting named like a credential, in tables and arrays alike."""
    pending: list[tuple[tuple[str, ...], object]] = [((), data)]
    while pending:
        where, value = pending.pop()
        if len(where) > _DEPTH:
            raise ValueError(f"{file}: settings are nested more than {_DEPTH} deep")
        if isinstance(value, dict):
            for key, inner in value.items():
                place = (*where, str(key))
                if _CREDENTIAL.search(_words(str(key))):
                    raise ValueError(
                        f"{file}: {'.'.join(place)}: credentials are never read from a file; "
                        "set the environment variable the provider names, or build the "
                        "connector in code and pass it"
                    )
                pending.append((place, inner))
        elif isinstance(value, list):
            pending.extend(((*where, str(index)), inner) for index, inner in enumerate(value))


def _store(file: Path, table: Mapping[str, object] | None) -> tuple[str, dict[str, object]] | None:
    if table is None:
        return None
    options = dict(table)
    kind = options.pop("kind", None)
    if not isinstance(kind, str) or kind not in _FACTORIES:
        known = ", ".join(sorted(_FACTORIES))
        raise ValueError(f'{file}: store.kind must name a store, such as kind = "sqlite" ({known})')
    checked = _section(file, "store", _store_settings(kind), options)
    assert checked is not None
    values: dict[str, object] = checked.model_dump(exclude_unset=True)
    path = values.get("path")
    if isinstance(path, str) and path not in ("", ":memory:") and not Path(path).is_absolute():
        # Read relative to the file, so a file next to its index finds it
        # from any working directory.
        values["path"] = str(file.parent / path)
    return kind, values


def _store_settings(kind: str) -> type[BaseModel]:
    """A model of the settings the store ``kind`` takes, typed as it declares them."""
    parameters = inspect.signature(_FACTORIES[kind], eval_str=True).parameters.values()
    fields: dict[str, Any] = {
        parameter.name: (
            parameter.annotation,
            ... if parameter.default is inspect.Parameter.empty else parameter.default,
        )
        for parameter in parameters
    }
    return create_model(  # type: ignore[call-overload]
        f"{kind}_store_settings", __config__=ConfigDict(extra="forbid"), **fields
    )


def _section(
    file: Path, name: str, model: type[_Section], table: Mapping[str, object] | None
) -> _Section | None:
    if table is None:
        return None
    try:
        return model.model_validate(table)
    except ValidationError as error:
        first = error.errors(include_input=False, include_url=False)[0]
        place = ".".join(str(part) for part in (name, *first["loc"]))
        message = f"{file}: {place}: {first['msg']}"
    raise ValueError(message)


def _built(
    file: Path,
    table: str,
    factory: Callable[..., _Built],
    section: _Embed | _Rerank,
    offering: tuple[str, ...],
) -> _Built:
    """The connector ``section`` describes, with any refusal naming the file.

    The provider is checked here, so that its name, a value from the file,
    is never repeated by the provider's own refusal.
    """
    check_provider(file, table, section.provider, offering)
    options = section.model_dump(exclude={"provider"}, exclude_none=True)
    try:
        return factory(section.provider, **options)
    except (ValueError, TypeError) as error:
        # The connectors' refusals are fixed text that quotes no value.
        message = f"{file}: [{table}]: {error}"
    raise ValueError(message)
