"""The default store: one SQLite file, or memory, and nothing to install.

Keyword search uses SQLite's FTS5 with its ``unicode61`` tokenizer, which
splits on spaces and punctuation. Chinese, Japanese and Korean are written
without spaces, so a run of those characters would be one enormous word. Before
text reaches the index, each such run is rewritten as its overlapping
two-character pieces; a query is rewritten the same way, so a two-character
query matches wherever those two characters are adjacent.

The built-in ``trigram`` tokenizer is not used for this: it returns nothing for
a query shorter than three characters, and two-character words are the common
case in all three languages.

Vectors are kept as 32-bit floats, one row per passage, and a vector search
compares the query with every stored vector that passes ``where=``: exact, and
suited to a corpus that fits on one machine (see ``docs/TOOLS.md`` for the
measured scale). That comparison uses numpy, which is imported only when a
vector is searched and comes with the ``convilyn[rag]`` extra.

SQLite blocks, so every operation runs in a worker thread, one at a time.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import numbers
import re
import sqlite3
import struct
import threading
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Final, TypeVar

from convilyn._internal.vectors import unit
from convilyn.rag.errors import StoreError
from convilyn.rag.types import (
    Condition,
    Hit,
    IndexEntry,
    IndexIdentity,
    Passage,
    Scalar,
    SourceInfo,
    StoreInfo,
)

T = TypeVar("T")

KIND: Final = "sqlite"

#: A run of characters written without spaces between words: kana, Han
#: ideographs and Hangul syllables.
_CJK_RUN = re.compile("[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿\U00020000-\U000323af]+")
#: A run of letters and digits in any script: what the tokenizer keeps as words.
_WORD_RUN = re.compile(r"\w+")
#: A query is cut to this many distinct terms. It bounds the work one search
#: can ask for; a query longer than this is a document, not a question.
_MAX_QUERY_TERMS: Final = 256

#: What the engine and the encoding of a value into it can raise: SQLite's own
#: errors, text it cannot encode, and a number it cannot hold or serialize.
_ENGINE_FAILURES: Final = (sqlite3.Error, UnicodeError, OverflowError, ValueError)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sources ("
    " source_id TEXT PRIMARY KEY, title TEXT, meta TEXT NOT NULL, passages INTEGER NOT NULL,"
    " digest TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS labels ("
    " source_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS labels_by_value ON labels (name, value, source_id)",
    "CREATE TABLE IF NOT EXISTS passages ("
    " id INTEGER PRIMARY KEY, passage_id TEXT NOT NULL, source_id TEXT NOT NULL,"
    " ordinal INTEGER NOT NULL, section TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS passages_by_source ON passages (source_id)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS terms USING fts5(body, tokenize = 'unicode61')",
    # One row per passage that has a vector; ``id`` is the passage's row.
    "CREATE TABLE IF NOT EXISTS vectors (id INTEGER PRIMARY KEY, data BLOB NOT NULL)",
)

#: Stored vectors are read and compared this many at a time, so that the
#: memory a search takes does not grow with the size of the index.
_VECTORS_PER_READ: Final = 4096


class SqliteStore:
    """Passages in one SQLite database. Create it with ``rag.store("sqlite", ...)``."""

    def __init__(self, *, path: str | Path | None = None) -> None:
        """``path`` names the database file; with none, the index lives in memory."""
        if path is not None and not str(path):
            # SQLite reads an empty name as a private database in a temporary
            # file, which is a place the caller did not name.
            raise ValueError("path must not be empty; leave it out to keep the index in memory")
        self._target = ":memory:" if path is None else str(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._closed = False
        #: The vector size of the index, once prepared; ``None`` when it holds none.
        self._dimensions: int | None = None

    def describe(self) -> StoreInfo:
        return StoreInfo(kind=KIND, cjk="overlapping two-character pieces")

    async def prepare(self, identity: IndexIdentity) -> None:
        await self._run(lambda db: _prepare(db, identity))
        self._dimensions = identity.dimensions

    async def write(self, source: SourceInfo, entries: Sequence[IndexEntry]) -> None:
        # Checked and scaled before the engine is touched. An entry scales its
        # vector when it is made, but a copy made without validation does not.
        vectors = [
            None if entry.vector is None else self._scaled(entry.vector) for entry in entries
        ]
        await self._run(lambda db: _write(db, source, entries, vectors))

    async def remove(self, source_id: str) -> bool:
        return await self._run(lambda db: _remove(db, source_id))

    async def sources(self) -> tuple[SourceInfo, ...]:
        return await self._run(_sources)

    async def vectors(self, source_id: str) -> dict[str, tuple[float, ...]]:
        if self._dimensions is None:
            return {}
        width = self._dimensions
        return await self._run(lambda db: _vectors(db, source_id, width))

    async def lexical(
        self, query: str, *, limit: int, where: Sequence[Condition] = ()
    ) -> tuple[Hit, ...]:
        _check_limit(limit)
        return await self._run(lambda db: _lexical(db, query, limit, where))

    async def dense(
        self, vector: Sequence[float], *, limit: int, where: Sequence[Condition] = ()
    ) -> tuple[Hit, ...]:
        _check_limit(limit)
        query = self._scaled(vector)
        numpy = _numpy()
        return await self._run(lambda db: _dense(db, numpy, query, len(query), limit, where))

    def _scaled(self, vector: Sequence[float]) -> tuple[float, ...]:
        """``vector`` scaled to length 1, refused if it does not fit this index.

        Scaled, every score is a cosine similarity and no component is large
        enough to overflow the 32-bit floats vectors are kept and compared in.
        """
        # Text and bytes would otherwise be read one number per character, and
        # a numeric string as the number it spells. Anything else a float can be
        # made from is a component: a caller's own numbers (numpy, Decimal, a
        # tensor's elements) rank equal to the built-in ones.
        if isinstance(vector, str | bytes | bytearray) or (
            isinstance(vector, memoryview) and vector.itemsize == 1
        ):
            raise TypeError("a vector is a sequence of numbers, not text or bytes")
        given = tuple(vector)
        if any(isinstance(value, str | bytes | bytearray) for value in given):
            raise TypeError("a vector is a sequence of numbers, not text or bytes")
        if any(_is_complex(value) for value in given):
            # Read as a float, a complex number would silently lose a part.
            raise TypeError("a vector is a sequence of real numbers, not complex ones")
        if self._dimensions is None:
            raise ValueError("this index holds no vectors: it was prepared without an embedding")
        if len(given) != self._dimensions:
            raise ValueError(
                f"a vector of {len(given)} numbers does not fit this index, "
                f"whose vectors have {self._dimensions}"
            )
        return unit(given)

    async def close(self) -> None:
        await asyncio.to_thread(self._close)

    def _close(self) -> None:
        with self._lock:
            self._closed = True
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    async def _run(self, work: Callable[[sqlite3.Connection], T]) -> T:
        return await asyncio.to_thread(self._locked, work)

    def _locked(self, work: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            if self._closed:
                raise StoreError("the store is closed")
            try:
                if self._connection is None:
                    self._connection = _connect(self._target)
                with self._connection:
                    return work(self._connection)
            except _ENGINE_FAILURES as error:
                raise StoreError("the SQLite store could not complete the operation") from error


def _is_complex(value: object) -> bool:
    """Whether ``value`` is a complex number, not a real one."""
    return isinstance(value, numbers.Complex) and not isinstance(value, numbers.Real)


def _check_limit(limit: int) -> None:
    """Refuse a ``limit`` that is not a count of hits."""
    if type(limit) is not int or limit <= 0:
        raise ValueError("limit must be a positive integer")


def _connect(target: str) -> sqlite3.Connection:
    """Open ``target``, set up so that nothing is written outside it."""
    connection = sqlite3.connect(target, check_same_thread=False)
    try:
        # Sorting may otherwise spill into a temporary file.
        connection.execute("PRAGMA temp_store = MEMORY")
    except BaseException:
        connection.close()
        raise
    return connection


def _prepare(db: sqlite3.Connection, identity: IndexIdentity) -> None:
    try:
        for statement in _SCHEMA:
            db.execute(statement)
    except sqlite3.OperationalError as error:
        if "fts5" in str(error).lower():
            raise StoreError(
                "this Python's SQLite was built without FTS5, which the SQLite store needs"
            ) from error
        raise
    wanted = identity.model_dump_json()
    row = db.execute("SELECT value FROM settings WHERE name = 'identity'").fetchone()
    if row is None:
        db.execute("INSERT INTO settings (name, value) VALUES ('identity', ?)", (wanted,))
    elif row[0] != wanted:
        raise StoreError(
            "this index was built with different settings and must be rebuilt: "
            "create a new store and add the documents again"
        )


def _write(
    db: sqlite3.Connection,
    source: SourceInfo,
    entries: Sequence[IndexEntry],
    vectors: Sequence[tuple[float, ...] | None],
) -> None:
    _remove(db, source.source_id)
    db.execute(
        "INSERT INTO sources (source_id, title, meta, passages, digest) VALUES (?, ?, ?, ?, ?)",
        (source.source_id, source.title, json.dumps(source.meta), len(entries), source.digest),
    )
    db.executemany(
        "INSERT INTO labels (source_id, name, value) VALUES (?, ?, ?)",
        [(source.source_id, name, json.dumps(value)) for name, value in source.meta.items()],
    )
    for entry, vector in zip(entries, vectors, strict=True):
        passage = entry.passage
        cursor = db.execute(
            "INSERT INTO passages (passage_id, source_id, ordinal, section, body, digest)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                passage.passage_id,
                passage.source_id,
                passage.ordinal,
                _section_json(passage.section),
                passage.text,
                passage.digest,
            ),
        )
        db.execute(
            "INSERT INTO terms (rowid, body) VALUES (?, ?)",
            (cursor.lastrowid, _spaced(entry.folded)),
        )
        if vector is not None:
            db.execute(
                "INSERT INTO vectors (id, data) VALUES (?, ?)",
                (cursor.lastrowid, _packed(vector)),
            )


def _vectors(db: sqlite3.Connection, source_id: str, width: int) -> dict[str, tuple[float, ...]]:
    """The stored vectors of ``source_id``'s passages, by digest."""
    rows = db.execute(
        "SELECT p.digest, v.data FROM passages p JOIN vectors v ON v.id = p.id"
        " WHERE p.source_id = ?",
        (source_id,),
    )
    found: dict[str, tuple[float, ...]] = {}
    for digest, data in rows:
        if not isinstance(data, bytes) or len(data) != 4 * width:
            raise StoreError("a stored vector is damaged; rebuild the index")
        found[digest] = struct.unpack(f"<{width}f", data)
    return found


def _section_json(section: tuple[str, ...]) -> str:
    """``section`` as stored: its characters as they are, where SQLite can hold them.

    Escaped, a Chinese character takes six bytes and a heading trail far more
    room than it needs. A trail holding a character UTF-8 cannot encode (a lone
    surrogate) is written escaped instead, which SQLite stores and reads back.
    """
    plain = json.dumps(section, ensure_ascii=False)
    try:
        plain.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(section)
    return plain


def _packed(vector: Sequence[float]) -> bytes:
    """``vector`` as little-endian 32-bit floats, the same on every machine."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _remove(db: sqlite3.Connection, source_id: str) -> bool:
    db.execute(
        "DELETE FROM terms WHERE rowid IN (SELECT id FROM passages WHERE source_id = ?)",
        (source_id,),
    )
    db.execute(
        "DELETE FROM vectors WHERE id IN (SELECT id FROM passages WHERE source_id = ?)",
        (source_id,),
    )
    db.execute("DELETE FROM passages WHERE source_id = ?", (source_id,))
    db.execute("DELETE FROM labels WHERE source_id = ?", (source_id,))
    return db.execute("DELETE FROM sources WHERE source_id = ?", (source_id,)).rowcount > 0


def _sources(db: sqlite3.Connection) -> tuple[SourceInfo, ...]:
    rows = db.execute(
        "SELECT source_id, title, meta, passages, digest FROM sources ORDER BY source_id"
    )
    return tuple(
        SourceInfo(
            source_id=source_id, title=title, meta=json.loads(meta), passages=count, digest=digest
        )
        for source_id, title, meta, count, digest in rows
    )


def _lexical(
    db: sqlite3.Connection, query: str, limit: int, where: Sequence[Condition]
) -> tuple[Hit, ...]:
    match = _match_expression(query)
    if not match:
        return ()
    clauses, arguments = _filtered(where)
    rows = db.execute(
        f"SELECT {_HIT_COLUMNS}, bm25(terms)"
        " FROM terms JOIN passages p ON p.id = terms.rowid"
        " JOIN sources s ON s.source_id = p.source_id"
        f" WHERE {' AND '.join(['terms MATCH ?', *clauses])}"
        " ORDER BY bm25(terms), p.source_id, p.ordinal LIMIT ?",
        [match, *arguments, limit],
    )
    # FTS5 reports a better match as a more negative number.
    return tuple(_hit(row, -row[-1]) for row in rows)


#: The columns :func:`_hit` reads, in its order.
_HIT_COLUMNS: Final = (
    "p.passage_id, p.source_id, p.ordinal, p.section, p.body, p.digest, s.title, s.meta"
)


def _hit(row: Sequence[Any], score: float) -> Hit:
    """Build the hit for a row that starts with :data:`_HIT_COLUMNS`."""
    passage_id, source_id, ordinal, section, body, digest, title, meta = row[:8]
    return Hit(
        passage=Passage(
            passage_id=passage_id,
            source_id=source_id,
            ordinal=ordinal,
            section=tuple(json.loads(section)),
            text=body,
            digest=digest,
        ),
        score=score,
        title=title,
        meta=json.loads(meta),
    )


def _filtered(where: Sequence[Condition]) -> tuple[list[str], list[Scalar]]:
    """The SQL clauses and arguments that keep passages of matching documents only."""
    clauses: list[str] = []
    arguments: list[Scalar] = []
    for condition in where:
        marks = ", ".join("?" for _ in condition.values)
        clauses.append(
            f"p.source_id IN (SELECT source_id FROM labels WHERE name = ? AND value IN ({marks}))"
        )
        arguments.append(condition.key)
        arguments.extend(json.dumps(value) for value in condition.values)
    return clauses, arguments


def refuse_vectors_it_cannot_search(store: object) -> None:
    """Raise :class:`StoreError` when ``store`` is this store and numpy is missing.

    Called before a corpus with an embedder embeds anything, so no embedding is
    paid for that a vector search here could never compare.
    """
    if isinstance(store, SqliteStore):
        _numpy()


def _numpy() -> Any:
    """Return numpy, which only a vector search needs.

    Its absence is a :class:`StoreError`, not an ``ImportError``: a corpus
    falls back to keyword search only when the query vector is refused, and a
    missing package is not that. The search is refused, and the message names
    the extra to install. A numpy that is installed but fails to load keeps
    its own error: installing the extra would not fix it.
    """
    try:
        return importlib.import_module("numpy")
    except ModuleNotFoundError as missing:
        if missing.name != "numpy":
            raise
        raise StoreError(
            'vector search needs numpy; install it with: pip install "convilyn[rag]"'
        ) from None


def _dense(
    db: sqlite3.Connection,
    numpy: Any,
    query: tuple[float, ...],
    dimensions: int,
    limit: int,
    where: Sequence[Condition],
) -> tuple[Hit, ...]:
    """Compare ``query`` with every stored vector that passes ``where``; keep the best.

    Rows are read a block at a time and in no particular order, so the engine
    never sorts the matching vectors and memory stays bounded however large
    the index. Equal scores are kept in the order the passages were written,
    the same on every call.
    """
    clauses, arguments = _filtered(where)
    condition = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = db.execute(
        f"SELECT v.id, v.data FROM vectors v JOIN passages p ON p.id = v.id{condition}",
        arguments,
    )
    target = numpy.asarray(query, dtype=numpy.float32)
    width = 4 * dimensions
    best_ids = numpy.empty(0, dtype=numpy.int64)
    best_scores = numpy.empty(0, dtype=numpy.float32)
    while rows := cursor.fetchmany(_VECTORS_PER_READ):
        if not all(isinstance(row[1], bytes) and len(row[1]) == width for row in rows):
            raise StoreError("a stored vector is damaged; rebuild the index")
        ids = numpy.fromiter((row[0] for row in rows), dtype=numpy.int64, count=len(rows))
        block = numpy.frombuffer(b"".join(row[1] for row in rows), dtype="<f4")
        scores = block.reshape(len(rows), dimensions) @ target
        if not numpy.isfinite(scores).all():
            raise StoreError("a stored vector is damaged; rebuild the index")
        best_ids, best_scores = _keep_best(
            numpy,
            numpy.concatenate((best_ids, ids)),
            numpy.concatenate((best_scores, scores)),
            limit,
        )
    if not len(best_ids):
        return ()
    order = numpy.lexsort((best_ids, -best_scores))
    chosen = [(int(best_ids[index]), float(best_scores[index])) for index in order]
    # One argument however many were chosen: SQLite caps the number of them.
    rows_by_id = {
        row[-1]: row
        for row in db.execute(
            f"SELECT {_HIT_COLUMNS}, p.id FROM passages p"
            " JOIN sources s ON s.source_id = p.source_id"
            " WHERE p.id IN (SELECT value FROM json_each(?))",
            (json.dumps([row_id for row_id, _ in chosen]),),
        )
    }
    # A passage removed between the comparison and this read is left out, as
    # keyword search leaves out what it can no longer join.
    return tuple(
        _hit(rows_by_id[row_id], score) for row_id, score in chosen if row_id in rows_by_id
    )


def _keep_best(numpy: Any, ids: Any, scores: Any, limit: int) -> tuple[Any, Any]:
    """Keep the ``limit`` best of ``scores``, ties going to the passage written first."""
    if len(ids) <= limit:
        return ids, scores
    order = numpy.lexsort((ids, -scores))[:limit]
    return ids[order], scores[order]


def _pieces(run: str) -> list[str]:
    """Return the overlapping two-character pieces of a run of CJK characters."""
    if len(run) == 1:
        return [run]
    return [run[index : index + 2] for index in range(len(run) - 1)]


def _spaced(folded: str) -> str:
    """Rewrite ``folded`` so that every CJK run is indexed as its pieces."""
    return _CJK_RUN.sub(lambda found: f" {' '.join(_pieces(found.group()))} ", folded)


def _match_expression(query: str) -> str:
    """Build the FTS5 expression that matches any term of ``query``.

    Every term is quoted, so nothing in the query is read as FTS5 syntax. A
    single CJK character matches the pieces that begin with it. Only the first
    distinct terms up to the bound are used, and the query is not read past
    the point where they have been found.
    """
    terms: dict[str, None] = {}
    for term in _query_terms(query):
        terms[term] = None
        if len(terms) == _MAX_QUERY_TERMS:
            break
    return " OR ".join(terms)


def _query_terms(query: str) -> Iterator[str]:
    """Yield the quoted terms of ``query`` in order, repeats included."""
    for found in _WORD_RUN.finditer(query):
        word = found.group()
        position = 0
        for run in _CJK_RUN.finditer(word):
            if run.start() > position:
                yield f'"{word[position : run.start()]}"'
            unspaced = run.group()
            if len(unspaced) == 1:
                yield f'"{unspaced}"*'
            for index in range(len(unspaced) - 1):
                yield f'"{unspaced[index : index + 2]}"'
            position = run.end()
        if position < len(word):
            yield f'"{word[position:]}"'
