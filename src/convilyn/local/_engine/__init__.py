"""Generated conversion engine. Internal — no stability guarantee.

Every module under this package is projected from Convilyn's server-side
conversion engine by ``scripts/oss/project_local_engine.py`` and regenerated
whenever that engine changes. Do not edit them; a drift gate compares them
against a fresh projection and fails on any hand edit.

This file and the sibling package ``__init__`` files are the exception: they
are hand-written, so there is no fresh projection to compare them against and
the drift gate does not cover them. What does cover them is the projection's
truth pass, which requires every name they cite to exist.

Do not import from here either. ``convilyn.local`` is the public surface and the
only one covered by the SDK's semantic-versioning promise — see
``docs/STABILITY.md``. Names in this package may move, be renamed, or disappear
in a patch release.

Deliberately empty of imports. A facade here would make every caller pay for
pdfplumber, python-pptx and Pillow whether or not they convert anything, and
``import convilyn`` must keep working with none of the optional extras
installed. Import the specific submodule you need instead.
"""
