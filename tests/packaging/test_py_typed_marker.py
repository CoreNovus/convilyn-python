"""PEP 561 marker — anti-rot guard.

`pyproject.toml` declares the ``Typing :: Typed`` classifier. Per PEP 561,
that promise is only honoured when ``py.typed`` ships inside the
installed package. If the marker is ever removed (e.g. someone deletes
``src/convilyn/py.typed`` thinking it's empty cruft), IDEs go back to
silently ignoring the SDK's type hints — and the user has no obvious
diagnostic.

This test fails fast in that scenario.
"""

from __future__ import annotations

from pathlib import Path


def test_py_typed_marker_present() -> None:
    """The PEP 561 marker file must exist at ``src/convilyn/py.typed``."""
    marker = Path(__file__).parent.parent.parent / "src" / "convilyn" / "py.typed"
    assert marker.exists(), (
        "PEP 561 marker missing — re-create an empty file at "
        f"{marker.relative_to(Path(__file__).parent.parent.parent)} so the "
        "'Typing :: Typed' classifier in pyproject.toml isn't a lie."
    )


def test_pyproject_typed_classifier_present() -> None:
    """Inverse guard — if the marker exists, the classifier must too."""
    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert '"Typing :: Typed"' in text, (
        "pyproject.toml should declare 'Typing :: Typed' to match the "
        "shipped py.typed marker — otherwise PyPI metadata under-sells "
        "the SDK's type support."
    )
