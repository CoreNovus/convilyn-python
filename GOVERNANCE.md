# Governance — `convilyn-python`

This repository is a **public mirror** of a package maintained in Convilyn's
monorepo (the single source of truth). Governance here is intentionally light —
it exists to make contribution predictable, not bureaucratic.

## The one non-negotiable: the monorepo is the source of truth

The shipped package is generated **from** the monorepo and released to PyPI. This
repo is a read-mostly projection: `Release vX.Y.Z` commits are pushed by
maintainers, never merged from contributor branches directly. Your contribution
becomes authoritative only after it is **upstreamed into the monorepo** and passes
the full upstream CI — then the mirror is refreshed from it, with **your authorship
and `Signed-off-by` preserved**. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
mechanics; this is why cross-language SDK contracts never drift.

## Roles

- **Contributor** — anyone who opens an issue or PR. No prior permission needed for
  docs/typo/example fixes; open an issue first for behaviour/API changes.
- **Maintainer** (`@CoreNovus/sdk-maintainers`) — reviews and merges, runs the
  upstreaming bridge, cuts releases, and triages. Listed in
  [MAINTAINERS.md](MAINTAINERS.md).
- **Security** (`@CoreNovus/security`) — handles private vulnerability reports (see
  [SECURITY.md](SECURITY.md)).

## Decisions

- **Routine** (bug fixes, docs, additive non-breaking changes) — a single maintainer
  approval + green CI is enough.
- **Public-API / breaking changes** — require an issue with maintainer agreement on
  direction *before* implementation, and must fit the SemVer promise in
  [`docs/STABILITY.md`](docs/STABILITY.md). Because the surface is contract-locked
  across languages upstream, a public-API change may be declined here even if the
  code is correct, if it doesn't fit the cross-cutting contract.
- **Disagreement** — maintainers seek consensus; if none is reached, the decision
  defaults to the more conservative (surface-preserving) option and is escalated in
  the upstream monorepo.

## Becoming a maintainer

Sustained, high-quality contribution (reviews, well-scoped PRs, helpful triage) over
time. An existing maintainer nominates; the maintainer team agrees. There is no
fixed contribution count — judgement over metrics.

## Code of Conduct

All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
