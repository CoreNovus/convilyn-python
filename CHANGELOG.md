# Changelog — `convilyn` (consumer SDK)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-08-16

### Fixed

- **PDF → Markdown rebuilds paragraphs instead of emitting one per visual line.**
  A PDF stores glyph positions, not paragraphs, so a Chinese paragraph wrapping
  over four lines arrived as four paragraphs — and the font-size pass then
  promoted some of them to headings.

  Measured on a 4-page Chinese whitepaper and a 12-page PRD, against the same
  documents' `.docx` originals as the reference:

  | | headings before | headings after | `.docx` reference |
  | --- | ---: | ---: | ---: |
  | whitepaper | 17 | **7** | 7 |
  | PRD | 129 | **32** | 32 |

  List items went from 0 to 7 and 0 to 32; table extraction is unchanged.

  **CJK text is joined with no space**, because Chinese and Japanese put none
  between words — a line break there carries no character, and inserting one
  invents data. Latin keeps its space, where the break really is standing in
  for one.

  Three defects sat behind this:

  - Lines were grouped by `int(top / tolerance)` — a bucket, not a distance. Two
    words 1.8pt apart landed either together or apart depending on where the
    line sat on the page, which is how a numbered heading arrived as `#### 摘要`
    followed by `#### 1.`.
  - Bullets were invisible. Word emits them from a symbol font as Private Use
    Area codepoints (U+F0B7), which render as nothing — so a list looked like
    prose with a leading blank.
  - `str.isupper()` was used as a heading signal. It skips uncased characters
    and answers about whatever is left, so a Chinese sentence containing one
    Latin acronym read as upper case. And a leading `1.` promoted numbered
    *list items* to headings; a finished sentence now distinguishes them.

- **`convilyn doctor --ping` no longer reports success when the API rejects your
  key.** A `401` or `403` from the tier probe is now a **FAIL** with exit code
  `2`, not a `WARN` with exit code `0`.

  It mattered because `doctor` is what people put in the first step of CI. The
  old classifier could not tell "the backend hiccuped" from "your credentials
  are refused" — every `APIError` was advisory — so a broken key produced
  `All checks passed.` and travelled downstream until something else failed for
  a reason that looked unrelated.

  A 5xx or a transport error stays advisory, deliberately: the required checks
  did pass, and an unreachable optional signal is not a broken environment.

  Two smaller fixes ride along. The exit code now comes from the check that
  failed rather than from matching its display name, so an auth failure is an
  `EXIT_API_ERROR` instead of an `EXIT_USAGE`. And the summary line counts
  warnings — `All checks passed.` is now reserved for a run with none, rather
  than printed above a `WARN` line it contradicts.

- **`convilyn.local.pdf` no longer leaks `pypdf`'s exception types.** An
  encrypted source raised `pypdf.errors.FileNotDecryptedError` out of **seven of
  the eight** operations — `page_count`, `extract_text`, `select`, `merge`,
  `rotate`, `compress`, `burst` — past an `except LocalError` that
  `docs/STABILITY.md` says is enough. All of them now raise
  `PdfOperationError`.

  The translation had been hand-rolled at five call sites with three different
  `except` lists; it is now one guard that every operation goes through, so the
  next one added cannot be written without it.

- **`convert.create(file="report.pdf")` raises `TypeError` with an explanation**
  instead of `AttributeError: 'str' object has no attribute 'filename'` from two
  frames down. The message names both ways forward: `file_id=` for an id, or
  `files.upload(path)` first. The signature was always right and a type checker
  always caught this; the fix is for callers who do not run one.

### Documentation

- **The typed-exception list in QUICKSTART section 4 is now the exceptions this
  package exports, and a test keeps it that way.** It had named
  `UnsupportedRouteError` under a `from convilyn import ...` heading — that name
  lives in `convilyn.local` and importing it from the top level is an
  `ImportError` — and listed 6 of the 14 catchable types.

  `UnsupportedRouteError` is deliberately **not** promoted to the top-level
  namespace: it is the offline engine's answer about what this machine can
  convert, and the offline engine ships behind extras. The list now has a
  section per namespace.

  Also noted there: `JobError` is exported and ends in `Error` but is a pydantic
  model, not an exception. `except JobError` is a `TypeError` at runtime.

- QUICKSTART documents `max_rows` / `--max-rows`, and `goals.understand()` /
  `goals.to_markdown()` — all three shipped in 2.0.0 with no section to read.

- QUICKSTART says outright that **CSV, XML and plain text get bigger** when
  rendered to Markdown (measured: +3.1% and +33.2% in tokens), and that the
  conversion earns its keep on formats whose structure is locked in a binary
  container. The tool converts them because the route is real; nothing said it
  costs you tokens rather than saving them.

- `docs/README.md`'s "no silent fallbacks, no partly-converted files" is scoped
  to the offline engine, which is where it is true and where `convilyn local
  doctor` can enumerate it. The hosted API publishes a `qualityMode` per route,
  and the sentence now points at it.

## [2.0.0] - 2026-08-15

### Added

- **`convert` now reaches images and media, not only documents.**
  `client.convert.create(file=photo, target_format="webp")` and
  `convilyn convert clip.mp4 --to mp3` work. Previously the resource sent a
  hardcoded `document_conversion` and could not derive a source format from
  anything but 13 document extensions, so `convilyn convert video.mp4 --to mp3`
  failed locally without issuing a request — and for a video the *metered* verb
  was the only one available, which is the wrong way round for a split whose
  whole purpose is knowing before you spend.

  **The processor is derived, never named by the caller.** It is the conversion
  family that speaks *both* the source and the target format — which is why
  `clip.gif --to png` is an image conversion and `clip.gif --to mp4` is a media
  one. Reading the family off the source alone cannot answer that.

  ```python
  await client.convert.create_and_wait(file=clip, target_format="mp3")
  # → {"processor_type": "media_processing", "params": {...}}
  ```

  **Nothing `convert` can reach spends credits.** OCR and transcription are not
  in the derivation at all; extraction remains `goals.understand()`. Whether a
  particular pair is *producible* stays the backend's answer, published at
  `GET /api/v1/{document,image,media}/support` — this SDK carries no copy of it.

- `convilyn convert --dry-run` now prints the `processor_type` it would reach
  (and carries it in `--json`), so the lane is visible before anything uploads.
  It builds the real request body rather than describing one, so an argument the
  live call would reject is rejected here too.

- **`goals.to_markdown(files)` — extract unstructured content into Markdown.**
  The metered path, for documents whose content has to be *extracted* before it
  can be written down: scanned pages with no text layer, embedded figures that
  need describing. That work calls per-unit billed third-party APIs, so it is
  charged.

  **If you only need a plain rendered `.md`, do not use this.** Deterministic
  document-to-Markdown conversion ships free on every plan through the
  file-conversion API; this method will never be the cheaper way to get one.

  ```python
  try:
      md = await client.goals.to_markdown(["file_abc"])
  except convilyn.UnderstandUnavailableError:
      ...  # fall back to file conversion
  ```

  **Not yet served by any platform build** — every call currently raises
  `UnderstandUnavailableError`, naming the free alternative. The method ships
  ahead of the pipeline so code written against it keeps working unchanged when
  the capability is enabled server-side, exactly as `understand()` shipped ahead
  of its own rollout. A differently-shaped result is never returned in its place.

- **`Route.unavailable_kind` — why a conversion cannot run, in a form a program
  can act on.** `available=False` did not mean the same thing on every engine: a
  document route was unavailable only because something was not installed, while
  an image route could be unavailable because this build of Pillow has no encoder
  for the target — which no install changes. Telling those apart meant reading the
  English in `unavailable_reason`, or knowing which engine produced the row.

  ```python
  routes = convilyn.local.capabilities().routes
  worth_fixing = [
      r for r in routes if not r.available and r.unavailable_kind != "unsupported_by_build"
  ]
  ```

  The three values are `missing_requirement` (a declared requirement is absent —
  `Route.missing` names it), `missing_plugin` (fixable, but by a component this
  package does not distribute, such as `pillow-heif`), and
  `unsupported_by_build` (nothing installable changes the answer). `None` exactly
  when the route is available.
- **Media conversion in `convilyn.local` — offline, no API key.** Video and audio
  containers convert into one another, and a video converts into an audio file:
  `mov → mp4`, `mp4 → mp3`, `wav → flac`, and 67 other pairs.

  ```bash
  convilyn local convert clip.mov --to mp4
  convilyn local formats --from mov      # what this machine can do with a .mov
  ```

  ```python
  from convilyn import local
  local.convert("clip.mov", to="mp4")
  ```

  **It needs FFmpeg, which is a program rather than a package**, so — like
  LibreOffice and Calibre — it is not an extra and `pip` cannot supply it.
  Nothing raises to find that out: an unavailable route says so as data, with
  `unavailable_kind="missing_requirement"` and a sentence naming the download.
  `convilyn local doctor` lists it alongside the other two.

  Animated GIF is a **source only**. It has no encoder here, so `*-to-gif` is not
  offered rather than offered and failing.

  **`Engine` gains `"media"`.** The union is wider, so a caller that exhaustively
  matches on it — a `match` statement with no default, or a dict keyed by every
  engine — needs a branch for the new value. Reading the field, comparing it, or
  printing it is unaffected.

  Not included: trimming, cropping, resolution changes, and compressing to a
  target size. Also not included, and never will be: transcription and anything
  else that calls a paid service. `convilyn.local` is the half that needs nothing
  from us, and a conversion whose cost depends on how long your video is does not
  belong in it.
- **`--out-dir` on `convilyn local convert`.** `batch` has always had it, and
  reaching for it on `convert` got only *"Did you mean '--out'?"*. It writes into
  the directory keeping the source's name — `convert report.docx --to md --out-dir
  build/` → `build/report.md`. Passing it together with `--out` is refused rather
  than ranked: one names a file and the other a directory, so honouring one would
  mean silently ignoring the other.
- `UnavailableKind`, the type alias for that field.
- `convilyn local formats --json` and `convilyn local convert --dry-run --json`
  carry `unavailable_kind` alongside `unavailable_reason`.
- **`max_rows` on `local.convert()` / `local.convert_many()`, and `--max-rows` on
  `convilyn local convert` / `batch`.** The CSV row cap was a module constant no
  caller could reach, so neither "this is 500,000 rows, give me the first 200"
  nor "give me all of it" could be said at all.

  ```bash
  convilyn local convert transactions.csv --to md --max-rows 200
  convilyn local convert transactions.csv --to md --max-rows 0    # the whole file
  ```

  It counts **data** rows — the header is not charged to it — and applies only to
  row-based sources. Passing it for a source with no rows is refused rather than
  ignored: a cap silently dropped on some inputs and honoured on others is the
  same invisibility this option exists to remove. In a batch that refusal is one
  file's `ok=False` result, not a stopped run.

### Changed

- **An unsupported conversion raises `UnsupportedRouteError`, not `ValueError`.**
  QUICKSTART says *"catch the base `ConvilynError` to handle them all
  uniformly"*, and `ValueError` is not one — so anyone who wrote their error
  handling from the documentation missed this entire class:

  ```python
  client.convert.create_and_wait(file=txt_file, target_format="mp3")
  # was: ValueError  — invisible to `except ConvilynError`
  # now: UnsupportedRouteError — which is a ConvilynError
  ```

  The message is unchanged, and the refusal still happens **before** the upload,
  so an impossible pair still costs nothing. Only the type moved.

  It is the type `convilyn.local` already uses for the same question, reused
  rather than duplicated under a second name.

  **⚠️ That changes what `LocalError` means.** `UnsupportedRouteError` subclasses
  `LocalError`, so `except LocalError:` now also catches this refusal from the
  **hosted** lane:

  ```python
  try:
      client.convert.create_and_wait(file=txt_file, target_format="mp3")
  except LocalError:
      ...  # reached now; would not have been before
  ```

  No `except` loses a case — every clause that caught something still catches it,
  so this cannot silently miss. But `LocalError` has stopped being a reliable
  answer to *"was this an offline failure?"*, and code that branches on it to
  route between the two halves needs to read `UnsupportedRouteError` explicitly
  instead.

  The alternative — moving `UnsupportedRouteError` out from under `LocalError`
  into the shared taxonomy — was considered and rejected: it would **narrow**
  `except LocalError:` so it stopped catching genuine offline route failures,
  which is the silent direction. A widening you can see beats a narrowing you
  cannot.

  The same applies to a source extension no family speaks, and to asking for a
  format to convert to itself — both reach the same refusal and both moved with
  it.

  **Argument mistakes deliberately stay builtins.** `upload()` with neither
  `path` nor `content`, a `page_range` on an image conversion, a `max_attempts`
  below zero — those keep raising `ValueError` / `TypeError`. The line is *the
  conversion you asked for cannot be produced* (a domain failure) versus *the
  arguments you passed do not make sense* (not one).

- **`convilyn local convert --out photo.webp` now converts to WebP.** `--out` named
  the path and nothing else, so the target format still defaulted to Markdown — the
  command planned a conversion to `md`, converted to the suffix, and reported the
  failure of the plan it had not run:

  ```console
  $ convilyn local convert logo.svg --out logo.png
  ✗ Unsupported conversion: No route from svg to md. This engine cannot read svg at all.
                                            ^^ nobody asked for md
  ```

  It now reports what was actually wrong:

  ```console
  ✗ Cannot convert here: Reading svg needs cairosvg, a Pillow plugin this package
    does not install: a vector rasteriser, which needs a native cairo. …
  ```

  This is the semantics the library has always documented — *`to` names the format
  and the output sits beside the input; `out` names the path and the format comes
  from its suffix* — so the CLI now matches the Python API rather than contradicting
  it. Passing `--to` as well still wins, and `--out` with no extension is refused
  with a message naming the flag to add, instead of guessing.

- **`local.plan()` accepts `out=`.** It took only `to=`, so it could not answer the
  question `convert(out=...)` answers, which is why the CLI had to guess a format
  before asking. `plan(src)` with neither still means Markdown; passing both lets
  `to` win, the same precedence `convert` uses.

- **⚠️ `convert.download_to()` and `goals.download_artifact_to()` no longer replace
  an existing file. They raise `FileExistsError` unless you pass
  `overwrite=True`.** This is a behaviour change to methods that already shipped,
  not a new option: a script that re-downloads to the same path succeeded before
  and now raises.

  ```python
  # before: silently replaced whatever was there
  # now:    FileExistsError — "out/report.pdf exists; pass overwrite=True to replace it"
  client.convert.download_to(job, to="out/report.pdf")

  # the re-run, and the whole migration
  client.convert.download_to(job, to="out/report.pdf", overwrite=True)
  ```

  Why it is worth a break: `convilyn.local.convert()` has always refused, and its
  docstring gives the reason as a product position rather than a default —
  *guessing what somebody wanted is how a converter overwrites the wrong file.*
  The cloud half overwrote silently. A package that answers the same question two
  ways, depending on which half you reach, does not have two defaults; it has no
  position, and you cannot reason from one side to the other.

  `goals.download_artifact_to()` changed with it. Both call one writer whose
  docstring promises they behave identically, so fixing only the reported one
  would have made that promise false and recreated the inconsistency one resource
  over.

  A pre-existing **symlink** at the destination is still refused outright, and
  `overwrite=True` does not override that — following it would write the bytes
  wherever it points, which is a different hazard with a different answer.

- **`convilyn convert` gained `--overwrite`.** The CLI downloads through the
  method above, so without it a second run of the same command had no way to
  finish. Same name and same meaning as `convilyn local convert --overwrite`,
  which has always had it.
- **XLSX conversion now carries percentage and currency formats across, and
  still refuses everything lossy.** A cell formatted `0.0%` used to convert as
  `-0.720386735542037`, which is correct but drops the fact that it is a ratio.
  It now converts as `-72.0386735542037%`.

  **The format's rounding is deliberately not applied.** A spreadsheet displays
  that cell as `-72.0%`, and so does the hosted `xlsx → csv` route; matching that
  string would discard eleven digits the file actually contains. The rule is
  *lossless and adds meaning*:

  | format | applied | why |
  |---|---|---|
  | percentage `0.0%` | yes, without the rounding | lossless; says it is a ratio |
  | currency `"NT$"#,##0` | yes, the symbol | lossless; nothing else records the currency |
  | thousands `#,##0` | no | no meaning, and separators break parsing |
  | rounding | never | a rounded number is a different number |

  The shift is an exact decimal one, so no digit appears that was not in the
  file: `0.07` under `0%` converts as `7`, not `7.000000000000001`.

  Dates are unaffected — they were already emitted as ISO rather than as Excel
  serial numbers.

- **`quality` defaults to omitted rather than to `"standard"`, and accepts an
  integer.** Each processor's own default then applies — which it must, because
  they disagree: document and media conversions take a preset name, image
  conversions take a 1-100 integer, and a request carrying `"standard"` for an
  image is rejected by the API. Callers passing `quality="standard"` for a
  document conversion are unaffected; that is the server's default for it.
- `page_range` now raises when passed with an image or media conversion instead
  of being sent to params that do not define it.
- An unconvertible pair is refused before the upload rather than after it.
- Nothing raises differently. `convert()` still raises `MissingDependencyError`
  when a declared requirement is missing and `UnsupportedRouteError` otherwise —
  the error taxonomy continues to split on "is it one of our extras", which is a
  question this SDK can answer for a caller, while `unavailable_kind` answers the
  separate one of whether a fix exists at all.
- **`ConversionResult.engine` is now `Engine | None`.** It is set on every success
  and on every failure that reached an engine; it is `None` only when the requested
  conversion has no route at all, because an unknown extension has no engine and
  naming one would state a fact about the run that is not true.
- **`AsyncConvert` is constructed from the HTTP transport alone.** It also took a
  `files` resource, stored it, and never read it — a dead dependency that made the
  two SDKs' constructors disagree, since the TypeScript `Convert` has always taken
  only the transport. Callers are unaffected: the resource is reached as
  `client.convert` and is not constructed directly.
- **Error decoding recognises two envelope shapes rather than three.** The SDK also
  unwrapped a top-level `{"error": {...}}`, described as coming from "older
  endpoints". No endpoint sends it: every error body declared across the API
  contracts is either flat or `detail`-wrapped, and the only object-valued `error`
  fields belong to *job-status* responses, which error decoding never reads. A
  response in some other shape now reports the HTTP status as its code instead of
  taking a code from an arbitrary nested object.

### Fixed

- **CSV truncation converted one row fewer than its warning claimed.** The row
  cap was charged the header, so a 6,000-row export produced 4,999 data rows
  under a warning saying 5,000. The off-by-one on its own is minor; a truncation
  warning that misreports what it truncated is not, because it is the only thing
  telling the reader the tail is gone at all. The cap now counts data rows, and
  the warning says `data rows` so the two cannot be read past each other.
- **`convilyn local batch` no longer makes one document's Markdown point at another
  document's images.** The engine names extracted assets per document —
  `img-0001.png`, `img-0002.png` — which is unambiguous inside one document and
  collides once a batch renders several into the same directory. The second write
  won, silently: the Markdown still linked to a file that existed, so nothing
  errored and nothing warned, and the reader got the wrong picture.

  **This changes where batch output lands.** Companion files now go under a
  per-document directory:

  ```
  md/
    report.md                        ![…](assets/report/img-0001.png)
    deck.md                          ![…](assets/deck/img-0001.png)
    assets/
      report/img-0001.png
      deck/img-0001.png
  ```

  Anything that globs `assets/*.png` after a batch, or hard-codes the flat path,
  needs the extra segment. The links inside the Markdown are rewritten to match, so
  a rendered document and its images stay a valid pair wherever they are moved.

  **`convilyn local convert` — the single-file path — is unchanged.** It writes
  `assets/img-0001.png` exactly as before. It shares its output directory with
  nothing this call knows about, so it never had the collision, and a path change
  it did not need would be one users pay for and gain nothing from.

  The directory name is the source's stem, which is already required to be unique
  across a batch: two inputs whose outputs would collide are refused before
  anything is written, and that check now protects the assets as well as the
  `.md`.

- **`convilyn convert page.htm --to pdf` no longer fails inside a worker.** The
  CLI had its own extension guess that returned the raw suffix, and since the
  resource only infers when no source format was supplied, the SDK's own mapping
  was unreachable from the command line — `htm` reached a worker that builds its
  format enum bare and raises. Both entry points now share one derivation, which
  also lower-cases and strips a leading dot.
- **A failed conversion no longer reports the wrong engine.**
  `ConversionResult.engine` was the literal `"structured"` for every failure, so a
  failed image conversion — and `convilyn local batch --json` reporting it — named
  an engine that had not run. It now comes from the route the failure occurred on.
- **`convilyn local convert` no longer prints a traceback for a conversion that
  cannot run.** A route can be unavailable with nothing missing (Pillow installed,
  this build unable to write the target), and the command tested only for missing
  requirements — so that case fell through to a `UnsupportedRouteError` nothing
  caught. It now refuses with the same one-line message as every other refusal.
- **Eight links in the PyPI project description no longer 404.** The packaged
  README (`docs/README.md`) linked to `./QUICKSTART.md`, `../CHANGELOG.md`,
  `../examples/`, `../AGENT.md`, `../LICENSE` and `./STABILITY.md` relatively.
  Those resolve against GitHub when the file is read in the repository, and
  against `pypi.org` when it is read as the project description — where none of
  them exist. Every link is now absolute to the public mirror, which is correct
  in all three places the file is rendered.
- **The project description no longer omits image conversion and PDF page
  operations.** Both shipped (1.5.0b1, 1.6.0b1) while the packaged README still
  described offline conversion as documents-to-Markdown only, so the two newest
  offline capabilities were invisible to anyone reading the PyPI page. It also
  opened by exporting an API key, which is not needed for anything under
  `convilyn local`; the zero-setup path now comes first.

### Documentation

- **QUICKSTART covers `out=` and `overwrite=`.** It showed only `to=`, so the
  natural guess for "put it here" was `output=` — which is a `TypeError` — and
  nothing said that `to` and `out` are mutually exclusive or that a second run
  needs `overwrite=True`. All three were in the docstrings and in no document.
  `plan(out=...)` is shown alongside, since it answers the same question without
  converting. `docs/README.md` gets the short form of the same three facts, so the
  PyPI page and the guide do not disagree.

- **QUICKSTART warns that Git Bash rewrites `api` arguments on Windows.** MSYS2
  converts anything shaped like a Unix path before the program runs, so
  `convilyn api GET /api/v1/health` requests
  `C:/Program Files/Git/api/v1/health` and returns **404** — a symptom that reads
  as a broken backend and has been misdiagnosed as one. The fix is
  `export MSYS2_ARG_CONV_EXCL='*'`.

  Not an SDK defect — every program invoked that way sees the same rewritten
  argument — but `api` is the escape hatch this package points people at, and it
  is the one command whose arguments are always paths.

- The `[Unreleased]` section above had two `### Changed` headings and two
  `### Fixed` headings, and four entries that add API (`goals.to_markdown`,
  `Route.unavailable_kind`, `UnavailableKind`, `--json` carrying
  `unavailable_kind`) were filed under `### Fixed`. Regrouped without changing
  any wording, so the next release's notes describe additions as additions.

## [1.6.0b1] - 2026-08-13

### Added

- **PDF page operations, offline — `convilyn.local.pdf`.** Merge, select pages,
  split into single pages, rotate, compress, and add or remove a password, all
  on the local machine with no API key:

  ```python
  from convilyn.local import pdf

  pdf.merge(["a.pdf", "b.pdf"], "combined.pdf")
  pdf.select("report.pdf", "summary.pdf", pages="1-3,10")
  pdf.burst("scan.pdf", "pages/")
  ```

  A separate namespace from `convert` because these are not conversions:
  a PDF goes in and a PDF comes out, rearranged. Needs the `pdf` extra.
- **`convilyn local pdf` CLI group** — `merge`, `select`, `split`, `rotate`,
  `compress`, `protect`, `unlock`, `info`. `protect` and `unlock` prompt for the
  password when it is omitted, so it stays out of shell history.
- `PdfOperationError`, under `LocalError` like the rest of the taxonomy.

### Fixed

- **The CLI no longer crashes on a console that cannot encode its glyphs.**
  A Windows console left on `cp437` cannot represent `✓` or an em dash, and
  Python raises from inside `print` rather than dropping the character — so a
  finished conversion ended in a traceback *after* the file had been written.
  Unencodable characters now degrade to a visible escape and the message
  survives.
- A malformed page range reported `invalid literal for int() with base 10:
  'oops'`. It now names what was typed and what was expected.

### Changed

- `MissingDependencyError.route` is now optional. A page operation has no route,
  and inventing one to describe a missing package would have put a fictional
  value in the attribute callers are meant to trust. Existing callers are
  unaffected — the parameter keeps its position and meaning.

## [1.5.0b1] - 2026-08-13

### Added

- **Image conversion, offline.** `convilyn local convert photo.png --to webp`
  converts between the raster formats Pillow supports on your machine — no API
  key, no network, no account. Alpha is composited when the target cannot carry
  it, so a transparent PNG becomes a JPEG rather than an error.
  - Install with `uv add "convilyn[images]"` (or `pip install "convilyn[images]"`).
  - Images are refused above 40 megapixels. A file's declared dimensions are
    checked before it is decoded, so a decompression bomb costs a header read.
- **Every known conversion now appears in `capabilities()`, available or not**,
  and an unavailable one says what would fix it. Asking for `heic` on a machine
  without the HEIF codec previously returned nothing — indistinguishable from a
  typo — and now returns a route naming `pillow-heif`. Where nothing would fix
  it, the reason says so instead of naming a package that cannot help.
- **`convilyn goals understand` — the CLI counterpart of
  `goals.understand()`.** Runs a grounded, schema-constrained understanding
  over one or more uploaded files straight from the shell:

  ```
  convilyn goals understand --files file_abc,file_def \
      --schema-file invoice.schema.json \
      [--instructions "totals only"] [--timeout 300] [--json] [--dry-run]
  ```

  `--json` emits a single object — `{command, file_ids, result}` — with the
  grounded result under `result` and no duplicated pretty-print riding along;
  human mode prints the indented result, because the result *is* this
  command's output. `--dry-run` reads and validates the schema file, prints
  the would-be request, and makes no network call.

  A missing, unreadable, non-JSON, or non-object `--schema-file` exits `1`
  with one error line — before any network call — instead of a traceback.
  `UnderstandUnavailableError` (the connected platform has not rolled the
  capability out) exits `2`: it is a property of the backend, not of your
  arguments. No public API surface changed — the command calls the existing
  `client.goals.understand(...)`.

### Changed

- `convilyn local formats` groups by source format. The image engine alone
  produces several hundred routes and the flat list had become unreadable; the
  `--json` payload is unchanged and still carries every route individually.
- `capabilities()` is cached for the process. Building it probes every codec
  with a one-pixel save, which a batch was otherwise paying for once per file.

### Fixed

- Converting to a format Pillow can read but not write (`psd`, `pcd`) raised
  `ConversionFailedError` wrapping a `KeyError` from deep inside the encoder. It
  now raises `UnsupportedRouteError` before opening the file, carrying the same
  sentence `capabilities()` would have shown.
- **LibreOffice conversions no longer fail when LibreOffice is already open.**
  It refuses to start a second instance against a profile another one holds,
  exiting non-zero with nothing in stderr — so converting an `.odt` while the
  desktop application was running reported "conversion failed" and no reason.
  Conversions now run against a profile of their own, kept under the user's
  cache directory. Deleting it is safe; it is rebuilt on the next conversion.

## [1.4.0b1] - 2026-08-13

### Added

- **Legacy, OpenDocument and ebook formats now convert**, rather than being
  declared and unavailable. `.doc`, `.odt`, `.rtf`, `.xls`, `.ods`, `.ppt` and
  `.odp` go through LibreOffice; `.epub`, `.mobi` and `.azw3` through Calibre.
  Each is converted once into the modern sibling this engine already reads well,
  so all ten inherit headings, tables and embedded images without a second
  parser being written for any of them.
  - The result reports the **original** format, not the sibling's, and carries a
    warning saying which sibling was used. Nothing about the route is hidden.
  - Neither program can be installed from PyPI, so neither is an extra.
    `convilyn local doctor` reports which are present and where to get the rest.

### Changed

- A conversion that reaches a vanished external program now raises
  `MissingDependencyError` rather than `ConversionFailedError`. The two are
  acted on differently — install something, or look at the file.
- `convilyn.local` internals are renamed to make the layering legible: a leading
  underscore now means internal, with no exceptions (`_probe`, `_tools`, `_run`,
  `_routes`, `_engine`). No public symbol moved; `convilyn.local.__all__` is
  unchanged. A contract test holds the rule.

## [1.3.0b1] - 2026-08-13

### Added

- **Offline file conversion — `convilyn.local`.** Converts PDF, Word,
  PowerPoint, Excel, CSV, XML and plain text to Markdown entirely on the local
  machine: no API key, no network, no account, no quota. Structure survives —
  headings, lists and tables — and embedded images are written to an `assets/`
  directory beside the Markdown so their links resolve.
  - `convert`, `convert_many`, `plan`, `capabilities`, `detect_format`, and the
    thread-offloading `aconvert` / `aconvert_many`.
  - `capabilities()` and `plan()` **never raise**: a missing dependency is
    reported as a `Route` with `available=False` and a sentence saying what to
    install. `convert()` raises typed errors; `convert_many()` returns a result
    per file so one bad input does not stop a batch.
  - Semver-covered from this release — see `docs/STABILITY.md`.
- **`convilyn local` CLI** — `convert`, `batch`, `formats`, `doctor`. Works with
  no credential configured.
- **Optional extras, one per format family** — `pdf`, `docx`, `pptx`, `xlsx`,
  `xml`, `images`, plus the composites `documents` and `all`. Install only what
  you read; plain text, CSV and Markdown rendering need nothing. Extra *names*
  are semver-covered; the distributions behind them are not.

### Notes

- Legacy and OpenDocument office formats and ebooks are **declared but not yet
  runnable**: they need LibreOffice or Calibre, which no extra can install.
  `capabilities()` reports them with instructions rather than hiding them, so
  the missing piece is discoverable.
- `pip uninstall convilyn` does **not** remove an extra's packages — a pip
  limitation. `uv remove` / `uv tool uninstall` do. See QUICKSTART §1.

## [1.2.0b14] - 2026-07-26

### Fixed

- A caller-supplied `poll_interval` is now clamped to a `MIN_POLL_INTERVAL`
  (0.2s) floor in both the goal-lane and convert wait loops. `poll_interval=0`
  previously produced an unbounded request rate for the whole timeout window:
  the stale-progress backoff is multiplicative, so a zero never grew, and
  `asyncio.sleep(0)` yields without waiting. The clamp is applied at the single
  loop both public waiters funnel through, so no entry point can bypass it.
  A slower cadence than the floor is left untouched.

## [1.2.0b13] - 2026-07-21

### Changed

- Install and development instructions are now uv-first across the README and examples (pip remains a documented alternative). No API or behaviour change.

## [1.2.0b12] - 2026-07-21

### Changed

- Documentation polish across the public surface: docstrings, guides, examples, and this changelog now use plain product language throughout (internal shorthand and tracker references removed). No API or behaviour change.

### Added

- **`goals.understand(files, *, schema, instructions=None)` — grounded,
  schema-constrained understanding.** Returns a result that
  conforms to `schema` (a plain JSON Schema dict — language-neutral, no new
  client-side validation dependency) and is grounded by the platform before it
  is returned, instead of a freeform `goal_text` answer. **Safe-degrade:** when
  the connected platform does not yet support schema-grounded understanding, it
  raises the new `UnderstandUnavailableError` rather than silently returning an
  ungrounded result (402/429/5xx propagate unchanged). async + sync. `extract()`
  is now **deprecated** in favour of `understand()` (see _Deprecated_ below).
- **`client.builder` — chat-driven workflow authoring.** Build a `uw_`
  workflow by conversation from your own client (parity with the web app):
  `create_session()` → `send_message()` → on a `register` verdict read
  `BuilderTurn.registered_workflow_id` and hand it to `goals.run(...)`. Also
  `get_session()`, `messages()`, and `quota()` (async + sync). Requires a
  Pro-tier account (402 `TIER_REQUIRED`; the `discover` sub-mode is exempt). New
  public types `BuilderSession` / `BuilderTurn` / `BuilderMessage` /
  `BuilderMessageList` / `BuilderPendingSlot` / `BuilderAttachment` /
  `BuilderQuota`.

### Deprecated

- **`goals.extract()` — superseded by `goals.understand()`.** `extract()`
  now emits a `DeprecationWarning`. It runs a single fixed workflow with no
  caller control over the output shape, whereas `understand(files, schema=...)`
  returns a result that conforms to a caller-supplied JSON Schema and is grounded
  by the platform before it is returned. `extract()` keeps working unchanged (a
  thin wrapper over the same `run() → artifacts() → parse` machinery
  `understand()` reuses) for back-compat; migrate to `understand()` for a
  guaranteed, grounded shape.

## [1.2.0b11] — 2026-07-21

### Added

- **`client.user_workflows` — typed management namespace for the workflows you
  author.** `list()` (cursor-paged), `get()`, `runs()`, `export()`
  (portable JSON document + `X-Export-Schema-Version`), `delete()` (409
  `WORKFLOW_IS_PUBLIC_USE_ARCHIVE` while public). async + sync. Wraps the
  curated `/user_workflows/*` management subset now declared in the SDK
  contract (`sdk_public_openapi.yaml`); pairs with
  `goals.run(user_workflow_id=…)` for running them and `client.builder` for
  authoring them. New top-level models: `UserWorkflowSummary`,
  `UserWorkflowsPage`, `UserWorkflowDetail`, `UserWorkflowRun`,
  `UserWorkflowExport`. Community-gallery workflows by other authors remain
  under `client.workflows` — the two namespaces stay deliberately distinct
  (minor, additive).
- The public API-surface test now also covers the `builder` resource methods
  and the seven client resource accessors.

## [1.2.0b10] — 2026-07-18

### Added

- **`goals.start(user_workflow_id=...)` / `run(...)` / `run_interactive(...)`
  (async + sync) — run a Builder-authored workflow (`uw_...`) on the typed SDK
  surface.** Previously only built-in catalog workflows (`workflow_id=`) or
  natural-language goals (`goal_text=`) were typed parameters; a user-authored
  workflow had to be sent through the `raw_request` escape hatch. The three
  workflow sources (`workflow_id` / `user_workflow_id` / `goal_text`) are now
  mutually exclusive and validated client-side (exactly one required). A
  `user_workflow_id` run may start with no `files` (it can collect them via
  checkpoints); only the `goal_text`-only NLP path still requires `files`. The
  `convilyn goals start` CLI gains a matching `--user-workflow-id` flag. Wire
  key: `userWorkflowId` (already on the create contract; this exposes it as a
  first-class SDK argument).

## [1.2.0b9] — 2026-07-18

### Fixed

- **`PlanTier` now includes `"business"`.** A business-tier account's
  `account.get_plan()` / `account.get_quota()` response previously raised a
  pydantic validation error because `PlanTier` was `Literal["free", "pro"]`.
  The literal now mirrors the backend plan catalog (`free` / `pro` /
  `business`). `get_plan()`'s docstring no longer references a phantom
  `/billing/plan` endpoint — `cost-preview` is documented as the SDK's
  canonical tier source (the `ck_`-accepting endpoint that returns
  `quotaCheck.tier`; the web app's JWT-only `/payment/subscription` is not
  reachable with a `ck_` key).

### Tests

- **Public-surface contract test truthed up to the shipped surface.** The
  keystone guard (`tests/contract/test_public_surface.py`) had drifted: its
  frozen sets never caught up with three already-released additions —
  `goals.extract()` (`1.2.0b6`), `client.files.list()` + its `FileList` /
  `StoredFile` / `StorageUsage` exports (`1.2.0b8`), and `goals.run_interactive()`
  (`1.2.0b8`) — so the guard failed against the code it is meant to protect. The
  frozen `__all__` and per-resource method sets now match the shipped surface.
  No public API change — the contract snapshot catches up. This unblocks the
  pre-publish gate (a red keystone test is a publish blocker).

## [1.2.0b8] — 2026-07-13

### Added

- **`goals.run_interactive(on_slot=, on_preview=)`** (async + sync) — drives the
  whole human-in-the-loop lifecycle to a terminal state so callers no longer
  hand-roll the `slots_pending → fill_slot → confirm → wait` loop. Reacts to
  each stop: `slots_pending` → `on_slot(slot, job)` for each pending slot →
  `fill_slots`; `ready` → `confirm`; `ready_with_preview` → `on_preview(job)`
  (default approve) → `confirm`/`cancel`; terminal → return. Callbacks may be
  sync or async. A `max_rounds` guard (`GoalJobTimeoutError(reason="rounds")`)
  bounds a runaway callback. Reuses the existing `wait`/`fill_slots`/`confirm`
  primitives — no new endpoint, no changed semantics. (Compiled/silent-mode
  workflows never stop for input, so this just runs them to completion.)

### Added

- **`client.files.list()`** (async + sync) — lists your **durable** stored
  files (e.g. emailed-in attachments) with a storage-usage summary
  (`used_bytes` / `free_bytes` / `over_quota`). Returns typed `FileList` /
  `StoredFile` / `StorageUsage`. Note: ordinary uploads are ephemeral and are
  removed by the platform's ~1-hour cleanup, so a just-uploaded transient file
  is **not** listed here — this surfaces durable storage only. An
  unauthenticated caller gets an empty list.

## [1.2.0b6] — 2026-07-12

### Added

- **`goals.extract(files)`** (async + sync) — one-call document extraction.
  Sugar over `start()` → `wait()` → `artifacts()` for the common "image/PDF →
  one JSON object" case, so single-step extraction no longer pays the full
  Goal Lane lifecycle boilerplate (start/wait/fetch/parse). Runs the platform's
  document-extraction workflow and returns the parsed JSON of the job's primary
  JSON artifact. It is **not** a new inference product — the understanding comes
  from the same platform workflow; this only collapses the run-then-fetch-then-
  parse dance into one method. A caller-supplied output `schema` is not yet
  supported (roadmap); to steer the extraction, call `run()` + `artifacts()`.

## [1.2.0b5] — 2026-07-11

### Added

- **Status-aware waiting: `goals.wait(..., idle_timeout=)` / `goals.run(..., idle_timeout=)`.**
  Long agentic runs (7–9 min analyze/execute phases are normal) made the flat
  300 s `timeout` a bad trade-off — give up on healthy jobs or wait forever on
  wedged ones. `idle_timeout` bounds the time tolerated **without any status
  or progress change**; a job that keeps advancing holds the loop open. On an
  idle trip, `GoalJobTimeoutError.reason == "idle"` (total-budget trips carry
  `"total"`); the message says the job may still be healthy on a long phase.
  Fully backward compatible — omitted, behaviour is unchanged.

## [1.2.0b4] — 2026-07-11

### Added

- **`client.files.delete(file_id)`** (async + sync) — deletes an uploaded
  file's cloud copy (storage object + metadata record) the moment you are
  done with it, instead of waiting for the platform's ~1-hour automatic
  cleanup. Only the uploader can delete (404 otherwise); a file attached to
  a still-running job returns 409 `FILE_IN_USE`. Aimed at privacy-sensitive
  callers (e.g. edge devices processing family documents) who want
  deterministic control over cloud retention.

## [1.2.0b3] — 2026-07-11

### Fixed

- **`goals.confirm()` without `expected_version` no longer fails.** The SDK
  used to send a body-less POST when no version was supplied; backends that
  declare the confirm body as a required parameter rejected it with a
  validation error before the handler ran. The SDK now always sends a JSON
  object (`{}` when empty). Server-side, the confirm body is also optional
  now, and `expectedVersion` omission on `fill_slots` is documented: the
  server conditions the write on the version it just read, so you only need
  to pass `job.item_version` when you want strict read-your-write locking.

### Docs

- `fill_slots()` / `confirm()` docstrings now spell out the optimistic-locking
  semantics (when to pass `expected_version`, what a 409 means).

## [1.2.0b2] — 2026-07-11

### Added

- **`client.workflows.catalog()`** (async + sync) — lists the platform's
  built-in goal-lane workflow catalog (`GET /workflows/catalog`), returning
  the new `CatalogWorkflow` type (workflow id, name, supported inputs,
  locales, `tier` / `free_tier_allowed` gate hints). Previously
  `workflows.search()` only reached the user-published community listing,
  so the built-in catalog was invisible to SDK callers.

## [1.2.0b1] — 2026-07-11

### Added

- **AI-workflow output artifacts are now reachable from the SDK.** Three new
  methods on `client.goals` (async + sync):
  - `goals.artifacts(job_spec_id)` → `list[Artifact]` — every output artifact
    of a completed/partial job, each with a presigned `download_url` valid
    for 1 hour;
  - `goals.download_artifact_url(job_spec_id, artifact_id)` →
    `ArtifactDownload` — mint a fresh presigned URL for one artifact;
  - `goals.download_artifact_to(job_spec_id, artifact_id, to=...)` — stream
    one artifact to disk (same size-capped streaming + symlink refusal as
    `convert.download_to`).
  Previously the only way to retrieve an AI-workflow's output was to call
  `GET /jobs/goal/{id}/artifacts` by hand outside the SDK.
- New public types `Artifact` and `ArtifactDownload` (exported at top level),
  wired into the conformance harness against the contract's new
  `OutputArtifact` / `DownloadInfo` schemas.

## [1.1.1b5] — 2026-07-10

### Fixed

- **`import convilyn` no longer crashes on Python 3.10.** `client.py` /
  `sync_client.py` used `typing.Self` (Python 3.11+, PEP 673) despite the
  package declaring `Requires-Python: >=3.10`; both now fall back to
  `typing_extensions.Self` on 3.10 (already an unconditional dependency for
  `python_version < '3.11'`, just not wired up until now). A second,
  previously-masked 3.10 incompatibility in `_internal/throttle.py`
  (`from datetime import UTC`, also 3.11+ — hidden behind the `typing.Self`
  crash until that one was fixed) is corrected the same way, using
  `datetime.timezone.utc`, which has always been available.

## [1.1.1b4] — 2026-07-09

### Fixed

- Public-mirror CI is now green: the SDK source is `ruff format`-clean (formatting
  was previously unenforced on `sdk/`), the secret-scan uses `detect-secrets-hook`
  (the old step's `git diff --exit-code` always failed on detect-secrets' volatile
  `generated_at` timestamp), and the broken typecheck step (`pyright`/`mypy`, neither
  shipped) was removed.

### Changed

- Pin `ruff==0.15.6` in the `dev` extra and enforce `ruff format` on the SDK tree so
  local formatting never drifts from the mirror CI.

## [1.1.1b3] — 2026-07-08

### Docs

- Remove references to the not-yet-published TypeScript / Go SDKs — Python is
  the only SDK live today; the multi-language framing returns when they ship.

## [1.1.1b2] — 2026-07-08

### Docs

- Correct the README licence line to **Apache-2.0** (matches `LICENSE` +
  the `[project] license` field; the `1.1.1b1` README still said MIT).

## [1.1.1b1] — 2026-07-07

### Security

- **Storage URLs are validated before the client dials them.** The
  client now rejects an upload/download URL whose host resolves to a
  loopback, link-local, or private address (in addition to the existing
  https-only check), so a malformed or tampered response cannot redirect
  an upload or download to an internal target.
- **Downloads are streamed with a size cap** instead of being buffered
  whole in memory, so a very large or hostile response cannot exhaust
  memory. Uploads are likewise capped (`MAX_UPLOAD_BYTES`) and fail fast.
- **`base_url` must be https** for any non-loopback host — the API key
  travels in an `Authorization` header, so an `http://` target is
  refused to avoid sending it in cleartext. Loopback hosts may use http
  for local development.
- **WebSocket URLs must be `wss://`** for any non-loopback host, and
  connection errors no longer include the auth token from the URL.

### Changed

- Docstrings, README, and CHANGELOG were revised for clarity; no public
  API changed.

## [1.1.0] — 2026-07-07

First published release (PyPI). Sections below accumulated since 1.0.1;
the `Removed` entries predate any published version, so no released
consumer is affected.

> **Events are polling-only in v1**: retrieve goal progress with
> `client.goals.wait(...)` / `retrieve(...)`. The WebSocket gateway does
> not accept consumer `ck_` keys yet; `goals.events()` streaming is
> roadmap (see `docs/STABILITY.md`).

### Fixed

- `files.upload` now speaks the backend's presigned-**POST** upload grant: when the presign response carries `fields`, the SDK multipart-POSTs (fields verbatim, file part last) instead of PUTting — the backend switched input uploads to a size-capped S3 POST policy (`content-length-range`) and a PUT against the POST URL fails with S3 403. A grant without `fields` still uses the legacy presigned-PUT path, so the SDK works against both backend generations.
- The synchronous `Convilyn` client now runs every call — and the final `close()` — on **one private, long-lived event loop** instead of a fresh `asyncio.run` loop per call. Per-call loops orphaned pooled `httpx` connections, which touched their already-closed loop at interpreter teardown and crashed the CLI on Windows (`RuntimeError: Event loop is closed`). Calling a sync method after `close()` now raises a clear `RuntimeError`, and calling one from inside a running event loop still raises with guidance to use `AsyncConvilyn`.
- A **failed** conversion job now surfaces `JobFailedError` even when the backend attaches a 0-byte placeholder result file. `ResultFile.size` was `Field(gt=0)`, so parsing a failed job whose `resultFiles[0].size == 0` raised a pydantic `ValidationError` instead — masking the real failure. The bound is now `ge=0`.
- `convert.create` now sends the discriminated-union tag as snake_case `processor_type` (was camelCase `processorType`); the file-conversion `JobRequest` discriminator is `processor_type` per the contract (`processorType` is only the *response* field name), so the previous key made the backend reject every conversion with HTTP 400 `union_tag_not_found`. File conversions now succeed against the current backend.
- The CLI `--json` output now escapes non-ASCII (`ensure_ascii=True`); a raw glyph in a payload (e.g. a `✓` from a job) previously crashed with `UnicodeEncodeError` on a non-UTF-8 console (Windows `cp950`).
- `goals.start(slots=...)` now sends the answers as `slotAnswers` (`[{slotId, value}]`); the previous `slots` payload had no matching field on the create endpoint and was silently dropped, so pre-seeded slot answers never reached the backend.
- Goal-job parsing now coerces wire-`null` `pendingInterrupts` / `pendingSlots` / `fileIds` / `filledSlots` to an empty list/dict via a before-validator; the backend legitimately returns `null` for these, which previously raised a validation error on the non-optional fields and made the whole `GoalJob` unparseable.
- File uploads from a path now send a length-bearing bytes body (so httpx emits `Content-Length`); the previous async-generator body made httpx use `Transfer-Encoding: chunked`, which an S3 presigned PUT rejects with HTTP 501. Path-based uploads now succeed against real storage.

### Changed

- **`goals.events()` failure now points at `wait()` polling.** The WS gateway
  does not accept `ck_` keys in v1, so a connect failure's `WebSocketError`
  message and the method docstring now spell out that WebSocket streaming is
  polling-only for now (use `wait()`).
- **Author-SDK / developer-portal tokens (`cvl_` / `cvi_`) are now rejected**
  by `APIKey` / `Convilyn(api_key=...)` with a precise `AuthError`, instead of
  being treated as acceptable consumer keys (they never authenticated against
  the data plane — the backend answered with an opaque 401). The `ck_` prefix
  and any unknown prefix are still accepted (forward-compat).

### Removed

- **BREAKING: `GoalJob.workflow_id`** — the backend's `GoalJobResponse` never
  echoes `workflowId` (the *request* accepts it; the response does not), so the
  attribute was always `None` at runtime. Removed pre-first-publish, so no
  released consumer is affected. `goals.start(workflow_id=...)` /
  `run(workflow_id=...)` and the `Workflow` / `WorkflowSummary` models are
  unchanged. `GoalJob` is now conformance-mapped in `sdk/sdks.json`, so any
  future field the wire doesn't speak fails CI instead of shipping silently.

### Added

- **`client.goals.start(..., llm_config_id=...)` / `run(..., llm_config_id=...)`**
  — optionally pin a goal run to one of your stored BYO-LLM provider configs
  (created in the console) so the run executes on your own provider/key. Omit it
  to use your account default. Serialised as `llmConfigId`; honoured only when
  BYO-LLM is enabled for your account, otherwise the run uses the platform
  provider.

### Fixed

- **Default API base URL** — corrected to `https://api.convilyn.corenovus.com`
  (was `https://api.convilyn.com`, which does not serve the API), so a default
  `Convilyn()` reaches the real backend out of the box. The base URL is the host
  root — resource paths carry their own `/api/v1` prefix.
- **API-key prefix** — the canonical consumer key is now `ck_` (minted in the
  API Console / Settings → API), matching the backend (`USER_API_KEY_PREFIX`)
  and the docs. `ACCEPTED_KEY_PREFIXES` now includes `ck_` (the developer-portal
  `cvl_` / `cvi_` tiers stay recognised); the quickstart + `convilyn doctor`
  examples show `ck_`. The runnable `examples/*` now lead with `ck_` too,
  completing the alignment.
- **Stale post-rename references** — after the `sdk-consumer` →
  `sdk-consumer-python` directory rename, the `pyproject.toml`
  `[project.urls]` (Changelog / Source Code), the `examples/03` AGENT.md
  reference, and the `examples/README.md` test-path link now point at
  `sdk-consumer-python`.

### Added

- **`CONVILYN_BASE_URL` environment override** — the client now honours the
  `CONVILYN_BASE_URL` env var (precedence: explicit `base_url=` arg →
  `CONVILYN_BASE_URL` → default), so the CLI and SDK can target a dev/staging
  API without code changes. `convilyn doctor` already surfaced this var and now
  reports the URL the client actually dials.
- **Public-API contract test** (`tests/contract/test_public_surface.py`) —
  freezes `convilyn.__all__`, the per-resource method sets, and the
  exception taxonomy, and fails if any `convilyn._internal` symbol leaks
  into the public namespace or the surface grows unexpectedly. The keystone
  guard behind the SemVer promise; see `docs/STABILITY.md`.
- **`convilyn.config`** — a public module home for the resilience config
  types (`RetryPolicy`, `ExponentialBackoffRetry`, `NoRetry`,
  `AutoThrottleConfig`). They are still re-exported from the top-level
  `convilyn` namespace, so `from convilyn import RetryPolicy` is unchanged —
  but the documented home is now a non-underscore module rather than
  `convilyn._internal`.
- **`docs/STABILITY.md`** — the published stability & versioning policy:
  what the public surface is, the SemVer promise, the deprecation policy,
  and the documented `raw_request` escape-hatch caveat.

- **`Convilyn(auto_throttle=...)`** — opt-in retry loop for
  `QuotaExceededError`. Pass `True` for the default policy (1 retry,
  60 s sleep cap, 5 s fallback delay) or an `AutoThrottleConfig` /
  dict for tuned knobs. The SDK reads the server's
  `details.retry_after_seconds` / `details.reset_at` hint when present
  and gives up immediately if the implied sleep exceeds `max_sleep`,
  so a misconfigured caller never blocks indefinitely.
- **Soft-limit signalling** — any response carrying the
  `X-Quota-State: soft_limit` header now emits a
  `convilyn.throttle` log warning + Python `UserWarning` and returns
  normally (forward-compat wiring; the backend will emit the header
  once the gateway support lands).
- **`client.account.usage_history(*, since=None)`** — list past usage
  periods (one row per metric+period). Wraps
  `GET /api/v1/payment/usage/history`; the new `UsageHistoryEntry`
  model carries `metric`, `period_start`, `period_end`, `used`, and
  optional `limit`. Pair with `client.account.get_quota()` for MTD
  spend reviews.
- `CostEstimate` now exposes `estimated_total_micro_u`,
  `estimated_min_micro_u`, and `estimated_max_micro_u` (wire aliases
  `estimatedTotalMicroU` / `estimatedMinMicroU` / `estimatedMaxMicroU`).
  Use these to render the projected cost range; the legacy
  `estimated_micro_u` upper-bound field stays for back-compat.

### Changed

- Renamed event types on `GoalEventType`: `specialist_started` →
  `agent_step_started`, `specialist_finished` → `agent_step_finished`,
  `handoff` → `orchestration_transition`. CLI glyphs follow.

### Removed

- `CostEstimate.max_iterations` and
  `CostEstimate.llm_cost_per_iter_micro_u` are no longer surfaced on
  the model. Callers should rely on the new cost-range triple
  (min / total / max).

### Documentation

- The package (`convilyn/__init__.py`) and async-client (`client.py`)
  docstrings now reflect the shipped resource surface — they previously
  said resources "land in subsequent releases" / "follow-up commits"
  while `client.files` / `convert` / `goals` / `workflows` / `account`
  already ship.

## [1.0.1] — 2026-06-30

### Security

- **`https`-only scheme guard on backend-supplied URLs.** `external_get` /
  `external_put` (the presigned-URL download/upload paths) now reject any URL
  whose scheme is not `https` (e.g. `http://`, `file://`, an internal address
  over plain HTTP). This is defence-in-depth against a compromised or MITM'd
  backend returning a downgrade/SSRF URL. Scheme — not host — is checked, so
  every legitimate https presign host (S3, CloudFront, custom domains) still
  works.
- **`convert.download_to` refuses to write through an existing symlink** at the
  destination path, so a pre-placed link cannot redirect the downloaded bytes.
  Writing to a regular or new path is unaffected.
- **`convilyn doctor` secret masking tightened** — diagnostics now reveal only
  the first 3 characters (the key tier prefix) and no longer print the trailing
  4 characters of an API key.

## [1.0.0] — 2026-05-24

First production release. The package surface has stabilised across the
pre-1.0 development cycle; the bump from `0.1.0` reflects API readiness, not a
breaking change.

### Added

- **`client.account` resource** — `get_plan()` returns the caller's
  billing tier; `get_quota(tools=..., max_iterations=...)` previews
  workflow cost + returns the tier's quota verdict (`ok` /
  `soft_limit` / `quota_exceeded`). Read-only, no side effects.
- **`convilyn account` CLI** — `convilyn account plan` and
  `convilyn account quota` mirror the resource. Both support `--json`
  for pipe consumption.
- **Typed billing exceptions**: `PlanRequiredError` (HTTP 402 +
  `TIER_REQUIRED`) and `QuotaExceededError` (HTTP 402 +
  `QUOTA_EXCEEDED`). Both subclass `APIError`, so existing
  `except APIError:` handlers continue to catch them. Each carries an
  `upgrade_url` for the caller's pricing CTA.
- **`client.workflows` resource** — community marketplace surface:
  `search`, `get`, `fork`, `publish`, `patch`, `like`.
- **`client.goals` resource** — agentic AI workflows: `start`,
  `wait`, `run`, `retrieve`, `fill_slot`, `confirm`, `cancel`, `retry`,
  plus async-only `events()` WebSocket streaming.
- **`convilyn goals` CLI** — drive AI workflows from the shell.
  NDJSON streaming for `events`; pinned exit codes (0 / 1 / 2 / 3 / 130).
- **`convilyn convert` CLI** + `client.convert` + `client.files`
  resources.
- **`convilyn doctor` CLI** — environment + connectivity diagnostics
  for the SDK's dependencies and auth setup.
- **`convilyn api` CLI** — `gh`-style escape hatch for any backend
  endpoint the SDK has not wrapped yet.
- **Production-grade resilience**: retry on 5xx / 429 / 408 with
  exponential backoff + jitter, `Idempotency-Key` auto-stamped on
  mutating verbs, `Retry-After` honoured.

### Changed

- Error envelope handling normalises three shapes: flat
  `{code, message, ...}`, FastAPI `{"detail": {...}}`, and the older
  `{"error": {...}}`. Callers now see the same typed exception
  regardless of which endpoint raised.
- License moved from speculative `Apache-2.0` placeholder to `MIT`
  (matches the repo's existing precedent under `llm-gateway/LICENSE`).

### Documentation

- `docs/QUICKSTART.md` — 5-min Python + CLI walkthrough.
- `docs/README.md` — PyPI landing page.
- `AGENT.md` — SOLID seams + extension points for AI coding agents
  contributing to the SDK.
- 9 runnable examples under `examples/01_*.py` … `09_account_quota.py`.
