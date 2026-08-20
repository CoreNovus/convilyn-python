"""Image conversion. Internal — no stability guarantee.

``convert_core`` transforms and saves; ``pil_formats`` maps this package's
format names onto Pillow's. Use ``convilyn.local`` rather than either directly.

Deliberately empty of imports, for the reason given in the parent package:
Pillow is an optional dependency, and importing it must stay the caller's
decision rather than a side effect of touching the package.
"""
