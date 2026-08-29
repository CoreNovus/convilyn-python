"""Isolate the local credentials file from every test in this package.

``resolve_auth`` reads three sources, and the third is the credentials file
``convilyn setup`` writes to the user's real config directory. Nothing scoped
that away, so any test asserting "no credential is available" was really
asserting "no credential is available **and nobody has ever run `convilyn
setup` on this machine**".

That is not hypothetical and it is not rare: it is what happens the first time a
contributor uses the tool they are working on. Four tests went red the moment
this repo's own operator signed in for real —
``test_auth.py::test_neither_raises``,
``test_auth.py::test_empty_string_treated_as_missing``,
``test_client.py::test_missing_key_raises_auth_error``, and
``test_client.py::test_empty_key_raises_auth_error`` — none of which had
changed, and none of which were about credentials files at all. Pointing
``APPDATA`` at an empty directory turned all four green again, which is the
whole diagnosis.

``git-workflow.md`` names the shape: *"A gate that queries filesystem state is
measuring the machine, not the code… it fails in both directions: red on a
dirty machine, green on a stale one."* The red direction is what surfaced here;
the green direction is worse and was equally available — a test asserting a key
IS resolved would have passed off the operator's real key.

Autouse and package-wide rather than per-module. A per-module fixture only
protects the modules somebody remembered to add it to, and the failing four
were spread across two files that share no fixture and no theme. The point is
that no test in this package should be able to see the host's credentials,
whether or not its author was thinking about credentials.

Tests that need a credentials file write one through
``credentials.write_credentials`` and get it inside the isolated root, so this
takes nothing away — ``tests/unit/cli/test_setup.py`` already had its own
narrower version of this fixture and keeps working unchanged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn._internal import credentials

_REAL_ROOT_MARKER = "uses_real_config_root"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{_REAL_ROOT_MARKER}: exercise the real config_root() instead of the isolated one.",
    )


@pytest.fixture(autouse=True)
def _isolated_credentials_root(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path | None:
    """Point the credentials file at an empty per-test directory.

    Opted out of by ``@pytest.mark.uses_real_config_root``, for the one class
    whose subject IS ``config_root`` — replacing the function under test with a
    stub makes its assertions vacuous rather than isolated. That class already
    said so in its own docstring ("deliberately not using `isolated_root`");
    the marker is what makes the statement enforceable now that the isolation
    is automatic.
    """
    if request.node.get_closest_marker(_REAL_ROOT_MARKER):
        return None
    root = tmp_path / "convilyn-config-isolated"
    monkeypatch.setattr(credentials, "config_root", lambda: root)
    return root
