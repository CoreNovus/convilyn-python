"""Single source of truth for ``convilyn``'s package version.

Read by both:

* ``pyproject.toml`` via ``[tool.hatch.version]`` (so the wheel
  metadata + ``importlib.metadata.version("convilyn")`` agree)
* ``convilyn.__init__`` for the in-process ``__version__`` attribute
  that callers may read at runtime.

Bump this file in CHANGELOG-bumping commits — never edit the
hardcoded constant in two places.
"""

__version__ = "4.0.0"
