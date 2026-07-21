"""Anti-rot guard — ``_version.py`` and ``CHANGELOG.md`` must agree.

When the next release bumps the version, both files have to change:

1. ``src/convilyn/_version.py::__version__``  (read by Hatch + runtime)
2. ``CHANGELOG.md``                            (the newest H2 heading)

A bump-only-one-file mistake is a silent footgun — the package
publishes with a higher version than the changelog describes, users
miss the new entries, and the divergence persists across releases.

This test pairs the two values at CI time. If they diverge, the test
fails fast with a clear pointer to which file to update.

The CHANGELOG format follows Keep-a-Changelog: the latest released
version sits in the first H2 heading shaped like ``## [X.Y.Z] — ...``.
Anything else (e.g. an unreleased section) is rejected so we never
silently approve a half-finished bump.
"""

from __future__ import annotations

import re
from pathlib import Path

_LATEST_HEADING_PATTERN = re.compile(
    r"^##\s+\[(?P<version>\d+\.\d+\.\d+(?:[abc]\d+|rc\d+)?)\]",
    re.MULTILINE,
)


def _read_version_constant() -> str:
    """Parse ``__version__`` directly from ``_version.py`` (no import).

    Importing the SDK at test-time would tie this guard to the SDK
    being installable, which is the wrong invariant — we want the
    constant to be valid Python AND match the changelog regardless
    of whether deps install cleanly.
    """
    version_file = Path(__file__).parent.parent.parent / "src" / "convilyn" / "_version.py"
    match = re.search(
        r'^__version__\s*=\s*["\'](?P<v>[^"\']+)["\']',
        version_file.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"could not parse __version__ from {version_file}"
    return match.group("v")


def _read_latest_changelog_version() -> str:
    """Return the first ``## [X.Y.Z]`` heading in CHANGELOG.md."""
    changelog = Path(__file__).parent.parent.parent / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    match = _LATEST_HEADING_PATTERN.search(text)
    assert match, (
        f"no released-version heading found in {changelog}; "
        "expected the first H2 to look like `## [1.0.0] — 2026-...`"
    )
    return match.group("version")


def test_version_and_changelog_agree() -> None:
    """``_version.py`` must match the latest released CHANGELOG entry.

    Failure modes this catches:
      * bumped ``_version.py`` but forgot to add a CHANGELOG entry
      * added a CHANGELOG entry but forgot to bump ``_version.py``
      * pasted the wrong version number into either file
    """
    code_version = _read_version_constant()
    changelog_version = _read_latest_changelog_version()
    assert code_version == changelog_version, (
        f"version drift: _version.py says {code_version!r} but the "
        f"top CHANGELOG entry is {changelog_version!r}. Bump both "
        "files in the same commit."
    )
