# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Report privately, one of:

- **GitHub private advisory** (preferred) — the [Report a vulnerability](../../security/advisories/new)
  button on this repo (Security → Advisories).
- **Email** `security@corenovus.com`.

Please include: affected version(s), a description, reproduction steps or a PoC,
and the impact you observed. We support coordinated disclosure and will credit you
unless you prefer to remain anonymous.

## What to expect

| Step | Target |
|---|---|
| Acknowledgement of your report | within **3 business days** |
| Initial assessment + severity | within **10 business days** |
| Fix + coordinated disclosure | severity-dependent; we'll keep you updated |

## Scope

- **In scope**: this package's published code (the mirror), its dependencies as
  they are used here, and the client's handling of credentials, URLs, and file
  I/O.
- The fix lands in the upstream monorepo (this repo is a downstream mirror) and is
  then released to the package registry + refreshed here.
- **Out of scope**: the hosted Convilyn platform APIs themselves — report those via
  `security@corenovus.com` as well, but they are handled through the platform's own
  process, not this repo's release.

## Supported versions

Security fixes are provided for the **latest released** major/minor line. Pre-1.0
and pre-release (`bN`/`rcN`) versions are supported on a best-effort basis while in
beta.

## Our commitments

- We will not take legal action against good-faith research that respects this
  policy, avoids privacy violations / data destruction / service degradation, and
  gives us reasonable time to remediate before public disclosure.
- Credentials are never logged; the client masks API keys and redacts tokens from
  URLs — if you find a case where it doesn't, that's exactly what we want to hear.
