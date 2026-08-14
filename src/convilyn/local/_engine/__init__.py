"""Generated conversion engine. Internal — no stability guarantee.

Everything under this package is projected from Convilyn's server-side
conversion engine by ``scripts/oss/project_local_engine.py`` and regenerated
whenever that engine changes. Do not edit these files; a drift gate compares
them against a fresh projection and fails on any hand edit.

Do not import from here either. ``convilyn.local`` is the public surface and the
only one covered by the SDK's semantic-versioning promise — see
``docs/STABILITY.md``. Names in this package may move, be renamed, or disappear
in a patch release.

Deliberately empty of imports. A facade here would make every caller pay for
pdfplumber, python-pptx and Pillow whether or not they convert anything, and
``import convilyn`` must keep working with none of the optional extras
installed. Import the specific submodule you need instead.
"""
