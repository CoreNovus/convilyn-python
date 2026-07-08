# Releasing `convilyn`

Releases are cut **from the monorepo** (the source of truth), published to PyPI, and
then reflected here as a new `Release vX.Y.Z` mirror commit. Contributors never cut
releases from this repo.

## Versioning

- **SemVer.** The public surface + promise are defined in
  [`docs/STABILITY.md`](docs/STABILITY.md).
- **SSOT = `src/*/_version.py`** in the monorepo. Bumping it is the release trigger.
- **Pre-releases** use PEP 440 suffixes (`1.1.1b4`). `pip install convilyn`
  installs the latest *stable*; `pip install --pre convilyn` opts into betas.

## Cut a release (maintainers)

1. **Bump** `src/*/_version.py` in the monorepo via a PR (+ a `CHANGELOG.md` entry);
   merge to the integration branch.
2. **Tag** the release commit with the manifest-driven tag:
   `sdk-consumer-python/vX.Y.Z` or `sdk-author-python/vX.Y.Z`, then push the tag.
3. **OIDC publish (no secrets).** The tag triggers `.github/workflows/sdk-publish.yml`
   in the monorepo — resolve → build → clean-venv install/import smoke → **PyPI
   Trusted Publishing over OIDC**. No long-lived API token is ever used.
   - One-time setup on PyPI: add a *Trusted Publisher* for the `convilyn` project
     bound to the `CoreNovus/convilyn` repo + the `sdk-publish.yml` workflow +
     release environment.
4. **Refresh this mirror:** regenerate with `scripts/oss/mirror_sdk.py` and push the
   new `Release vX.Y.Z` (append-only, plain `git push` — no force). Run
   `scripts/oss/validate_mirror_ci.sh <sdk>` first so the mirror's CI is green before
   the push.

## Beta → stable checklist

- [ ] Public surface matches `docs/STABILITY.md` and the contract tests are green.
- [ ] `CHANGELOG.md` has a dated entry; no `[Unreleased]` gaps.
- [ ] A `--pre` install smoke test passes on a clean venv (import + a real call).
- [ ] Drop the `bN` suffix in `_version.py`; tag `…/vX.Y.Z`; OIDC-publish; refresh
      the mirror.

## Never

- Never `twine upload` with a long-lived token — use the OIDC tag flow.
- Never force-push the mirror or re-tag a published version (PyPI versions are
  immutable — bump instead).
