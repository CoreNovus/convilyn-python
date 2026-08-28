"""Media conversion. Internal — no stability guarantee.

``convert_core`` decides what a conversion runs; ``formats`` is the container
vocabulary; ``hardening`` restricts what the program is allowed to open. Use
``convilyn.local`` rather than any of them directly.

Deliberately empty of imports, like its sibling packages: nothing here should
execute as a side effect of touching the package.
"""
