# Changelog — `convilyn` (consumer SDK)

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/).

## [4.0.0] - 2026-09-03

### Changed — BREAKING

- **The five MCP tools lost their `convilyn_` prefix.**

  | 3.x | 4.0.0 |
  |---|---|
  | `convilyn_convert` | `convert` |
  | `convilyn_capabilities` | `capabilities` |
  | `convilyn_pdf` | `pdf` |
  | `convilyn_understand` | `understand` |
  | `convilyn_quota` | `quota` |

  The host already namespaces every MCP tool by server, so a plugin install
  exposed `mcp__plugin_convilyn_convilyn__convilyn_convert` — `convilyn` three
  times in one identifier, the last of which told the model nothing the
  namespace had not already said.

  **Migration.** Anywhere you named a tool — a permission rule, an
  `allowed-tools` list, a subagent `tools` field, a hook matcher — drop
  `convilyn_` from the tool segment and leave the host's namespace alone:

  ```
  mcp__plugin_convilyn_convilyn__convilyn_convert  ->  mcp__plugin_convilyn_convilyn__convert
  mcp__convilyn__convilyn_convert                  ->  mcp__convilyn__convert
  ```

  Nothing else about the tools changed: same inputs, same behaviour, same
  results. A saved prompt that names a tool needs the one-word edit above;
  a caller that only uses the Python API or the CLI is unaffected.

  **Why it was renamed rather than left alone.** The previous release argued
  for keeping the prefix precisely because renaming is breaking. That settles
  the mechanism — a major version — not the question; a public surface does not
  get to stay wrong permanently because correcting it costs a major.

  **Why there is no deprecation window**, which is a documented departure from
  this project's usual policy: a deprecated MCP tool has to stay *registered*
  to keep working, so a window would have shipped ten tools for at least one
  minor release. That doubles a catalogue whose small size is the property
  worth protecting — every description is re-sent to the model on every turn —
  and exceeds the description budget this package holds itself to. MCP also has
  no deprecation channel a client acts on, so the "warning" could only be prose
  in a description the caller pays for every turn. `docs/STABILITY.md` carries
  the carve-out and the full reasoning.

### Changed

- **`convert` now publishes a real `outputSchema`**, so a host can validate what
  it returns instead of being told "any object".
  `Annotated[CallToolResult, Envelope]` keeps the return type — so `isError`
  stays ours — *and* derives the published schema from `Envelope`.

  It is the only one, and the reason is narrow: every field in `ConvertEnvelope`
  is declared under the name it is sent under, so the schema the library derives
  and the payload the tool sends cannot disagree. `quota` is **deliberately
  excluded** — `CostEstimate` carries camelCase aliases, and a schema derived
  from it advertises `estimatedMicroU` while the payload sends
  `estimated_micro_u`, which every client that validates output schemas rejects.
  `pdf` and `capabilities` have multiple success shapes that a top-level union
  cannot express through this path, and `understand`'s `result` is by
  construction the caller's own JSON Schema output.

  A modelled shape converts silent payload drift into a loud failure. That is
  the point, and it is a new failure mode — worth taking on the one tool whose
  shape is stable and alias-free, not on the four where it is not.

- **A refused call now sets `isError` on the wire**, in addition to the
  `{"ok": false, "error", "hint"}` body it already returned. A host keying off
  the protocol flag previously saw an unbroken run of successes while the model
  was reading refusals. The body is unchanged — the flag was added, not traded
  for it.

  **A refusal is what sets it, not merely `ok: false`.** A batch where one file
  of twelve failed returns `ok: false` with eleven good rows — a call that did
  its job, and `convert`'s contract is explicit that a failed file is a result
  rather than an exception. Flagging it as a tool execution error told the host
  the call would have failed when it had not. It also matters more than
  cosmetically: `isError` exempts a result from output-schema validation, so a
  rule keyed on `ok` alone would skip validation on exactly the payload most
  likely to drift.

- **`pdf` `operation: "info"` now returns a bounded sample instead of the whole
  text layer.** It used to return everything: a 19-page spec measured 36,660
  characters — roughly 9,200 tokens from a single call, and doubled on the wire
  because the payload rides in both the text block and `structuredContent`.
  The description called it *"the cheapest way to learn whether a PDF has a text
  layer"*; it was the most expensive way. Defaults are now 4,000 characters
  (≈2 pages) from the first 20 pages, both overridable per call with `max_chars`
  and `max_pages`.

  The result says when it clipped — `text_truncated`, `text_chars`,
  `pages_read` — and carries a `hint` naming the two narrower calls: a page
  range for part of the document, or `convert` for all of it at zero tokens.
  Silent truncation would be worse than the original problem: a model cannot
  tell a short document from a clipped one, and would answer about the part it
  received. Measured on a 40-page document: 43,629 characters before, 4,000
  after.

  `pages` already accepted `"1-5"` / `"1-3,7,10-12"` and was wired end to end —
  it was simply never mentioned for `info`, so nothing the model reads suggested
  a 26× cheaper call existed.

- **The five MCP tools now declare their behaviour to the host.** Every tool
  carries `annotations` and a human-readable `title`: `capabilities`
  and `quota` are marked read-only, `understand` and
  `quota` as reaching the network. A client could not previously
  auto-approve the read-only tools, because nothing said which ones they were.
- **`pdf`'s `operation` is now a real schema `enum`**, so the eight
  valid values are visible before a call instead of only in the error hint.
  Every tool parameter also carries a description. `convert`'s `to`
  stays deliberately open: its value set is what the local machine can write.
- **`convert` now documents that it returns output PATHS, not the
  converted text.** That is the basis of the batch being free, and it was
  stated nowhere the model reads; the skill's own wording implied the opposite.

### Fixed

- **The spend-approval prompt showed a price that was not the price.** Before
  uploading your files and charging you, `understand` asked for approval with a
  figure — and the figure was **"about $1.00" on every call, for every file, for
  every account.** It came from pricing an *empty* chat-Builder tool palette:
  `(0 tools × 20 iterations) + (50,000 µU × 20)`. It never varied with file
  count, size, page count, schema, tier or balance, and it described an
  operation you were not running.

  Three things were wrong at once: the wrong operation (that estimator "knows
  nothing about which workflow you intend to run"), the wrong unit (insured
  pre-margin µU, which understates the charge — on one measured run the quote
  was 120.1 credits against 403–419 actually charged), and no correction
  afterwards, since the charge is not yet reported back on a finished run.

  **The prompt now states that the amount is unknown rather than guessing it**,
  and says why. It is not silently omitted: an approval screen with no cost line
  reads as "free". The blocking network round-trip that fetched the constant is
  gone with it. `quota`'s description no longer suggests using it to price a
  run — it prices a tool palette you pass explicitly, and with no arguments it
  returns a constant.

- **A corrupt PDF told you to install a package you already have.**
  `PdfOperationError` subclasses `LocalError`, so a single `except LocalError`
  attached `uv add "convilyn[pdf]"` to *every* PDF failure. With pypdf
  installed, a damaged file returned `Stream has ended unexpectedly` alongside
  advice to install pypdf. Only a genuinely missing dependency carries that hint
  now.

- **`convilyn agent install` registered an MCP server the host could not
  start.** Both destinations wrote the bare name `convilyn` as the command, and
  an MCP host spawns its servers with an environment whose `PATH` need not
  contain the directory the package was installed into. The install reported
  success and the five tools never appeared — a CLI that works in your terminal
  and nothing at all in your assistant. Measured on Windows 11 with a
  `uv tool install`: bare name `failed`, the same config with an explicit
  `env.PATH` `connected`, an absolute path `connected`. Both destinations now
  carry the absolute path of the `convilyn` belonging to the interpreter that
  ran the install.

  Two things this does **not** do. It is not established to be Windows-only —
  the mechanism (a subprocess `PATH` missing the install directory) is
  platform-neutral, and POSIX is simply unmeasured. And it does not repair an
  existing `~/.codex/config.toml`: that file already declares `convilyn`, so
  the installer reports `unchanged` and leaves the old bare-name table in
  place. Fix that one by hand, or delete the `[mcp_servers.convilyn]` section
  and re-run.

## [3.6.1] - 2026-09-02

### Security

- Raised the `pdf` extra's `pypdf` floor from `>=6.10.2` to `>=6.16.1`, closing
  three GHSAs disclosed against pypdf on 2026-09-01: an infinite loop in
  `TreeObject.insert_child` on a crafted PDF (GHSA-jp53-mhqp-8xcg), and two
  long-runtime/high-memory amplification issues in outline and XForm-object
  handling (GHSA-23w6-3w8w-8484, GHSA-763m-79hh-57f2). No code in this package
  changed — resolving `pypdf>=6.16.1` (or newer) is the whole fix.

## [3.6.0] - 2026-08-31

### Added

- **`convert.download_to(job, to_dir=...)`** lands a result in a directory under
  the name the platform gave it, instead of a name you have to invent. Pass
  either `to` (a filename) or `to_dir` (a directory) — one of the two.

### Changed

- **A conversion that produced a package is no longer written under a name that
  denies being one.** Converting a document containing images to `md` returns a
  ZIP — the Markdown plus its `assets/` — and

  ```python
  client.convert.download_to(job, to="report.md")
  ```

  used to write those ZIP bytes into that name without a word. The result was a
  file whose name guarantees Markdown and whose first two bytes are `PK`; any
  tool that opened it as Markdown got binary. The response said
  `mimetype='application/zip'` the whole time and this method never read it.

  That call now raises, naming the package and pointing at `to_dir`. If you
  meant to keep the archive, name it `.zip`.

  **Only an archive is refused.** Writing PDF bytes to `report.output` still
  works: the bytes are what the name promises and only the extension is
  unconventional, which is your business. An archive is different in kind —
  the file is a container holding the thing its name claims to be.

### Fixed

- **`goals.understand()` described itself as taking several files, and the
  platform takes one.** Its summary line read "understanding of file(s)", its
  parameter is a list, and nothing anywhere mentioned a limit — so the shape a
  caller could reasonably infer was one the platform rejects. It now says one
  file, and says what to do instead: send each file as its own request.

  The limit is the platform's, and it is being lifted. The count is deliberately
  **not** checked in the client, and this is worth saying because the opposite
  looks like the helpful choice: a copy of a server-side admission rule goes
  stale the day the server moves, and it goes stale in the direction where this
  SDK refuses work the platform would have accepted. The refusal you get today
  is immediate, names the limit, and costs no credits — which is a better answer
  than a duplicate of the rule that will outlive it.

- **An uploaded `File` was accepted by `convert` and rejected by the goal
  lane.** `files.upload()` hands you a `File`, and `convert.create(file=...)`
  takes that object — so passing it to `goals.understand(files=[...])` is the
  obvious next step. It produced

  ```
  TypeError: Object of type File is not JSON serializable
  ```

  raised from inside the HTTP library while encoding the request, naming neither
  the method, nor the parameter, nor what to pass instead.

  Every `files=` parameter on `client.goals` now takes uploaded `File` objects,
  their `file_id` strings, or a mix. That is `start()`, `extract()`,
  `understand()`, `run_interactive()` and `to_markdown()` — the report named
  `understand()`, but all of them built the request the same way.

  Anything else is refused **before the request is sent**, with a message that
  names the parameter and the fix rather than one from the JSON encoder two
  layers down.

  The parameters are typed `Sequence[str | File]`, not `list[str | File]`. A
  `list` is invariant, so widening it that way would have **rejected** every
  existing caller passing a plain `list[str]` — silently for anyone using a type
  checker, and invisibly for everyone else.

- **The `convilyn_quota` tool did not tell your assistant that a quote is not a
  balance.** 3.5.0's release notes drew the distinction — "it is not a balance
  check; the balance is `client.account.get_balance()`" — but the tool
  description, which is the only text the assistant actually reads, contained
  neither the word "balance" nor the call that answers it.

  So an assistant was told to use the number to answer an approval, and told
  nothing about the question it cannot answer. It now says three things: that
  this is a price and not what the account has left; that the balance is
  `client.account.get_balance()`; and that the figure is pre-margin cost in
  micro-USD, so converting it to credits **understates** what you are charged —
  which fails in the direction that tells someone they can afford a run they
  cannot.

- **`CostEstimate`'s docstring said the SDK "does not wrap it yet".** It pointed
  at `POST /credits/workflow-quote` for affordability and described the absence
  of a wrapper as a scheduling detail. It is a decision: that route is not on
  this SDK's published surface, and the public contract argues against the shape
  it was being used in. Now stated as a decision, with the direction that is
  actually going somewhere — the charge arriving on the job you ran.

- **An interrupted `convilyn setup` could leave you with no key at all — and
  destroy the one you already had.** The credentials file was truncated in
  place and then written, so a Ctrl-C, a full disk, or a sign-in that failed
  after that point left `credentials.json` empty. The key that had been in it
  was already gone.

  That is a worse position than it sounds, because the two sides then disagree:
  your machine reads the empty file and reports no credential, while the server
  still holds an active key created under this machine's name — and it will not
  create a second one under the same name. Nothing in the terminal could settle
  it.

  The file is now written beside itself and renamed into place, which is atomic
  on every platform this package supports. An interrupted write leaves the
  previous key exactly as it was, and leaves nothing behind.

- **`convilyn agent install --dry-run` still said it had changed one of your
  files.** 3.5.0 fixed this for two of the three destinations. The third —
  `~/.codex/config.toml` — reported

  ```
  appended: C:\Users\you\.codex\config.toml
  ```

  in the past tense while writing nothing. It is now `would append:`.

  The reason it was missed is worth stating, because it says which machines saw
  it: `appended` is the word this command uses when the Codex config **already
  exists**, so the case that read wrong was every Codex user, and the case that
  read right was a machine that had never run Codex. Every test written for the
  3.5.0 fix started from an empty home directory, so none of them could reach it.

  The verb list is no longer maintained by hand next to the code that prints it.
  It is derived from the set of actions the installer can return, and a test
  fails if the two ever disagree — so a new destination cannot ship a word the
  dry run does not know how to say.

- **`convilyn setup --no-browser` announced that it was opening your browser.**
  The flag worked — no browser was launched — but the two lines printed above
  the URL both promised a launch:

  ```
  Opening your browser to sign in with google...
  If it doesn't open automatically, visit this URL:
  ```

  On the headless and SSH sessions the flag exists for, that means waiting for a
  window that was never coming. It now prints one line that matches what it
  will do: `Open this URL to sign in with google (this machine will not launch a
  browser):`.

  The sentence used to be written by the caller while the decision to launch was
  made somewhere else, which is how the two came apart. Both are now decided in
  the same place, so the same mistake cannot be made again by the next command
  that opens a link.

  Those lines are also ASCII now. They were the one place a Windows console
  using cp950 or cp932 — which is most of them in Taiwan, Hong Kong and Japan —
  rendered an ellipsis as mojibake, in the single line telling a headless user
  where to sign in.

- **`convilyn setup` could only succeed once per machine.** The API key it
  creates was named after your hostname, with no way to change it, and the
  console refuses a second active key with the same name. So a first run that
  half-completed — you closed the browser, or the sign-in worked but the save
  was interrupted — left every later attempt failing with

  ```
  Login failed: HTTP 409 key_mint_failed: An active key with this name already exists.
  ```

  and nothing you could do from the terminal to get past it. `--force` did not
  help: it only skips reusing a key already saved on your machine, then asks for
  the same name again.

  Three things change. `convilyn setup` now **retries once under a distinct
  name** when the first is taken, so an interrupted run repairs itself. A new
  **`--key-name`** option lets you choose the name outright — useful on a shared
  machine, or when you would rather pick than accept a generated one. And if both
  names are taken, the error now says so and names the flag, instead of repeating
  advice you had no way to follow.

  A name you supply is checked before the browser opens, so a typo costs a
  message rather than a full sign-in ending in a rejection.

- **`convilyn doctor` reported a missing API key as a failure.** It exited
  non-zero and printed `1 failed`, which reads as a broken install — but offline
  conversion (`convilyn local …`) needs no account at all, so an install without
  a key is limited, not broken. It is now a warning, and the command exits 0.

- **`convilyn doctor` could say your credentials file was fine and your key was
  missing, in the same run.** That check only ever looked at file permissions,
  never at the contents, so a file holding no usable key still reported `OK`. It
  is now named `Credentials file perms`, and a file that exists but yields no key
  is reported as exactly that — with the path — rather than as "not set".

## [3.5.0] - 2026-08-30

### Added

- **Your AI coding assistant can now use convilyn directly.** One command sets
  it up:

  ```bash
  uv tool install "convilyn[all,mcp]"   # or: pip install --user "convilyn[all,mcp]"
  convilyn agent install
  ```

  After that, an assistant working in your project can convert a `.docx`,
  `.pptx` or `.pdf` to Markdown by itself, without you copying text around. The
  conversion still happens on your machine and still costs nothing.

  Claude Code and Codex look in different places, so both are written: Claude
  Code gets a plugin at `~/.claude/skills/convilyn/` that loads on the next
  session with no marketplace and no install step; Codex gets
  `~/.agents/skills/convilyn/SKILL.md` and an `[mcp_servers.convilyn]` table
  merged into `~/.codex/config.toml`.

  **Install it where your editor can find it.** The MCP server is started by the
  editor rather than by your shell, so it has to reach `convilyn` on `PATH`, and
  a project virtualenv is not on the editor's `PATH`.

  `convilyn agent install` is safe to re-run, supports `--dry-run`, and merges
  into your existing config rather than replacing it. If your config is in a
  shape it cannot safely edit, it says so and changes nothing.

- **`convilyn mcp serve`** — speak the Model Context Protocol on stdin/stdout,
  which is how coding assistants talk to outside tools. It offers five:
  `convilyn_convert`, `convilyn_capabilities` and `convilyn_pdf` (local and
  free), `convilyn_quota` (prices a hosted run and reports your tier — it is not
  a balance check; the balance is `client.account.get_balance()`), and
  `convilyn_understand`
  (structured extraction — this one spends credits, and its description says so
  where the assistant will read it).

  Only those last two need an account. The three local tools work with no
  `convilyn setup` at all.

  Needs the new `mcp` extra: `pip install "convilyn[mcp]"`. It is not part of
  `[all]`, because `[all]` is about file formats and this is not.

- **A plugin for editors that install plugins from a marketplace.** Point yours
  at `CoreNovus/convilyn-python` and it gets both the guidance and the five
  tools. This is the route for handing convilyn to a team; on your own machine
  `convilyn agent install` already sets Claude Code up directly.

- **Guidance that says when *not* to use convilyn.** The skill file tells your
  assistant to read `.md`, `.txt`, `.csv` and source files directly, because
  that is genuinely faster and costs the same nothing. It is there so the tool
  gets reached for when it actually helps.

### Security

- **No API key is written into any assistant config file.** `convilyn setup`
  already stores your key where the CLI finds it, so the MCP setup carries no
  credentials at all — nothing to leak when a config file gets copied to another
  machine or pasted into a bug report.

- **`convilyn api` no longer accepts an absolute URL, and that closes a way your
  API key could leave your machine.** Every request this client makes carries
  your key in an `Authorization` header, and the underlying HTTP library ignores
  the configured API host the moment it is handed a full `https://…` address —
  so `convilyn api GET https://somewhere-else/…` sent your key to
  `somewhere-else`. It now refuses before anything is sent, and tells you to
  pass a path like `/api/v1/jobs` instead.

  This matters most where the path is not typed by you: the command is
  documented for AI assistants to use, and the skill this package ships grants
  an assistant permission to run any `convilyn` command — so the argument could
  come from a document the assistant was asked to read. Reaching an external
  address is still supported where it always was, on the download/upload paths,
  which do not attach your key and already validate the address.

- **`convilyn_understand` only reads files from the folder your editor opened,
  and never a credential file.** It is the one tool here that sends your files
  to us, and it used to accept any path that existed — so an assistant that had
  just read a document telling it to "check `~/.ssh/id_rsa`" could have uploaded
  it. It now resolves each path first (so a shortcut cannot point outside the
  folder) and refuses anything credential-shaped — `.env`, `*.pem`, private
  keys, `.npmrc`, and this tool's own `credentials.json` — even inside your
  project.

- **Nothing is uploaded or charged until you say yes.** The tool used to ask
  your assistant, in writing, to check with you first. That is a request to the
  same assistant it is trying to restrain. Your editor now shows you a real
  prompt naming the files and the price before anything leaves your machine, and
  an assistant cannot answer it for you. If your editor cannot show such a
  prompt, the tool declines rather than proceeding — nothing is sent, and
  nothing is billed.

- **`convilyn_pdf` answers instead of crashing when an argument is missing.** It
  raised a `KeyError` that reached your assistant as a stack trace; it now
  returns which argument it needs, which is something the assistant can act on.

- **`convilyn agent install --dry-run` no longer says it changed your machine.**
  It reported `created: …` for each destination while writing nothing, so the
  one command whose entire job is to show you what *would* happen described it
  in the past tense. It now says `would create:`.

- **A billing link from the server is checked before your browser is opened.**
  When a job stops for want of credits, the response carries a top-up link, and
  the CLI offered to open it. On Windows that path will open a local file or run
  a program just as readily as a web page, so the link is now required to be a
  real `https://` address first. It is still always printed, so a legitimate
  link is never lost.

## [3.4.1] - 2026-08-29

### Changed

- **The browser page you land on after signing in now explains what was set
  up**: that a key is being created for this machine, that your sign-in session
  is used once and then discarded, and that the key never passes through the
  browser. It used to say only "you can close this window".

### Fixed

- **The browser no longer shows "Signed in" when the sign-in was rejected.** If
  the callback failed a security check, the page said it had succeeded while the
  terminal said it had failed. It now says what went wrong and that nothing was
  saved.

- **`convilyn doctor` now checks the credentials file's permissions on Windows
  too.** It used to skip the check entirely there, so it reported nothing either
  way.

  On Windows the file is protected by the permissions it inherits from
  `%APPDATA%`, which by default let only you, Administrators and the system read
  it — the same protection as on macOS and Linux. That default is fine, and
  `doctor` now confirms it rather than staying silent:

  ```
  ✓ [OK] Credentials file: ACL grants no broad principal (C:\...\credentials.json)
  ```

  If the file ends up somewhere more permissive — a redirected `%APPDATA%`, a
  network share, a restored backup — `doctor` says so and names who can read it,
  instead of skipping.

## [3.4.0] - 2026-08-29

### Added

- **Sign in with your Convilyn email and password**, not only Google or GitHub.

  ```
  convilyn setup --provider email
  ```

  It asks for your email and password in the terminal (the password is not
  shown as you type) and never opens a browser. Google and GitHub work exactly
  as before.

- **A welcome message after a successful sign-in**, with links to the pages
  worth reading first: choosing a lane, converting offline, credits and
  pricing, and managing your API keys.

### Changed

- **`convilyn setup` no longer makes you sign in again if you already have a
  working key.** It checks the saved key first and stops there if it works.

  It checks by *using* the key, not by looking for the file — a key you revoked
  from the console leaves the file exactly as it was, and in that case you do
  need to sign in again. If the key no longer works, it says so and continues
  to the normal login.

  Use `convilyn setup --force` to sign in again anyway — for a shared machine,
  a rotated key, or a different account.

## [3.3.0] - 2026-08-29

First stable release of the 3.3 line. It contains everything from `3.3.0b1`
plus the two entries below.

### Added

- **`convilyn setup` — sign in from your browser.** Run it and it opens your
  browser, you sign in as usual, and an API key is created and saved for you.
  Nothing to copy and paste.

  ```
  convilyn setup
  ```

  Add `--no-browser` to print the URL instead of opening one, which is what you
  want over SSH.

  Only the API key is written to disk. The login tokens are used once to create
  that key and then discarded — they are never saved and never logged.

- **A test results page**:
  [`docs/MEASURED-2026-08-28.md`](docs/MEASURED-2026-08-28.md), linked from both
  the README and the PyPI page. It reports how well conversion and extraction
  actually score, on named corpora, together with the known limitations. Every
  figure comes from [`doc-eval`](https://github.com/CoreNovus/doc-eval), a
  separate evaluator you can run yourself.

  The filename carries the date it was measured, so a later report cannot be
  confused with this one.

## [3.3.0b1] - 2026-08-25

### Fixed

- **`convilyn.local`: a running header or footer no longer arrives as body
  text.** A page number, a document reference or a `CONFIDENTIAL` marker is
  printed on the page without being part of what the page says, and every one
  of them was being converted into the Markdown alongside the real content.

  A PDF states none of this, so it is inferred, and all three conditions narrow
  what is removed: the region has to lie in a band at the very top or bottom of
  the page, be the first or last in reading order, and be set smaller than the
  median of what the rest of that page is set in. Where the answer is unclear
  the text is kept — a repeated page number costs a line, a deleted paragraph
  cannot be recovered. Measured on a 23-page facing-page textbook: 16 page
  numbers removed, no body text touched — **on the single-column pages only.
  On a facing-page (2-up) spread, only the left page's header and the right
  page's footer are ever reachable** (the reading-order edge condition is
  evaluated over the whole flattened region list, so the right page's header
  and the left page's footer sit at interior indices and cannot be first or
  last); found in code review after this line was first written, and recorded
  as a known gap rather than silently left overclaiming.

  Only the text goes, and only in a PDF. A logo drawn in the header band is a
  picture the document contains and PDF still extracts it; the HTML path
  strips the whole element it was found in, so the same logo inside an HTML
  `<header>` is lost — a known asymmetry, not fixed here.

- **`convilyn.local`: a PDF's own title no longer outranks its sections.**
  Every PDF's title fell to the same outline level as its top-level section
  headings — `#` for the title, `##` for "1. Introduction", but the title's
  ACTUAL heading level was also `##`, so the outline read as three siblings
  rather than a title with sections nested under it. A document with 4
  headings and 3 outline levels came back with 2.

  The size-ratio tier that decides a line's heading level had no tier for a
  document title at all, despite carrying that intent in its own comment —
  every line fell into whichever LOWER tier its font size cleared. Added the
  missing tier at 1.9x body text (not the more obvious 1.8x: that ratio is
  one of the most common real heading sizes and would have split ordinary
  section headings apart instead of catching only titles).

- **`convilyn.local`: a converted CSV no longer opens with a heading made up
  from the filename.** The extractor titled the document `path.stem`, so a file
  named `export.csv` gained an `# export` heading the CSV never contained — and
  through the hosted lane, where the path is a staging temp file, every
  conversion began with a fabricated `# tmpXXXXXXXX`.

- **`convilyn.local`: a single-sheet XLSX no longer opens with its own sheet
  name as a heading.** A workbook nobody bothered to rename its one sheet in
  — the common case, since Excel's own UI default is "Sheet1" — converted
  with `## Sheet1` as the document's first line, reading as if the workbook
  itself were titled that. A heading exists to navigate BETWEEN sections; a
  workbook with only one sheet has nothing to navigate between, so its name
  is dropped regardless of what it is (not just recognisable defaults —
  sheet COUNT is the signal, not the name). A workbook with 2+ sheets is
  unaffected: each sheet still gets its own heading, same as before.

### Changed

- **Default `base_url` is now `https://api.convilyn.com`** (was
  `https://api.convilyn.corenovus.com`). The old host keeps serving
  indefinitely — it is an additive CloudFront alias, not a retirement — so
  this only changes what a client resolves to when neither a constructor
  argument nor `CONVILYN_BASE_URL` is set. Set `CONVILYN_BASE_URL` (or pass
  `base_url=` explicitly) to keep using the old host.

## [3.2.0b3] - 2026-08-23

### Fixed

- **`convilyn.local`: a PDF with more than one column no longer comes back with
  its lines welded together.** The extractor treated "same `top` coordinate
  means same line", which is the same thing as asserting every page has one
  column. On a facing-page (2-up) scan that premise fails on every line, and
  the three symptoms it produced were one missing layer, not three bugs.

  A layout pass now runs in front of every PDF: recursive XY-cut over the word
  bounding boxes, pure geometry, no new dependency and no model download. **A
  region with no qualifying gap comes back whole** — i.e. exactly today's
  behaviour — so a single-column document cannot be made worse by it.

  Measured on a 23-page 2-up textbook: welded two-column lines fell from
  541 / 1,630 to 103 / 2,386.

- **A one-row or one-column grid is no longer emitted as a table.** `pdfplumber`
  reads decorative rounded label boxes as tables — 26 of 44 on that same file —
  and the Markdown renderer opens with `header, *body`, so a one-row grid
  renders as a header with nothing under it. Worse, its cells were being cut
  out of the prose stream, so a single token could arrive split across two
  cells (`| 閩-E | -B1 |`) and a sentence could arrive interleaved with another.

  Rejected grids contribute no bounding box, so their words return to the prose
  where they belong. Tables on that file: 44 → 17, all of them at least 2x2.
  Character conservation is unchanged at 99.892% — filtering the false tables
  dropped no text.

- **Images land where the page drew them, instead of all trailing the page.**
  Coordinates come from `pdfplumber`'s `page.images` and the bytes still come
  from `pypdf`, joined on the PDF's own XObject resource name. On the reference
  file 68 of 68 image blocks now have content after them on the same page.

- **JPEG 2000 images are no longer delivered as files named `.png` that no
  viewer opens.** Every unknown suffix used to be reported as `image/png`; 34
  embedded `.jp2` files shipped that way. Unknown suffixes now report
  `application/octet-stream`, and non-renderable formats are re-encoded to real
  PNG. Assets whose name matches their format: 31 / 63 → 63 / 63, zero broken
  links.

### Changed

- **`account.usage_history()` documents the row shape the server actually
  sends.** It returns **at most 50 rows, newest first, with no cursor** — so
  receiving exactly 50 means older periods exist and you have not seen them.
  The rows are run COUNTS for quota metrics; the credits period is never in
  that set, so **no row here reports spend**. `account.get_balance()` is the
  credits question. Documentation only — no behaviour change.

### Packaging

- **The author email published on the PyPI page can now receive mail.** It
  pointed at `convilyn.corenovus.com`, a domain with no MX record, so the one
  channel a stranger installing this package can see swallowed their mail.
  Fixed metadata takes effect only on a release, which is one of the reasons
  this one exists. Support goes to `support@convilyn.com`.

## [3.2.0b2] - 2026-08-21

### Added

- **`account.get_balance()`** — `GET /api/v1/credits/balance`, the two-bucket
  credit balance (`period_credits` + `topup_credits`, totalled as
  `balance_credits`). Compare a quote from `account.get_quota()` against
  `balance_credits`.

  `account.usage_history()` does **not** answer this and never did: it returns
  run COUNTS for quota metrics, and the credits period is not in its tracked
  set, so it carries no balance row at all. Before this, the only balance a
  `ck_` key could reach was a side effect of quoting a workflow you did not
  intend to run.

### Changed

- **`GoalJobFailedError.retryable` is now `bool | None`.** It was a bare
  `bool`, so "the server said do not retry" and "the server said nothing" were
  the same answer — `False`. That is the reading this package's own
  `InsufficientCreditsError` docstring already forbids for its operands
  (*"read them as unknown, never as zero"*), and it applies to a verdict too.

  Not academic: until the backend began sending `suggestedAction` on a failed
  job, `suggested_action` was `None` on **every** failure, so `retryable` was
  structurally `False` for every job this SDK has ever seen fail. A caller
  branching on it would never once have retried.

  `if exc.retryable:` keeps working unchanged. Add `elif exc.retryable is
  None:` if you want to distinguish "no guidance" from "do not retry".

## [3.2.0b1] - 2026-08-20

### Installing this pre-release

Pin the version. Do **not** reach for `--pre`:

```bash
pip install "convilyn[all]==3.2.0b1"
```

pip already allows a pre-release when the specifier names one explicitly, so
the pin is sufficient on its own. `--pre` is a **global** switch — it applies
to the whole dependency resolution, not just to `convilyn` — and pip's own
hint (`install with `pip install --pre``) does not say so. Following that hint
during round-6 testing produced:

| package | pinned install | after `--pre` |
|---|---|---|
| `defusedxml` | 0.7.1 | **0.8.0rc2** |
| `lxml` | 6.1.1 | **7.0.0a3** |
| `pydantic` | 2.13.4 | **2.14.0b1** |

Every model in this SDK is built on pydantic, and `lxml` is what
`python-docx` / `python-pptx` parse with — so a result from that environment
cannot be attributed to `convilyn` at all. That is pip's behaviour rather than
a defect here, but anyone installing a pre-release will meet it, so it belongs
next to the version number rather than in a support thread.

Pre-release. Minor rather than patch because of one **behaviour change you can
see from the outside**: five failure paths that used to raise `ValueError` now
raise a `ConvilynError`. Nothing is removed from the public API, but `except
ValueError:` wrapped around `extract()` / `understand()` / `to_markdown()` stops
catching them. See **Changed** — it is written in both directions on purpose.

### Added

- **`GoalArtifactUnusableError`** — the run succeeded, was charged, and there is
  still nothing usable to hand back. That is not the same event as a failed job
  and it is not your mistake, so it is now its own type under `ConvilynError`
  rather than a bare `ValueError`.

  It carries the operands you would otherwise have to parse out of the message:

  | attribute | |
  |---|---|
  | `reason` | `"missing"` \| `"unparsable"` \| `"too_large"` |
  | `kind` | `"json"` \| `"markdown"` — which artifact was being fetched |
  | `job_spec_id`, `artifact_id` | feed both straight to `goals.download_artifact_to(...)` |
  | `size_bytes`, `max_bytes` | set on `"too_large"` |
  | `job_status` | the terminal status the run actually reached |

  The `too_large` case is the one worth knowing about: the message has always
  told you to use `download_artifact_to()`, but `extract()` / `understand()` /
  `to_markdown()` never return a job handle, so there was no supported way to
  obtain the two ids that call needs. They are on the exception now.

  ```python
  except convilyn.GoalArtifactUnusableError as exc:
      if exc.reason == "too_large":
          client.goals.download_artifact_to(exc.job_spec_id, exc.artifact_id, to="out.json")
  ```

- **`GoalJobFailedError.detail` / `.suggested_action` / `.retryable`.**
  `PROCESSING_LIMIT` is one canned sentence covering **four unrelated ceilings**
  — an iteration cap, an input-token budget, a repeated tool call, a scratchpad
  read loop — so until now a caller could not tell them apart, nor whether
  changing the input would help.

  ```python
  except convilyn.GoalJobFailedError as exc:
      if exc.detail and exc.detail.reason == "ITERATION_LIMIT":
          print(f"stopped at {exc.detail.reached} of {exc.detail.limit} steps")
      if exc.retryable:
          job = client.goals.retry(exc.job_spec_id)   # same job spec, not charged again
  ```

  `detail.reason` is one of `ITERATION_LIMIT`, `TOKEN_BUDGET`,
  `REPEATED_TOOL_CALL`, `SCRATCHPAD_READ_BUDGET`; `limit` / `reached` are `None`
  — never `0` — when a resumed run has no counter. `suggested_action` is the
  server's own next step for this `code`, so you do not keep a second copy of
  that mapping, and `retryable` is simply `suggested_action == "retry"`. Read it
  rather than inferring: a plan ceiling is **not** retryable but **is**
  actionable, which is why the API sends an action rather than a boolean.

  Both fields require a backend that serves them; against an older deployment
  they are `None`, which is why this is a pre-release.

### Changed

- **`except ConvilynError:` now catches five situations it did not.** All five
  are post-success artifact problems in `extract()`, `understand()` and
  `to_markdown()`: no JSON artifact, no Markdown artifact, the payload is not
  valid JSON, and the two in-memory size caps. If the exception table in
  QUICKSTART §4 is what you built your handling on, this is the direction that
  makes it more true, and no change is required.

- **`except ValueError:` around those three methods stops catching them.** This
  is the migration, and it is one line:

  ```python
  - except ValueError as exc:          # used to catch an unusable artifact
  + except convilyn.GoalArtifactUnusableError as exc:
  ```

  **What is still a builtin:** argument mistakes — but they are **`ValueError`
  *or* `TypeError`**, not one type, and this said `ValueError` alone:

  | call | raises |
  |---|---|
  | `understand([], schema={})` — empty file list | `ValueError` |
  | `understand(["file_x"], schema="not-a-dict")` — schema is not a dict | `TypeError` |

  `goals.py`'s own docstring has always said `ValueError / TypeError` and the
  CLI handler has always caught both; only this entry merged two different
  inputs into one sentence. A reader who wrote `except ValueError:` from it
  would miss every schema *type* error.

  The line being drawn is *"you passed something unreasonable" stays a builtin;
  "the platform produced something unusable" becomes a `ConvilynError`*.

- **`convilyn goals understand` exits `3` instead of `1`** when the run produced
  no usable result. `1` means you invoked the command wrongly; this outcome is
  a run that happened and was paid for, which is what `3` already meant for a
  failed job.

  This said "the command's `--help` documents `3` as covering both", and it did
  not: the help text named exactly one exit code, `1`, for a malformed
  `--schema-file`. The implementation was right the whole time — only the help
  was silent. `--help` now carries the full table, so the claim and the output
  agree.

### Fixed

- **A non-UTF-8 artifact escaped as a bare `UnicodeDecodeError`.** `json.loads`
  on bytes raises `UnicodeDecodeError`, not `JSONDecodeError`, so the guard
  never fired: `except ConvilynError:` missed it and the promised message never
  appeared. `to_markdown()`'s `.decode("utf-8")` had the same hole. Both are now
  reported as `GoalArtifactUnusableError(reason="unparsable")`.

- **The oversize message named `extract()` even when you called
  `understand()`.** The cap is stated by the exception's `size_bytes` /
  `max_bytes` now, so it cannot name the wrong method.

- **`doc_analyzer` is not a workflow that exists.** It appeared in QUICKSTART,
  in an example file, in `--workflow-id` help text and in a docstring — so the
  first snippet a new user copies returned a 4xx. Every occurrence now names
  `goal_lane.content_to_multipost`, an active workflow with a single required
  slot, which is also what makes the human-in-the-loop walkthrough actually
  reach its loop.

- **`to_markdown()`'s documentation said no platform build served it.** That
  stopped being true and the docstring did not. It now describes what the
  method really raises when a given output kind has no pipeline.

## [3.1.0] - 2026-08-17

Minor, and both halves are why: the public surface **grows** by four exception
types, and the set of packages installed into your environment **shrinks** by
one. Nothing is removed from the API and nothing you catch today stops being
caught, so no migration is required.

### Added

- **Four typed billing refusals.** The paid path can refuse a run in four ways
  that want four different next steps from you, and until now all four arrived
  as a bare `APIError` — so telling "top up" from "wait" from "this workflow
  has no price" meant string-matching `exc.code`, which is matching on
  something we reserve the right to change.

  | | status | what to do |
  |---|---|---|
  | `InsufficientCreditsError` | 402 | top up — carries `required_credits`, `available_credits`, `shortfall_credits` |
  | `FreeTierBlockedError` | 403 | leave the Free plan (or fund the run) — carries `upgrade_url` |
  | `SpecNotPricedError` | 409 | pick another workflow; retrying will not help |
  | `ChargeUnavailableError` | 409 | transient — retry later |

  **`InsufficientCreditsError` is not `QuotaExceededError`, and they share HTTP
  402.** A quota is a ceiling you were given and it resets at the next period; a
  balance is money you hold and it does not refill on its own. One status code,
  two different facts about your account — so they are two types rather than one
  type you branch on by `code`:

  ```python
  except InsufficientCreditsError as exc:
      print(f"short by {exc.shortfall_credits} credits")   # None when unknown
  except QuotaExceededError:
      ...                                                  # wait, or upgrade
  ```

  `shortfall_credits` is derived from the two operands rather than sent as a
  third field, because a third field that must agree with two others is a field
  that can disagree with them. It is `None` — *unknown*, never zero — when the
  refusal carried no operands, and clamped at zero if they ever disagree.

  All four subclass `APIError`, so every existing `except APIError:` and
  `except ConvilynError:` keeps catching them. **A refusal code this build does
  not model still arrives as a plain `APIError`** with `code` and `details`
  intact — on 402, 403 and 409 alike — so a new server signal is never an
  unhandled crash and never a type asserting a remediation nobody verified.

### Removed

- **`websockets` is no longer a dependency.** It had been *required* since
  before 3.0.0 and imported nowhere in the package since — the WebSocket
  surface was removed in 3.0.0 (`goals.events()`, `GoalEvent`, `WebSocketError`,
  `ws_url`) and the dependency did not follow, so every `pip install convilyn`
  pulled a package no code could reach.

  Nothing in the public API changes; there was nothing left importing it. What
  changes is your installed environment — one fewer transitive package, one
  fewer version-compatibility surface, one fewer CVE feed to read. That is why
  this is a minor rather than a patch.

## [3.0.1] - 2026-08-17

A fix-only release: nothing added, nothing removed from the public API. That is
why it is a patch and not a minor. `goals.extract()` and `estimated_micro_u` are
both still here and still work; their removal is bound to 4.0.0.

### Fixed

- **`goals.understand()` refusals now carry the server's reason instead of a
  blanket "not supported".** A request the backend rejected for a nameable,
  actionable cause — too many files, mixed file kinds — surfaced as
  `UnderstandUnavailableError` with the class's default text: *the connected
  platform does not support schema-grounded understanding yet*. That is a claim
  about the platform, and it was false; the caller's request was the problem,
  and the backend had said so.

  Two independent losses, both fixed:

  - the error envelope decoder recognised `{code, message, …}` and
    `{"detail": {…}}` but not `{"detail": "<string>"}` — a plain-string detail,
    which several AI-workflow create paths return. That body fell through, so
    `message` degraded to the HTTP reason phrase and the explanation was
    discarded before any resource saw it.
  - `understand()` then constructed the error with no argument, discarding even
    that.

  A refusal whose body carries **no** message is unchanged: it still reads as
  the platform not supporting the feature, which is the accurate reading when
  the server offered nothing. The HTTP reason phrase is a status label, not an
  explanation, and is not forwarded as one.

  **`UnderstandUnavailableError` is still the type raised**, for the same four
  statuses as before — only the message improves, so `except
  UnderstandUnavailableError` written against 3.0.0 keeps working. Giving the
  wrong-request case a *distinct type* needs a machine-readable discriminator on
  the wire, which does not exist today; that is tracked for the next major.

- **A shipped CLI example crashed.** `examples/07_goals_cli.sh` was published in
  the sdist and did not run; it is removed. The examples index and the scripts
  it lists are now checked against each other in both directions, so the index
  cannot name a file that is absent and a file cannot ship unlisted.

- **The docs described a WebSocket event stream that 3.0.0 removed.**
  `docs/QUICKSTART.md` claimed the goals surface "adds … a WebSocket event
  stream" while §7.3 of the same document recorded its removal, and the
  `convilyn goals` CLI docstring still listed an `events` subcommand. Both now
  match what the package does.

- **The upload path's SSRF guard is now actually covered by a test.** Uploads go
  through a presigned **POST** grant. The SDK also carried a presigned-PUT
  fallback "so the SDK works against both backend generations" — there is no
  such generation; the contract makes `fields` required and the server has one
  producer. That dead path is gone.

  What matters more than the removal: the two SSRF assertions (reject non-HTTPS,
  reject internal hosts) were attached to the **dead** method, while the live one
  had none. They were moved onto the live path rather than deleted with the code,
  so the guard your uploads actually pass through is the one under test. No
  behaviour change — the path you were already using is unchanged.

- **The offline engine's format table now cannot advertise a format the package
  does not contain.** No conversion changes here — `convilyn.local` reads the
  same formats it read in 3.0.0, and the shipped engine is byte-for-byte what
  3.0.0 shipped. What changed is that this is now *enforced* rather than true by
  luck.

  The engine is generated from the platform's own conversion code. That upstream
  gained an HTML reader; the generator's precondition asked whether the import
  could be *rewritten* for the published package, not whether the module it
  named was one the package actually carries — and those read as the same
  question. A regenerated engine would have advertised `html → md` through
  `capabilities()`, then raised `ModuleNotFoundError` the first time anyone
  converted an HTML file.

  A postcondition over the whole generated tree now refuses any build whose own
  imports do not resolve, so a route this package offers is a route it can run.

  **HTML remains unavailable offline, and is now a stated limit rather than an
  omission.** The platform's HTML reader is built on a GPL-3.0 library, and this
  package is Apache-2.0 with permissive dependencies throughout; adding it is a
  licensing decision, not a packaging one. `convilyn.local.capabilities()` lists
  no `html` route, which is the honest answer — the hosted conversion API reads
  HTML as it always has.

## [3.0.0] - 2026-08-16

### Added

- **`ConvertJob.warnings` — what a *successful* conversion could not preserve.**
  The field has been on the wire since the warnings channel was built, and the
  LibreOffice route began filling it in the same wave as this release; the SDK
  simply did not model it, so every warning the server sent was dropped at the
  last hop.

  It matters most where the job **succeeds**. An `.xls` workbook converted to
  CSV returns a file, reports `completed`, and holds only its first sheet —
  `warnings` is the only thing that says so. `pdf_reverse` has likewise been
  emitting `Page N has minimal or no text` all along, seen by nobody.

  ```python
  job = await client.convert.create_and_wait(file=f, target_format="csv")
  for note in job.warnings:
      print(note)
  ```

  Entries are prefixed by kind (`best_effort:`, `truncated:`, `bundled:`,
  `layout_degraded:`, …), so splitting on the first `:` groups them — but treat
  an unprefixed entry as a plain note rather than an error, because some
  producers emit one. Always a list: absent on the wire means empty, never
  `None`, so `for note in job.warnings` needs no guard.

- **A refused conversion now tells you why, in fields you can act on.**
  `JobFailedError` gains `detail` (`convilyn.types.JobErrorDetail`), carrying
  `reason`, and — for a workbook refused because CSV holds one table —
  `sheet_count` plus `faithful_targets`.

  ```python
  except JobFailedError as exc:
      if exc.detail and exc.detail.reason == "MULTI_SHEET_WORKBOOK":
          print(f"{exc.detail.sheet_count} sheets; try "
                f"{' / '.join(exc.detail.faithful_targets or [])}")
  ```

  Before this, a six-sheet `.xlsx` → `csv` returned
  `[GENERIC]: Something went wrong during processing. Please try again.` — a
  retry instruction for a refusal that is deterministic, so every attempt spent
  quota to fail identically. The code is now `UNSUPPORTED_INPUT` and the retry
  advice is gone; `detail` is what lets you explain the refusal in your own
  words and your own locale.

  `str(exc)` also gains a trailing sentence when a detail is present. The
  existing `Job <id> (<type>) failed [<code>]: <message>` prefix is unchanged,
  so `startswith` matching keeps working.

  **`code` deliberately stays `UNSUPPORTED_INPUT`** rather than becoming a new
  member. A new code sends clients that predate it down their unknown-code
  path, which on the web client resolves to a generic "try again" — reinstating
  the exact advice this removed, for the users least able to act on it. Branch
  on `code` first and treat an unrecognised `detail.reason` as absent: the
  server may know refusals your build does not.

### Removed — BREAKING

- **The WebSocket event stream is gone.** Removed: `goals.events()`, the
  `convilyn goals events` CLI command, the `GoalEvent` type, the
  `WebSocketError` exception, and the `ws_url` / `ws_transport_factory`
  constructor arguments (plus `CONVILYN_WS_URL`).

  **Nothing that worked stops working — it never worked.** The platform's WS
  gateway authenticates developer-portal keys (`cvl_` / `cvi_`), a JWT, or an
  anonymous browser cookie. This SDK *rejects* developer-portal keys at
  construction and issues no JWT, so no credential it can hold was ever
  accepted. Every call raised. The tests passed because the transport was mocked.

  **Why it was removed rather than fixed.** Making it work means the gateway
  accepting a consumer key at `$connect`, and its authorizer takes identity from
  a **query parameter** — it must, because the browser client shares that gateway
  and a browser cannot set headers on a WebSocket handshake. So "gateway support"
  meant putting a long-lived, non-self-revocable API key in a URL, permanently,
  on every streaming call. Query strings reach proxy logs, debug tooling, and any
  access log later switched on.

  **Migration.** Use `client.goals.wait(job_spec_id, timeout=..., idle_timeout=...)`
  or `retrieve()`; CLI `convilyn goals status`. Both authenticate over HTTPS with
  an `Authorization` header. `wait()` already backed every documented example.

  If streaming returns it will use a short-lived, single-use connect ticket —
  a design sharing no code with what was removed, which is why keeping this was
  not free optionality.

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
  (0.2s) floor in both the goals and convert wait loops. `poll_interval=0`
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
  `client.goals` lifecycle boilerplate (start/wait/fetch/parse). Runs the platform's
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
  built-in workflow catalog (`GET /workflows/catalog`), returning
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
