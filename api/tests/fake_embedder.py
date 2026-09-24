"""A deterministic stand-in for a real embedding provider.

Tests about ingest and search are about bookkeeping — what gets re-embedded,
what gets deleted, which tenant's rows come back — and none of that needs a real
model. This one derives a stable unit vector from a hash of the text, so the
same text always lands in the same place and the same run always gives the same
answer, and it counts every call so a test can assert that nothing was embedded
at all.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Sequence

from app.rag.embeddings import Embedder


class FakeEmbedder(Embedder):
    """Stable pseudo-random vectors, and a tally of what was asked for."""

    def __init__(self, model: str = "fake-test-model", dimension: int = 1024) -> None:
        super().__init__(model=model, dimension=dimension)
        self.document_calls = 0
        self.query_calls = 0
        self.documents_embedded: list[str] = []

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
        noise = random.Random(seed)
        raw = [noise.gauss(0, 1) for _ in range(self.dimension)]
        length = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / length for x in raw]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self.document_calls += 1
        self.documents_embedded.extend(texts)
        self.usage.record(texts=len(texts), tokens=sum(len(t) // 4 for t in texts))
        return [self._vector(text) for text in texts]

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self.query_calls += 1
        self.usage.record(texts=len(texts), tokens=sum(len(t) // 4 for t in texts))
        return [self._vector(text) for text in texts]
