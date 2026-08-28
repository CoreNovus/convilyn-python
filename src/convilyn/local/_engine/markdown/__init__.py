"""Document-to-Markdown extraction. Internal — no stability guarantee.

``registry`` maps a source format to the extractor that reads it, each extractor
returns the ``model.MarkdownDoc`` intermediate, and ``render`` turns that into
Markdown. Use ``convilyn.local`` rather than any of it directly.

Deliberately empty of imports, for the reason given in the parent package: the
extractors pull heavy optional dependencies, and importing one must stay the
caller's decision rather than a side effect of touching the package.
"""
