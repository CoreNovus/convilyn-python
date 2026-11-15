"""Wire formats: the request and response shapes providers speak.

There are few of these and many providers. A provider's row names its format;
supporting a new format is a module here and a line in one of the tables below.
"""

from __future__ import annotations

from convilyn.providers._wires import cohere_rerank, gemini_embeddings, openai_embeddings
from convilyn.providers._wires.shapes import EmbedWire, RerankWire

#: The embeddings formats, by the name a provider's row gives.
EMBED_WIRES: dict[str, EmbedWire] = {
    "openai_embeddings": openai_embeddings.WIRE,
    "gemini_embeddings": gemini_embeddings.WIRE,
}

#: The reranking formats, by the name a provider's row gives.
RERANK_WIRES: dict[str, RerankWire] = {
    "cohere_rerank": cohere_rerank.WIRE,
}
