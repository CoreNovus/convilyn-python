# Agent tools — the shape they share

`convilyn` ships toolkits for building agents. Each lives in its own namespace
(`convilyn.rag` is the first) and all of them are built to the same shape, so
that what you learn using one carries over to the next. This page is that shape.

## You decide how a tool is used

A toolkit is a set of parts, not a fixed pipeline.

- **Every stage is a function you can call on its own.** `rag.split()` does not
  need a store, and nothing forces you through a convenience object to reach a
  stage. `rag.open()` returns a corpus that composes the stages for you; it does
  nothing you could not do with them directly.
- **Your function ranks equal to the built-in.** Wherever a tool needs a piece
  of behaviour that could reasonably be yours — a length measure, a store — you
  pass it as an argument, and the built-in is just the value used when you pass
  nothing.
- **Where your data lives is yours to decide.** Nothing is written to a path
  you did not name: with no path, an index lives in memory and is gone when you
  close it.
- **Nothing runs a model on your machine by default.** A tool that needs an
  embedding or a language model calls the service you choose, with your key, or
  the function you supply. No default path downloads or loads a model.
- **The connection is yours.** Anything that calls a service accepts your own
  `httpx` client, so a proxy, mutual TLS, request signing and rotating
  credentials are things you set up, not things you wait for us to support.
- **A limit is a default, not a gate.** What a service is known to accept is
  data you can read (`providers.available()`) and override per call site. A
  model the data has never heard of is allowed through with the service's
  defaults; there is no list of approved models.
- **A language or model the tool has no preset for is not refused.** You lose
  the preset, not the tool: supply the options yourself.

## One way to do each thing

- **Data and functions are kept apart.** Tunable parameters are frozen models
  that can be written to a file and compared (`rag.Recipe`). Functions are
  arguments and are never stored inside them.
- **Pure computation is a plain function.** Anything that waits on a disk or
  a network is `async` first and has a synchronous form beside it, like the
  rest of this SDK: `rag.aopen()` and `AsyncCorpus`, `rag.open()` and `Corpus`.
- **A thing that comes in kinds is made by one factory.** `rag.store(kind, ...)`
  creates one, and `rag.stores()` lists the kinds as data. One kind ships
  today, `sqlite`; the contract tests it passes are the ones a further kind
  would have to pass.
- **Errors are `ConvilynError`s.** A mistake in an argument raises a built-in
  `ValueError` or `TypeError`, as everywhere else in the package. What goes
  wrong at run time raises `rag.RagError` (a store raises `rag.StoreError`),
  with a fixed message that never carries your documents, queries or paths.
- **What is saved says how it was made.** An index records the settings it was
  built with and refuses to open under others, telling you to rebuild, instead
  of returning worse results quietly.
- **A leading underscore means internal.** Import from the namespace itself
  (`from convilyn import rag`), never from a module whose name starts with `_`.

## Where a default comes from

A default is only as good as the evidence behind it, so every default says
which kind it has:

| Grade | Meaning |
|---|---|
| **measured** | We ran the experiment and kept the result. |
| **published** | Taken from a standard or a published source, which is cited. |
| **derived** | Computed from a measured or published value. Not verified on its own. |

A derived default is a starting point. Measure on your own documents before
relying on it, and expect it to change once we have measured it too — which is
what "provisional" in [STABILITY.md](STABILITY.md) allows.

## `convilyn.rag` today

| Default | Value | Grade | Basis |
|---|---|---|---|
| Passage size (target / max / min) | 384 / 512 / 128 | measured for Traditional Chinese counted with the `qwen3` tokenizer | In our own experiment, not included in this package: retrieval on Traditional Chinese corpora, where other sizes and fixed-length cutting scored lower |
| No overlap; sections open at heading levels 1–3 | — | part of that measured configuration, not varied on their own | The same experiment of our own, not included in this package |
| Token estimate, characters per token | per tokenizer and script, and per language when one is named | measured | In our own experiment, not included in this package: one parallel document in 15 languages, counted with each tokenizer, with its line breaks and indentation taken out |
| Token estimate, whitespace | under `qwen3`, `cl100k` and the cautious estimate: one token per 80 spaces, 12 line breaks or 4 Windows line endings in a row, one per tab, three per typographic space; a single space between words is free. Under `xlm_r`: nothing | measured | In our own experiment, not included in this package: runs of 1 to 6,000 whitespace characters counted with each tokenizer. `xlm_r` folded every run into the next word. Of 60,000 random mixtures of kinds, fewer than 1 in 1,000 were counted one token short and none by more |
| Passage size for a language or tokenizer other than the measured one | see Languages below | derived | The measured sizes converted to the same amount of content |
| Sentence-ending marks `。！？｡`, and `.!?` before whitespace | — | published | Unicode `Sentence_Break` property |
| `；` also ends a sentence | — | part of the measured configuration | Same experiments |
| Passages a search returns | 10 | derived | The cutoff the standard retrieval benchmarks report at; not measured for usefulness |
| Candidates each search contributes, and the reranker reorders (`SearchOptions.candidates`) | 100 | measured | In our own experiment, not included in this package: the same Traditional Chinese experiments as the fusion defaults, where 150 scored slightly lower (nDCG@10 −0.004) and took half again as long |
| A query that cannot be embedded, or a reranker that fails | the search answers by keyword, or in fused order, and `SearchResult.skipped` says so | derived | An answer from the steps that worked, labelled, serves better than none; not compared with failing the search |
| How `rag.fuse` combines a keyword and a vector ranking | `min_max`: each ranking's scores scaled to 0–1, then averaged | measured | In our own experiment, not included in this package: retrieval on three Traditional Chinese test sets (986 questions), without a reranker: nDCG@10 0.904 against 0.871 for reciprocal rank fusion; z-score scaling tied; L2 scaling and a geometric mean were worse |
| `rag.fuse` weights | equal | measured | In the same experiment of our own, not included in this package: 0.5 / 0.5 beat 0.3 / 0.7 and 0.7 / 0.3 (nDCG@10 −0.019 and −0.023, both significant). No finer grid was tried, and the comparison was not repeated with a reranker in place |
| `rrf_k` under `method="rrf"` | 60 | published | The constant of the paper that introduced reciprocal rank fusion (Cormack, Clarke and Büttcher, SIGIR 2009); not tuned here |
| A passage one ranking does not hold | earns nothing from that ranking | derived | The usual reading of score fusion; not compared with alternatives |
| `rag.merge_adjacent` | neighbouring passages of one document joined, gap of none, no size limit, score of the best-ranked | derived | A choice, not measured: passages do not overlap, and joining restores the context a cut separated. Whether it helps answers has not been measured |
| Keyword search: any word of the query may match, best match first | BM25 with the engine's own constants | published | SQLite FTS5 documentation |
| Chinese, Japanese and Korean text in the SQLite store | indexed as overlapping two-character pieces | measured | In our own experiment, not included in this package: SQLite's built-in trigram tokenizer returned nothing for two-character queries; overlapping pairs matched them |
| Terms read from one query | the first 256 distinct | derived | A bound on the work one search can ask for, not a tuned value |
| A passage's `section` (its heading trail) | each heading or title at most 256 characters, the whole trail at most 512 with the outer headings shortened first; control characters read as spaces, and then runs of spaces closed up; a cut name ends in `…`. A heading longer than 256 characters is also kept whole as the first text of its section; one shortened only to fit a deep trail is not | derived | Every passage carries its trail, so a bound keeps what one document makes a store write in proportion to the document. Real trails run to a few dozen characters; not a tuned value |

A size is an amount of content, and it is only meaningful together with the
ruler it was chosen with. That is why `SplitOptions` carries `tokenizer` and
`language` next to the three sizes, and why there is a preset per language:

```python
recipe = rag.preset("zh-Hant")  # the measured setting, exactly
recipe = rag.preset("ja", tokenizer="xlm_r")  # the same content, in your model's tokens
rag.split(markdown, source_id="handbook", options=recipe.split)
```

Calling `rag.split()` with no options keeps the three default numbers but
counts with the cautious estimate, which gives Chinese text about twice as many
tokens as the measured setting did. The passages are then about half as long as
the measured ones: safe for any model, and not the configuration that was
measured. Use the preset when you want that configuration.

How close the estimate is, measured against the real tokenizers on the
parallel document as published, line breaks and indentation included: the
cautious estimate gave between 1.00 and 1.23 times the largest true count for
Chinese, Japanese, Korean, Spanish, French, German, Portuguese, Russian and
Indonesian, and 1.58 times for English, the sparsest of the Latin-script
languages. With the tokenizer and the language both named it gave between 0.97
and 1.09 times the true count. On Python source it gave 1.06 to 1.09 times the
largest true count, and on text that is mostly indentation or blank lines 1.1
to 1.6 times: whitespace is no longer free, so a passage of such text cannot
grow past what a model accepts.

Two things follow from the token measurements and are worth knowing before you
pick sizes:

- **The tokenizer matters more than the language.** The same content cost up to
  3.7 times as many tokens under one tokenizer as under another. A size chosen
  for one embedding model is not the right size for a different one; name your
  tokenizer with `rag.token_estimator(...)` or pass your own measure.
- **No published study recommends a passage size per language.** The controlled
  comparisons that exist are on English corpora and disagree with each other by
  dataset. Sizes for languages other than Traditional Chinese are therefore
  derived, not measured.

### Ranking

Two steps work on hits, whatever produced them, and need no store:

```python
fused = rag.fuse(keyword_hits, vector_hits)  # one ranking, best first
fused = rag.fuse(a, b, options=rag.FuseOptions(method="rrf", weights=(1, 2)))
answer = rag.merge_adjacent(fused[:20])  # neighbours of one document joined
```

`rag.fuse` recognises a passage across rankings by its `passage_id`. Scores
from different rankings are not on one scale — a keyword score and a
similarity are different kinds of number — so `min_max` puts each ranking on
0–1 before averaging, and `rrf` ignores the scores and reads the ranks. Ties
are broken by the best rank a passage had in any ranking, so a call always
returns the same order. The settings are `Recipe.fuse`, so an experiment
written to a file reproduces its ranking.

`rag.merge_adjacent` joins only what it is given. Pass it the hits you will
use (the top 20, say), not a whole candidate list: a long run of neighbours
becomes one long hit.

### Hybrid search

Give `rag.open` an embedder, and optionally a reranker, and a corpus does the
whole pipeline:

```python
embed = providers.embedder("openai", model="text-embedding-3-small")
rerank = providers.reranker("cohere", model="rerank-v4.0-pro")
with rag.open(store=rag.store("sqlite", path="kb.sqlite3"), embed=embed, rerank=rerank) as corpus:
    corpus.add_markdown(markdown, source_id="handbook")
    result = corpus.search("how long do refunds take")
    result.skipped  # () when every step ran
```

The connectors in this example are described under `convilyn.providers` below,
including how far they have been tested.

- **Adding** embeds the passages as documents. A passage whose text is
  unchanged since the document was last added keeps its stored vector and is
  not sent again; that is decided by `Passage.digest`, so a passage whose
  digest is not the SHA-256 of its text is refused. A failure here is raised:
  a document stored without its vectors would be found by keyword only,
  silently.
- **Searching** embeds the query as a query, takes the best
  `SearchOptions.candidates` passages from keyword search and from vector
  search, fuses them by `Recipe.fuse`, lets the reranker reorder them, and
  returns `limit`. A hit's score is the reranker's when one ran, otherwise the
  fused score.
- **When a step fails**, the search still answers: if the query cannot be
  embedded, by keyword alone; if the reranker fails or answers with something
  that does not fit, in the fused order. `SearchResult.skipped` names what was
  left out (`"embed"`, `"rerank"`), and a `RuntimeWarning` names the kind of
  error (and a provider's category, such as `auth`), never its message. A
  failure of the store itself is raised. Opening a corpus with an embedder
  over the SQLite store without numpy installed raises `rag.StoreError`
  naming `pip install "convilyn[rag]"` before any text is embedded, so no
  embedding is paid for that could not be searched.
- **The index records the model.** Opening embeds one short text to learn the
  vector size; an index built by one model is refused under another (or with
  none), telling you to rebuild.

`embed` is anything with a `model` name and an `embed(texts, *, kind)` method
returning one vector per text (`rag.Embedder`); `rerank` is anything with
`rerank(query, documents, *, top_n=None)` returning `(position, score)` pairs,
best first (`rag.Reranker`). The connectors in `convilyn.providers` are both,
and so is an object of your own around a local model or another service. A
synchronous method runs in a worker thread; an `async` one is awaited, so
async models go with `rag.aopen` (the synchronous `rag.open` refuses them: its
private event loop closes with the corpus and would strand their connections).
`rag.open` recognises a method declared `async def`; a plain method that
returns an awaitable is not recognised, and is awaited on that private loop.
The corpus calls them and never closes them.

### One settings file

`rag.open("rag.toml")` (and `rag.aopen`) builds the whole corpus from one TOML
file, so an experiment and production read the same settings:

```toml
[store]                  # rag.store(kind, **settings)
kind = "sqlite"
path = "kb.sqlite3"      # a relative path is read from the file's directory

[embed]                  # providers.embedder(provider, ...)
provider = "openai"
model = "text-embedding-3-small"
dimensions = 1024

[rerank]                 # providers.reranker(provider, ...)
provider = "cohere"
model = "rerank-v4.0-pro"

[recipe.search]          # rag.Recipe, table by table
limit = 5
```

- **Keys never go in the file.** Each connector reads the environment
  variable its provider names (`providers.available()` lists them). A setting
  named like a credential (`api_key`, `apiKey`, `token`, `secret`, `password`,
  `authorization`, …, in any table or array) is refused, and no error message
  repeats a value from the file.
- **Trust the file like code.** It holds no key, but it chooses where your
  provider's key is sent (`base_url`) and which files are opened (`path`).
  Load only a file written by whoever writes your application, never one
  from a user, a tenant or an upload.
- `[embed]` takes `provider`, `model`, `base_url`, `dimensions`, `max_inputs`,
  `max_input_tokens`, `on_overflow`, `timeout`; `[rerank]` takes `provider`,
  `model`, `base_url`, `max_documents`, `timeout`. A retry policy or an HTTP
  client is code: build the connector yourself and pass it.
- `rag.open` builds synchronous connectors and `rag.aopen` async ones; the
  corpus built them, so closing it closes them.
- A piece passed as an argument fills a table the file does not have
  (`rag.open("rag.toml", embed=my_embedder)`). A table and the same piece
  passed as an argument is refused rather than one silently winning.
- Unknown tables and settings are refused by name, here and in `rag.Recipe`,
  so a misspelled setting is an error, not a default.

### Stores

A store keeps passages and answers searches. Cutting, folding text and ranking
happen in this SDK, not in the store, so the same recipe behaves the same on
every store and a result measured on one carries to the next.

| Kind | Where the index lives | Needs | Chinese, Japanese, Korean |
|---|---|---|---|
| `sqlite` | A file you name, or memory | Nothing beyond Python for keyword search; `pip install "convilyn[rag]"` (numpy) for vector search | Overlapping two-character pieces: a query of two characters or more matches wherever they are adjacent. A one-character query matches only where that character starts a piece, so `貓` finds `貓很好` but not `我的貓` |

Every store answers two kinds of search: by keyword (`lexical`) and by vector
(`dense`), the latter returning the passages whose vectors are closest to the
query's, scored by cosine similarity: stored vectors and the query are both
scaled to length 1, whatever length they arrive with, and a vector of zeros is
refused. An index that holds vectors records the
embedding model and the vector size it was built with (`IndexIdentity`), and
refuses to open under another, telling you to rebuild: vectors from two models
cannot be compared.

The SQLite store searches vectors exactly, comparing the query with every
stored vector that passes `where=`, so the time a search takes grows in
proportion to the number of passages. In our own experiment on one development
machine (not included in this package), with 1024-number vectors, that was
about 6 ms per thousand passages (about 0.2 s at 30,000). No store with an
approximate index ships today; for a corpus too large to compare one by one,
implement `rag.Store` over an engine that has one (see below). Vectors are read
a block at a time, so the memory a search takes does not grow with the index.

`where=` filters on the labels you attached with `meta=`. It has three forms
and no others, because these are the ones every store can honour the same way:
a value (`{"lang": "en"}`), any of several (`{"lang": ["en", "ja"]}`), and
several labels at once, all of which must hold. A value matches only a value
of the same type: the number `1` is not the string `"1"`.

To use your own storage, implement `rag.Store` and pass the object to
`rag.open(store=...)`. The index is replaced a whole document at a time: adding
a `source_id` again removes its previous passages.

### Languages

`rag.languages()` returns this table as data. Sizes are target / max / min, in
tokens of the tokenizer named in the column; `rag.preset(language)` with no
tokenizer uses the `qwen3` column.

| Code | Language | Sizes | `qwen3` | `xlm_r` | `cl100k` |
|---|---|---|---|---|---|
| `zh-Hant` | Chinese (Traditional) | measured | 384 / 512 / 128 | 382 / 509 / 127 | 752 / 1003 / 251 |
| `zh-Hans` | Chinese (Simplified) | derived | 351 / 468 / 117 | 386 / 515 / 129 | 672 / 896 / 224 |
| `en` | English | derived | 393 / 524 / 131 | 434 / 579 / 145 | 389 / 518 / 130 |
| `ja` | Japanese | derived | 566 / 754 / 189 | 480 / 641 / 160 | 942 / 1256 / 314 |
| `ko` | Korean | derived | 560 / 747 / 187 | 468 / 624 / 156 | 908 / 1210 / 303 |
| `es` | Spanish | derived | 570 / 760 / 190 | 492 / 657 / 164 | 580 / 773 / 193 |
| `fr` | French | derived | 605 / 807 / 202 | 555 / 740 / 185 | 607 / 810 / 202 |
| `de` | German | derived | 634 / 845 / 211 | 485 / 646 / 162 | 636 / 847 / 212 |
| `pt` | Portuguese | derived | 583 / 778 / 194 | 475 / 633 / 158 | 590 / 787 / 197 |
| `ru` | Russian | derived | 689 / 919 / 230 | 500 / 667 / 167 | 1011 / 1349 / 337 |
| `id` | Indonesian | derived | 743 / 990 / 248 | 458 / 610 / 153 | 739 / 985 / 246 |

How a derived row is computed: the measured sizes are multiplied by the number
of tokens the text of the same parallel document takes in that language under
that tokenizer, relative to Traditional Chinese under `qwen3`. The document's
line breaks and indentation are left out of both, because the estimate counts
whitespace on its own. Every row therefore
holds the same amount of content per passage. That is a sound starting point
and nothing more: nobody has measured that it is the best size for retrieval in
that language, so measure on your own documents before relying on it.

Two limits of the conversion are worth knowing:

- **It does not look at your model's input limit.** A converted `max` can exceed
  what a model with a short input accepts — Russian under `xlm_r` is 667, and
  some models that use that tokenizer stop at 512. Lower the sizes for such a
  model.
- **The token figures come from one document.** Naming a language makes the
  estimate use the figure measured for it, which removes the over-count for
  English (over half, when counted as the densest Latin-script language). Text
  of a different kind — code, tables of numbers — will count differently, and
  `xlm_r` was under-counted by a quarter on Python source.

Sentence boundaries are recognised for all eleven languages. For any other
language — Thai, Arabic, Hindi and Vietnamese were measured and the estimate
was not accurate enough for them — there is no preset, but nothing is refused:
headings, tables and lists are still honoured, an oversize paragraph is cut by
length, and you choose the sizes and pass your own `measure`.

### Evaluating retrieval

To see it work first, with no key and no network:

```console
$ convilyn rag bench demo
ndcg@10 0.905 over 23 questions
wrote bench-demo/summary.md and bench-demo/report.json
```

The demo indexes six short documents that ship with the package (a made-up
bookshop's handbook, in English and Traditional Chinese, written for this
project) by keyword into an in-memory SQLite store, and runs 23 questions
over them, four of them with no answer in the documents. `convilyn rag bench
run --config rag.toml --questions questions.jsonl` does the same for the
corpus your settings file opens (already indexed) and your own questions.
Both replace the two files in `--out` by renaming a new file over each, so
a symbolic link there is replaced rather than written through. A failure is
one `error:` line that never repeats a value from your files, with exit code
1 for a file or setting problem (or numpy missing for a corpus with
`[embed]`), 2 for a model service that failed, and 3
when the report cannot be written (or the demo's own files cannot be read).

`convilyn.rag.bench` measures how often the passages that answer a question
come back, for any retriever:

```python
from convilyn.rag import bench

report = bench.run(retrieve=corpus.search, questions="questions.jsonl")
report.metrics["ndcg@10"]  # averages; report.groups per group
report.require(min_ndcg_at_10=0.85)  # AssertionError naming the gap
```

- **`retrieve(question)`** may return a `SearchResult`, a sequence of `Hit`,
  or mappings with `source_id` and `text`, best first; only the first
  `max(k)` are read. It may be sync (`bench.run`) or `async` (`bench.arun`).
  Questions run one at a time, and the bench calls nothing but `retrieve`;
  a sync `retrieve` under `bench.arun` runs on the event loop, so its
  latency is its own but the loop waits for it.
- **A question file** is JSONL, one question per line:
  `{"id", "question", "group"?, "support": [{"source_id", "excerpt"?}], "status"?}`.
  A question with no `support` has no answer in the documents: it is timed
  but left out of the retrieval metrics. `"status": "draft"` is skipped.
  An excerpt needs at least two characters once folded (shorter could never
  match). Errors name the line and a known field and never quote the line,
  nor the name of an unknown field.
- **Matching**: a passage supports an item when it comes from the item's
  document and, if the item has an `excerpt`, holds at least `cover` (0.5) of
  the excerpt's character pairs once folded by `rag.normalize`, with spacing
  left out. Pairs of characters treat Chinese and English alike.
- **Metrics**, for each `k` (default 1, 5, 10): `recall@k`, the share of
  items supported within the top `k`; `mrr@k`, one over the rank of the first
  supported item; `ndcg@k`, with each item scoring once, at the first rank
  that supports it, against the ideal of one item per rank. When one passage
  supports several items, they take that rank and the following ones, so a
  passage that answers everything at the top scores 1. Latency is reported
  as `latency_p50_ms` and `latency_p95_ms` (nearest rank) over every
  question run.
- **Bounds** are `min_<metric>` / `max_<metric>` with `@` written `_at_`. A
  bound on a metric the set cannot measure (no answerable question) **fails**
  rather than passes, and a check with no bound is refused. A `Report` read
  back from JSON is validated like one a run made (no NaN, scores within
  0–1, the metrics of its `k`), so a damaged report cannot pass a bound.
- **Comparing two runs**: `bench.compare(before, after, metric="ndcg@10")`
  pairs the questions by `id`, resamples the per-question differences with
  replacement (`random.Random(seed)`, standard library), and reads the
  interval the middle 95% of the resampled means span. The verdict is
  `better` when the whole interval is above 0, `worse` when below, and
  `inconclusive` when it contains 0: on a few dozen questions a small gap in
  the averages is often noise. Reports over different answerable questions
  are refused, naming how many differ, and so are reports sharing fewer than
  10 (too few to carry a verdict). A per-question difference within 1e-9 of
  zero counts as zero, so float rounding cannot make a verdict. `confidence`
  is from 0.5 below 1, `resamples` from 100 to 1,000,000. Latency is not
  compared (lower is better there).
- `report.to_markdown()` and `comparison.to_markdown()` give the text a
  person reads, the same for the same input.
- **Comparing several settings**: `bench.compare_variants({"base": a,
  "rerank": b, ...})` compares every report after the first with the first.
  Each comparison taken at 95% has a 5% chance of a verdict that is only
  noise, and those chances add up as variants are added, so with `m`
  comparisons each is made at `1 - 0.05 / m` (Bonferroni) and the 95% holds
  for all of them together. `Variants.to_markdown()` gives one row per
  variant with its verdict. From the command line, each variant is a
  `rag.toml`, named by its file's stem; the first is the baseline:

  ```console
  $ convilyn rag bench compare --questions questions.jsonl rag.toml rag-top1.toml rag-top3.toml
  rag-top1: worse (-0.115 ndcg@10)
  rag-top3: inconclusive (-0.023 ndcg@10)
  wrote bench-compare/summary.md
  ```

  (The shipped demo indexed by keyword, with `[recipe.search] limit` 1 and 3
  against the default 10.)

  Variants that change only how a search is answered (`[recipe.search]`,
  `[recipe.fuse]`, `[rerank]`) can point at the same index and reuse it; a
  variant that changes how passages are cut, folded or embedded needs its
  own index, which `rag.open` refuses to mix up. The questions are read
  once. `--estimate` reads only the settings and the question file and
  prints each variant's calls to model services: one embedding call per
  question when `[embed]` is set (with the input tokens by
  `rag.estimate_tokens`), and, when `[rerank]` is set, the reranking calls
  for at most `max(limit, candidates)` passages per question in batches of
  the reranker's `max_documents`. Retries are not counted, and it counts
  calls, not prices.
- **Holding a standard**: `convilyn rag bench pin --config rag.toml
  --questions questions.jsonl` writes `bench-pin.json`: the report, a digest
  of the question set, and each document's `(source_id, digest)`
  (`SourceInfo.digest`, the SHA-256 of what was added; for Markdown,
  independent of how it was cut). `convilyn rag bench check ... --pin
  bench-pin.json` runs again
  and compares:

  | Outcome | Exit | When |
  |---|---|---|
  | `held` | 0 | same questions and documents, and the comparison is not `worse` |
  | `worse` | 3 | the comparison on the pin's metric is `worse` |
  | `not_comparable` | 4 | the questions, the documents, `k` or `cover` changed, or fewer than 10 questions |

  A changed settings file is exactly what a check judges, so it is reported
  ("Settings since the pin: changed"), not refused. In CI:

  ```console
  $ convilyn rag bench check --config rag.toml --questions questions.jsonl --pin bench-pin.json
  ```

  `bench.pin(report, questions=..., sources=corpus.sources())` and
  `bench.check(...)` do the same in code.

  A pin is plain JSON that sets the bar, so review changes to it like the
  CI configuration it sits beside. It lists your documents' `source_id`s and
  SHA-256 digests of the documents and the settings file, which let someone
  confirm a guess at their contents: commit it only where those names may
  be seen. A file that is not a pin is refused naming only the pin's own
  fields, never a key or value from it.

| Default | Value | Basis |
|---|---|---|
| `k` | 1, 5, 10 | convention |
| `compare` resamples / confidence | 10,000 / 95% | convention for a percentile bootstrap |
| `compare` metric | `ndcg@10` | convention: rank-aware and bounded |
| `compare_variants` confidence | 95% for all comparisons together, `1 - 0.05 / m` each | derived: Bonferroni, which holds whatever the comparisons' dependence; more conservative than needed when they are correlated |
| `cover` | 0.5 | derived: half an excerpt is enough to answer from |
| matching by character pairs | — | derived: the same rule for scripts with and without spaces |
| ties in `ndcg` | spread over following ranks | derived: keeps every score at most 1 |

## `convilyn.providers` today

Connectors to the model services you already use: embedders
(`providers.embedder`), which turn texts into vectors, and rerankers
(`providers.reranker`), which put a shortlist in order with a stronger model.
Embeddings are spoken in two formats, OpenAI's (which many services and
self-hosted servers copy) and the Gemini API's own; reranking in Cohere's.

How far they have been tested: every connector has been tested only against
simulated HTTP answers shaped from each provider's published documentation,
never against the live service with a real key.

**This is the one place in the SDK where your text leaves your machine for a
service other than Convilyn.** It goes to the address you chose, under your
key, and nowhere else. What that rests on:

- **The key** comes from the argument or from the environment variable in the
  table. This SDK never writes it to a file, an error message, a `repr` or a
  log, and sends it in a header, never in the address. A key holding a control
  character, which the HTTP layer would reject while naming the whole value, is
  refused before it gets there. A row's key variable is read whenever you pass
  no `api_key`, including when `base_url` replaces the row's address and when
  you pass your own client: for a server of your own, use the
  `openai_compatible` or `cohere_compatible` row, which reads no variable.
- **The address** must be `https`. Plain `http` is accepted only for this
  machine, with the host `localhost`, `127.0.0.1` or `[::1]`, and a client made here
  then ignores proxy settings in the environment, so nothing in between is
  handed the request.
- **A redirect is never followed**, including when you pass a client of your
  own that is set to follow them.
- **Your own `http_client` is yours.** Its proxies, event hooks and logging
  apply as you configured them; this SDK adds the header that carries the key
  and the request, and nothing else.

The provider's answer is not trusted either: one that does not account for
every text or document exactly once, or that carries numbers no vector or
score can hold, is a `ProviderError`, never a crash and never a silent wrong
result. Its size is not capped: the address is one you chose.

`providers.available()` returns the tables below as data; a row's `embed_wire`
and `rerank_wire` say which of the two it offers.

### Embedders

| Provider | Address | Key variable | Texts per request | Tokens per text | Source, checked 2026-11-14 |
|---|---|---|---|---|---|
| `openai` | `https://api.openai.com/v1` | `OPENAI_API_KEY` | 2048 (published) | 8192 (published) | https://github.com/openai/openai-openapi/blob/manual_spec/openapi.yaml |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `GEMINI_API_KEY` | 100 (derived: not published for this call; the API's description states 100 for its older batch call) | 8192 for `gemini-embedding-2`, 2048 for `gemini-embedding-001` (published); none for other models | https://ai.google.dev/gemini-api/docs/embeddings |
| `mixroute` | `https://api.mixroute.ai/v1` | `MIXROUTE_API_KEY` | 96 (an estimate: not published by the provider; the smallest cap among the model families it routes to) | not published | https://mixroute.ai/docs/api-reference/endpoint/embeddings |
| `openai_compatible` | the `base_url` you pass | none; pass `api_key=` if your server wants one | 2048 (published for the format; set `max_inputs` to what your server takes) | not known | https://github.com/openai/openai-openapi/blob/manual_spec/openapi.yaml |

Every call goes through the same steps, in the same order, whichever provider
it is for: check the texts, deal with any that are too long, cut the list into
requests, send with retries, turn a refusal into a `ProviderError` with a
category, check that the answer accounts for every text exactly once, and scale
each vector to length 1.

`kind="document"` or `kind="query"` tells a model that embeds the two
differently which one it is given, in the way that model's documentation asks
for; other models are sent the text alone. For `gemini-embedding-001` and the
`embed-` models reached through `mixroute` that is a field of the request. For
`gemini-embedding-2`, whose documentation says the field cannot be used, it is
the documented wording put before your text (`title: none | text: ` for a
document, `task: search result | query: ` for a query), so what that model
embeds is a few tokens longer than what you passed. The request and response
shapes of the Gemini format were read from the API's machine-readable
description, https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta.

### Rerankers

| Provider | Address | Key variable | Documents per request | Source, checked 2026-11-14 |
|---|---|---|---|---|
| `cohere` | `https://api.cohere.com/v2` | `CO_API_KEY` | 1000 (published, as a recommendation rather than a refusal) | https://docs.cohere.com/reference/rerank |
| `mixroute` | `https://api.mixroute.ai/v1` | `MIXROUTE_API_KEY` | 1000 (an estimate: not published by the provider; the figure the format's own documentation recommends) | https://mixroute.ai/docs/api-reference/endpoint/rerank |
| `cohere_compatible` | the `base_url` you pass | none; pass `api_key=` if your server wants one | 1000 (the format's recommendation; set `max_documents` to what your server takes) | https://docs.cohere.com/reference/rerank |

`reranker.rerank(query, documents, top_n=None)` returns every document's
position in the list you passed and its score, best first. A call goes through
the same kind of sequence as an embedding: check the query and the documents,
cut the documents into requests, send each with the same query and with
retries, turn a refusal into a `ProviderError`, check that each answer scores
every document of its request exactly once, then order all of them and keep
`top_n`.

A score orders the documents of one query. It is not a probability, and
scores from different queries or different models do not compare. The length
of a document is not checked here: the service cuts a long one to its own
limit (4096 tokens per document unless changed, for `cohere`).

### Defaults and failures

| Default | Value | Grade | Basis |
|---|---|---|---|
| A text that is too long | refused by name, when you pass a `measure`; otherwise left to the provider | — | An estimate can over-count by more than half for some languages, and refusing a text the provider would have taken is worse than letting the provider answer |
| `on_overflow="truncate"` with no `measure` | cut using the cautious token estimate for the provider's tokenizer | derived | Same characters-per-token tables as `convilyn.rag`; cuts a little short rather than a little long |
| Retries | up to 5 after the first attempt, on 408, 429, 500, 502, 503 and 504 (no other status), waiting as `Retry-After` says up to 30 seconds | published | The SDK's existing retry policy, which follows common client practice |
| Reranking more documents than one request carries | each request scored on its own, then all scores put in one order | derived | A reranking model scores a document against the query without looking at the other documents, so scores of one query and one model are comparable across requests. Not measured here, and no provider states it; pass `max_documents` to keep a shortlist in one request if your model differs |
| Documents with the same score | kept in the order you passed them | — | So that the same call returns the same ranking |
| Timeout of a client made here | 60 seconds for each of connecting, sending, and waiting for more of the answer | derived | Long enough for a full request of long texts; not measured. It is not a limit on the whole call, and a request that times out is not retried |

A `ProviderError` says what happened through `category` (`auth`,
`rate_limited`, `invalid_request`, `unavailable`, `connection`,
`bad_response`) and `status`. Its message is a fixed sentence: it does not
repeat what the provider said, because that text can contain what you sent.

## `convilyn.graphs` today

Ready-made graphs for an application built on LangGraph. Each one is a compiled
graph built from functions you supply, so it never chooses a model, a search
engine or a prompt for you. Install with `pip install "convilyn[graphs]"`.

```python
from convilyn import graphs

improve = graphs.refine(generate=write_draft, evaluate=run_checks)
```

**Which graph.** Each graph is for one situation. `graphs.available()` returns
the same sentence as `use_when`, and each builder's docstring states it right
after its one-line summary, as "Use it when …".

| Use it when | Graph | Compared with the graph it is closest to |
|---|---|---|
| a draft can be checked, by tests, a schema, a linter or a reviewer, and fixed from what the check says: it drafts, checks and redrafts with the feedback until the check passes or the rounds run out. | `graphs.refine` | `plan_execute` runs different steps in order; `refine` repeats one draft against one check, two calls a round. |
| answers must come from your documents and one search can miss: it keeps only the passages judged relevant, rewords the query and searches again when none are, then answers and says whether the answer is grounded. | `graphs.corrective_rag` | `fanout` asks several sources once, at the same time; `corrective_rag` asks one source again, one after another, only when nothing relevant came back. |
| you need certain details from the user before acting, and they may arrive over several messages: it keeps each valid value found so far and reports which details are still missing. | `graphs.collect` | `use_case` first chooses which task a message is about (a route call), then gathers that task's details and runs an agent; `collect` gathers one fixed set of details. |
| your application handles several distinct tasks, each with its own instructions, required details and allowed tools: it sends each message to one task, gathers that task's details and lets an agent answer with only that task's tools. | `graphs.use_case` | `collect` alone is enough when there is one task and only its details are missing. |
| a question may need several independent sources at once: it runs the ones your classifier picks at the same time and combines what they return into one answer. | `graphs.fanout` | `plan_execute` runs steps one after another, each seeing the earlier results, plus a replan call per step; `fanout` branches run at once and none sees another's result. |
| a task takes several steps whose results shape the next ones: it plans, carries out one step at a time and revises the rest of the plan after each step, within a step limit. | `graphs.plan_execute` | `fanout` is fewer calls and faster when the parts do not depend on each other; `refine` when the work is one draft and one check. |

**Three ways to place one**, and the graph is the same object in each:

| Your situation | What to write |
|---|---|
| Your state uses the graph's key names | `builder.add_node("improve", improve)` |
| Your state names them differently | `builder.add_node("improve", graphs.as_node(improve, inputs={"task": "request"}, outputs={"result": "reply"}))` |
| An agent should decide when to call it | `graphs.as_tool(improve, name="improve", description="...")` |

`as_node` and `as_tool` work on any compiled graph, not only the ones built
here. `graphs.available()` lists every graph with the keys it reads and writes.

**What stays yours.** A graph built here has no saved-state backend, no store
and no run-time context of its own. Inside your graph it uses the ones yours
was compiled with, and it runs the same when yours has none. It reads only its
inputs and writes only its outputs; keys it keeps for itself start with `cv_`.
Its state is plain data (text, numbers, booleans, lists, dictionaries), so
whatever saves your state can save its.

**What belongs to the framework, and this package cannot change:**

- Added directly as a node, a graph shares any state key your graph declares
  under the same name. Use `as_node` to keep them apart.
- To stream what happens inside, ask for it: `stream(..., subgraphs=True)`.
- Called as a tool, what the graph kept while running is not visible to the
  agent afterwards. What should last belongs in the agent's own state.
- A tool's arguments are the graph's input schema, and an agent can fill in
  every one of them. `as_tool` checks their types strictly (text is not
  accepted where a number or a boolean is declared), reads them as the JSON an
  agent writes (a date as `"2026-11-14"`), drops anything else, and hands the
  graph the checked values. A refusal names the field, never the value the
  agent wrote. The graphs built here declare only their inputs; a graph of your
  own compiled without `input_schema=` offers the agent every state key it has.
- `as_node` and `as_tool` read a graph's schema through pydantic, which below
  Python 3.12 accepts `typing_extensions.TypedDict` but not `typing.TypedDict`.
  The graphs built here use the first; on Python 3.11, a graph of your own
  needs it too.

**Plain or async functions.** Supply either. With plain functions a graph runs
under `invoke` and `ainvoke`. With any `async` function, use `ainvoke` or
`astream`; `invoke` raises a `TypeError` that says so, before the `async`
function has started. Under `ainvoke` a plain function runs on the event loop's
thread, as it would in any LangGraph node, so one that blocks for long is better
written as `async`.

A function that returns something the graph cannot use (a number where text is
expected) raises `graphs.GraphError`. Its message is a fixed sentence and never
repeats your text.

### `graphs.refine`

Draft, evaluate, redo with the feedback, until accepted or out of rounds.

| | |
|---|---|
| Reads | `task` |
| Writes | `result`, `accepted`, `feedback`, `rounds` |
| `generate(task, previous, feedback)` | Returns a draft. `previous` and `feedback` are `None` for the first one. |
| `evaluate(task, draft)` | Returns `None` to accept, or text saying what to fix. |

`evaluate` does not have to be a model. A schema check, a test run or a linter
gives feedback from outside the model, and published results find that a model
asked to correct itself without outside feedback often does not improve
([Huang et al., 2024](https://arxiv.org/abs/2310.01798)). When the rounds run
out, the last draft is returned with `accepted` false and the feedback it got.

### `graphs.corrective_rag`

Search, keep what is relevant, reword the query when nothing is, then answer.

| | |
|---|---|
| Reads | `question` |
| Writes | `answer`, `documents`, `grounded`, `attempts` |
| `retrieve(query)` | Returns a `rag.SearchResult` or a list of strings. `corpus.search` fits as it is. |
| `grade(question, passage)` | Returns whether the passage helps answer the question. |
| `rewrite(question, query)` | Returns a query to try after `query` found nothing relevant. |
| `generate(question, documents)` | Returns the answer from the passages kept. |

Each document is `{"text": ..., "source": ...}`; `source` is the document's
identifier when the retriever is a corpus, and `None` otherwise. When every
search comes back with nothing relevant, `generate` is still called, with an
empty list, and `grounded` is false: what an answer without sources should say
is your decision. The design follows Corrective RAG
([Yan et al., 2024](https://arxiv.org/abs/2401.15884)), without its web-search
fallback: pass a `retrieve` that falls back if you want one.

### `graphs.collect`

Gather the fields a task needs from what the user says, over as many turns as
it takes.

| | |
|---|---|
| Reads | `message`, `collected` (absent on the first turn) |
| Writes | `collected`, `missing` |
| `fields` | Each field's name mapped to its JSON Schema, in the order to ask for them. Plain data: load it from a file. |
| `extract(message, schema)` | Returns the values the message gives. `schema` is an object schema of the fields still missing; leave out, or set to `None`, what the message does not say. |
| `check(field, value)` | Optional. Your own check of a value its field's schema accepted: `True` keeps it, `False` drops it. For a field the shapes below cannot express. |

```python
fields = {
    "order_id": {
        "type": "string",
        "maxLength": 16,
        "pattern": "^A-[0-9]+$",
        "description": "The order number.",
    },
    "email": {"type": "string", "format": "email", "description": "Where to send the reply."},
}
details = graphs.collect(fields=fields, extract=read_fields)
```

`extract` is where your model reads the message, with whatever
structured-output method your provider offers; nothing else in the graph calls
a model. A valid value already in `collected` is never replaced (remove it
from `collected` to ask again). Keys in `collected` that are not fields are
your state, not the graph's: they are carried through untouched and unchecked.
Every value is checked against its field's schema on every run, whether
`extract` found it or it came in `collected` (an agent calling the graph as a
tool writes `collected` too): one that does not match is dropped, and so is a
value for a field that was not asked for. An empty `missing` therefore means
every field holds a value its schema accepts. `format` is read as a note, not
checked, as JSON Schema defines it.

**A field's schema is one of a closed set of shapes.** The value an agent or a
model writes decides how long a JSON Schema check takes, and admitting any JSON
Schema left a new way each time to make one check take seconds (references,
other dialects, choices that collect every error, pairwise `uniqueItems`, long
text in every error). So `collect`, and therefore `use_case`, admits only:

| Shape | Keywords |
|---|---|
| a scalar: `type` `string`, `integer`, `number`, `boolean`, `null`, or a list of them | the type's own: `minLength`, `maxLength`, `format`, and `pattern` with a `maxLength` of at most 256; `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, a whole-number `multipleOf` up to 2^53 |
| a choice | `enum` (up to 256 scalars) or `const` (one scalar) |
| an `array` of one scalar shape | `items`, `maxItems` (needed, up to 256), `minItems`, `uniqueItems` |
| an `object` of scalar properties | `properties` (up to 64), `required`, `additionalProperties: false` |
| anything | `true`, or a schema of annotations only |

`title`, `description` and `examples` are allowed everywhere. Any other keyword
is refused when the graph is built, with a `ValueError` naming the field and the
keyword. A value is checked only within 256 parts (lists, objects and the
values in them), 64 levels and 16 KB as JSON: one larger from `extract` raises
`graphs.GraphError`, one larger in `collected` is dropped like any value that
does not match. Checking a value is then one pass over its parts, each against
one scalar shape, its length and range keywords first. A `pattern` is your
regular expression and reads at most 256 characters, since it needs a
`maxLength` and is checked after it; keep it free of nested or adjacent
repetition (`(a+)+`, `^a*a*$`) and of unanchored repetition at the start
(`\s+$`), each of which slows down with the length of the text.

**A field the shapes cannot express** (a nested object, a choice between
shapes, a rule across fields) takes the schema `true`, and your own
`check(field, value)` decides: it returns `True` to keep the value and `False`
to drop it. It runs on every value its field's schema accepted, whether
`extract` found it or it came in `collected`, and every time the graph runs,
so a value kept in an earlier turn or before a pause is checked again. `value`
is plain JSON within the limits above; `check` is your code, outside them, and
an exception it raises ends the run. A `true` field with no `check` is
unchecked, by your choice.

**Asking for what is missing.** By default a run ends with `missing` listing the
fields still without a value, and your reply asks for them however your
application words things; the next turn's message continues from `collected`,
which your saved state carries. Nothing has to pause, so a host without a
saved-state backend works the same. Your state keeps a list of chat messages
rather than one text? A step of your own copies the latest message's text into
a key, and `as_node` reads it from there.

With `collect(..., interrupt=True)` the graph instead pauses with
`{"missing": [...]}` and waits: resume the run with the user's answer as text
(the framework's `Command(resume=answer)`) and it extracts again, pausing until
nothing is missing. Resuming needs a host compiled with a saved-state backend;
run without one, the run still returns what it is waiting for, but cannot be
continued. A paused run resumes the same way whether the graph is a node, a
renamed node, a tool, or called from a step of your own.

### `graphs.use_case`

Choose which of your tasks a message is about, gather that task's fields, then
let an agent answer with only that task's tools.

| | |
|---|---|
| Reads | `message`, `use_case_id`, `collected`, `clarify_turns` (all but `message` may be absent) |
| Writes | `outcome`, `reply`, `use_case_id`, `candidates`, `collected`, `missing`, `clarify_turns` |
| `cases` | `graphs.UseCase` objects: `id`, `description`, `instructions`, `fields` (the shapes of `collect`), `tools` (names), `max_model_calls` (1–8), `max_tool_calls` (0–8). Plain data: load them with `UseCase.model_validate`. |
| `model` | The chat model the agent calls, as `langchain.agents.create_agent` takes it. |
| `tools` | Every tool any use case names, as objects. |
| `extract(message, schema)` | As in `collect`; needed when a use case has fields. |
| `check(field, value)` | As in `collect`. |
| `route(message, choices, active)` | Returns `{"use_case_id": ..., "candidates": [...]}`. `choices` lists each use case's `id` and `description`; `active` is the one in progress. Leave it out when your own code or agent chooses. |
| `middleware` | Your own agent middleware, added to every use case's agent. |

```python
cases = [graphs.UseCase.model_validate(case) for case in json.load(open("cases.json"))]
serve = graphs.use_case(
    cases=cases, model=model, tools=[refund, change_address], extract=read_fields, route=choose_case
)
```

**Choosing.** `route` is your function, so how the choice is made (a model with
structured output, keywords, a classifier) is yours, and so is its prompt. The
graph keeps only ids that are use cases, puts the chosen one first, and keeps at
most three. When as many use cases are named as there are use cases (two at
least, three at most), the message is ambiguous: the run ends with `outcome`
`clarify` and the `candidates` for your reply to offer. It does that at most
`max_clarify_turns` turns in a row; after that, or when nothing fits, `outcome`
is `no_match` and your fallback answers. Without `route`, the run takes
`use_case_id` as given, which is how an agent of yours that has already chosen
calls it as a tool; an id that is not a use case raises `graphs.GraphError`.

**Fields** are collected exactly as `collect` does, under the use case's id in
`collected`, so two use cases never share a value. While one is missing,
`outcome` is `missing` and your reply asks for it; with `interrupt=True` the run
pauses instead, as `collect` does.

**Answering.** Each use case gets its own agent, built once with
`langchain.agents.create_agent`: your `model`, the use case's `instructions` as
its system prompt, and only the tools it names. A call to any other tool is
refused without running it. The agent's turn holds the user's message and, as
JSON, the values collected for the use case: they came from the user, so they
travel as the user's words, never in the system prompt. `max_model_calls` and
`max_tool_calls` bound one run. When the agent needs one more model call than
`max_model_calls` allows, the run ends with `outcome` `budget_exhausted` and no
reply; a tool call past `max_tool_calls` is not run, and the agent is told so.
Both count every call made while answering one turn, including those after a
pause inside the agent is resumed; the next turn starts from zero. A tool or
middleware of yours that pauses resumes like any other pause in your graph.

Your `middleware` runs after those limits, on every use case's agent, so give it
no limit of the same kind. Middleware that brings tools of its own is refused,
since every use case would hold them. Middleware that retries or falls back to
another model makes several model calls that count as one.

**Your tools check their own arguments.** `use_case_id` and `collected` are
inputs: an agent of yours calling this graph as a tool writes them, and a value
that matches its schema is not proof the user said it. A tool that acts on an
order decides for itself whether this user may act on that order.

What a use case's `instructions` and tool descriptions say is your wording; the
graph adds none.

### `graphs.fanout`

Choose the branches a question needs, run them at the same time, then combine
what they returned into one answer.

| | |
|---|---|
| Reads | `question` |
| Writes | `answer`, `results` |
| `branches` | Each branch's name mapped to a function `(text)` that returns what it found, as text. |
| `classify(question, names)` | Returns the branches to run, each mapped to the text it is given. `names` lists every branch in the order you declared them. |
| `synthesize(question, results)` | Returns the answer. `results` maps each branch that ran to what it returned. |

```python
answer = graphs.fanout(
    branches={"docs": search_docs, "tickets": search_tickets, "billing": look_up_billing},
    classify=choose_sources,
    synthesize=write_answer,
)
```

**Choosing.** `classify` is your function: a model with structured output,
keywords, or a rule. It returns `{name: text}`, so a branch can be given the
question as it is (`{name: question for name in chosen}`) or only the part it
answers. Each branch it names runs once; a name that is not a branch raises
`graphs.GraphError`, which never repeats the name. When it names none, no branch
runs and `synthesize` gets an empty `results`: what that answer says is yours.
`results` is an output like `answer`, so an agent calling the graph through
`as_tool` reads each branch's own text too; a branch whose raw output the model
should not see returns only what it may.

**Running at the same time.** Every chosen branch runs in the same step. Under
`ainvoke` they run concurrently on the event loop; under `invoke` the framework
runs each on a thread of its own, so a plain function that more than one branch
shares must be safe to call that way. `max_concurrency` in your run's config
caps how many run at once; the graph sets no limit of its own. `results` lists
the branches in the order `classify` named them, whichever finished first. A
branch that raises fails the run with its own error; one that should not, catches
it and returns text saying so.

**Pausing.** The graph never pauses on its own. A branch of yours may, with the
framework's `interrupt` (asking for approval before it acts, say): in a host
with a saved-state backend the run pauses with that value, and resuming runs the
paused branch again from its start while the branches that had already finished
keep what they returned and do not run again.

### `graphs.plan_execute`

Turn a task into steps, carry them out one at a time, and revise the rest of
the plan after each step, until there is an answer or the step limit is reached.

| | |
|---|---|
| Reads | `task` |
| Writes | `answer` (`None` when the steps ran out), `done` (each step with its result, in order), `answered` |
| `plan(task)` | Returns the steps, as a list of text. |
| `execute(task, step, done)` | Carries out one step and returns its result as text. `done` lists the earlier steps as `{"step": ..., "result": ...}`. |
| `replan(task, done, remaining)` | Runs after every step. Returns `{"answer": text}` to finish, or `{"steps": [...]}`, the steps to carry out next. |
| `max_steps` | Required, 1 to 32: the most steps one run carries out. |

```python
solve = graphs.plan_execute(
    plan=write_plan, execute=carry_out_step, replan=review_progress, max_steps=8
)
```

**Three functions, each what suits it.** A strong model can plan while a cheaper
one, a tool, or another graph (`use_case`, say) carries out each step, and
`replan` can be plain code that returns `{"steps": remaining}` while nothing
needs rethinking. An empty plan goes straight to `replan`. Each step is a step
of the graph, so a host with a saved-state backend saves the run after every
one. An agent's own to-do list (langchain's `TodoListMiddleware`) plans inside
one agent and one model; this graph keeps the plan as state you can see.

**Limits.** `max_steps` has no default: a run that reaches it without an answer
ends with `answered` false and `answer` `None`, and `replan` still runs after
the last step, so it can answer there. Every result stays in the run's state and
is handed to `replan` again, so what each function returns is held to the value
limits `collect` uses (256 parts, 64 levels and 16 KB as JSON) and a larger
return raises `graphs.GraphError`, which never repeats the value. A longer
result is kept outside the graph and referred to by a short text.

**Pausing.** The graph never pauses on its own. A step of yours may, with the
framework's `interrupt`: resuming runs that step again from its start, and the
steps already done are not run again.

### Defaults

| Default | Value | Grade | Basis |
|---|---|---|---|
| `refine(max_rounds=)` | 3 drafts | derived | A small fixed budget, so a check that never passes ends; not measured here |
| `corrective_rag(max_retries=)` | 2 more searches | derived | Each retry costs a search, a round of grading and a rewrite; not measured here |
| `collect(interrupt=)` | `False`: end the run with what is missing | derived | Works in every host, with or without a saved-state backend; pausing is for hosts that have one |
| `use_case(max_clarify_turns=)` | 1 turn in a row | derived | Asking twice running whether the user meant A or B reads as not listening; after one, your fallback answers. Not measured here |
| `use_case(interrupt=)` | `False` | derived | Same as `collect` |
| ambiguity threshold | `max(2, min(3, use cases))` candidates | derived | Two named among two use cases, or three among three or more; one use case never asks. Not configurable; not measured here |
