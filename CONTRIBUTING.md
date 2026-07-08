# Contributing to convilyn-python

**Community contributions are welcome and land in the shipped package** — with your
authorship preserved. Here's how.

## How this repository works (read this first)

`CoreNovus/convilyn-python` is a **public mirror** of source maintained in Convilyn's
private monorepo, which is the single source of truth. That lets us change the
backend contract and the SDK **together, in one reviewed change**, so the client
and the server never drift apart. The mirror is a generated, read-mostly projection —
but it is **not** a dead end for contributors:

```
you fork convilyn-python  ──PR──▶  convilyn-python   ──upstreamed──▶  monorepo PR
                                                              │ full CI
                                                              ▼
      mirror refreshed  ◀── merged ──  contract + conformance + smoke + blackbox
```

**Your PR becomes a monorepo PR first — it is not merged here directly.** A
maintainer converts your accepted PR into a monorepo PR with **your authorship
preserved** (via `git format-patch`); it runs the full monorepo CI, and once merged
the mirror is refreshed from it. Your mirror PR is then closed
`Upstreamed in CoreNovus/convilyn#<N>`.

This keeps the SDK and the backend wire contract in lock-step (the platform's defence
against drift) while giving you a first-class contribution path.

## Before you start

- **Small doc / example / typo fixes** — open a PR here directly.
- **Behaviour / API changes** — please open an **issue first** so a maintainer can
  confirm the direction before you invest time (the change has to fit the
  published API contract).
- Look for [`good first issue`](../../labels/good%20first%20issue) if you're new.

## Developer setup

```bash
git clone https://github.com/CoreNovus/convilyn-python.git
cd convilyn-python
pip install -e ".[dev]" && pytest
```

Before pushing, run the same checks CI runs (see `.github/workflows/ci.yml`):

```bash
ruff check . && ruff format --check . && pytest
```

## Sign your commits (DCO)

We use the **Developer Certificate of Origin** — a one-line certification that you
wrote (or have the right to submit) the patch. **No CLA, no paperwork.** Just add
`-s` to every commit:

```bash
git commit -s -m "fix: correct the retry backoff ceiling"
```

That appends a `Signed-off-by: Your Name <you@example.com>` trailer. The
`DCO` check on your PR verifies every commit is signed off. If you forget:

```bash
git rebase --signoff HEAD~<number-of-commits>   # then force-push your branch
```

By signing off you agree to the DCO at <https://developercertificate.org/>.

## Pull request checklist

- [ ] Every commit is `Signed-off-by` (DCO).
- [ ] Tests added/updated for the change; the CI is green.
- [ ] Public API changes are discussed in a linked issue first.
- [ ] No secrets, internal hostnames, or internal identifiers added (the secret
      scan + vocabulary lint will flag these — see `.github/workflows/ci.yml`).

## Licensing of your contribution

This project is **Apache-2.0**. Under Apache-2.0 §5, contributions you submit are
provided under the same license (inbound = outbound). The DCO sign-off is your
certification of origin; there is no separate CLA.

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). Please be
kind — we want this to be a good place to contribute.

## Security issues

**Do not** open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md)
for the private disclosure channel.
