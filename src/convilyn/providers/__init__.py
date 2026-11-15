"""Connectors to the model services you already use.

``convilyn.providers`` turns "call the embeddings API" and "call the rerank
API" into things that behave the same across services: a connector sends as
much per request as the service takes, retries what is worth retrying, checks
every answer, and puts the answers of several requests together. Each
service's limits are data you can read and override, not surprises you meet in
production.

    from convilyn import providers

    with providers.embedder("openai", model="text-embedding-3-small") as embedder:
        result = embedder.embed(["refund policy", "shipping times"])
        result.vectors            # one unit-length vector per text, in order

    with providers.reranker("cohere", model="rerank-v4.0-pro") as reranker:
        result = reranker.rerank("how long do refunds take", shortlist)
        result.ranking            # (index, score) for each document, best first

``providers.aembedder()`` and ``providers.areranker()`` are the same things for
async code, and ``providers.available()`` lists the services with what is
known about each, including which of the two each one offers.

**This is the one place in the SDK where your text leaves your machine for a
service other than Convilyn.** It goes to the address you chose, under your
key, and nowhere else. The key is read from the argument or from the
environment variable the provider's row names; this SDK never writes it to a
file, an error message, a ``repr`` or a log, and never puts it in an address.
Plain ``http`` is accepted only for this machine (``localhost``, ``127.0.0.1``
or ``::1``), and a redirect is never
followed. Nothing here runs a model locally.

**You stay in charge of the connection.** Pass ``http_client=`` — your own
``httpx`` client — for a proxy, mutual TLS, a signing hook or credentials that
rotate. With a client that authenticates on its own, no key is needed. What
that client does with a request (its proxies, hooks and logging) is as you set
it up.

**Limits are defaults, and unknown models are allowed.** ``max_inputs``,
``max_input_tokens`` and ``max_documents`` override what the row says; a model
the row has never heard of is sent as is. A text that is too long to embed is
refused by name when you pass a ``measure`` to count it with;
``on_overflow="truncate"`` sends its beginning instead.

## How this package is laid out

**A leading underscore means internal**, as everywhere in this SDK.

Public, and covered by the stability policy:

* this module — the only import a user needs
* ``types`` — frozen models
* ``errors`` — what is raised at run time

Internal, and free to change in any release:

* ``_profiles`` — the providers, as rows of data
* ``_wires`` — request and response formats
* ``_steps`` — the fixed sequence every call goes through
* ``_transport`` — the address, the key and the sending, shared by every connector
* ``_embedder``, ``_reranker`` — the synchronous and asynchronous connectors
* ``_factory`` — the functions this module re-exports
"""

# No `from __future__ import annotations` here: it would bind a public
# attribute named `annotations` on a namespace whose export set is frozen.
from convilyn.providers._embedder import AsyncEmbedder, Embedder
from convilyn.providers._factory import aembedder, areranker, available, embedder, reranker
from convilyn.providers._reranker import AsyncReranker, Reranker
from convilyn.providers.errors import ErrorCategory, ProviderError
from convilyn.providers.types import (
    EmbedKind,
    EmbedResult,
    OverflowPolicy,
    ProviderInfo,
    RankedDocument,
    RerankResult,
)

__all__ = [
    "AsyncEmbedder",
    "AsyncReranker",
    "EmbedKind",
    "EmbedResult",
    "Embedder",
    "ErrorCategory",
    "OverflowPolicy",
    "ProviderError",
    "ProviderInfo",
    "RankedDocument",
    "RerankResult",
    "Reranker",
    "aembedder",
    "areranker",
    "available",
    "embedder",
    "reranker",
]
