"""Turning text into vectors, behind an interface we own.

Two rules do most of the work here, and both come from CLAUDE.md:

* **Documents and queries are embedded differently.** Voyage wants
  `input_type="document"` for the things being stored and `input_type="query"`
  for what someone typed. Mixing them up costs retrieval quality silently —
  everything still works, it just finds slightly the wrong thing forever.
* **Vectors from different models are not comparable.** Every chunk records the
  model that made it, and search filters on that. `Embedder.name` is the
  identity that gets recorded, and it carries the dimension too: the same model
  truncated to 512 dimensions is a different vector space from one at 1024.

The interface is ours rather than the SDK's so that an offline dev box, a
different vendor or a future local model is a swap here instead of a change
everywhere.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

# voyageai 0.5.0 sends one HTTP request per `embed()` call and caps it at 128
# inputs (voyageai.VOYAGE_EMBED_BATCH_SIZE). Batching is ours to do.
VOYAGE_MAX_BATCH = 128

# A second cap, on size rather than count: a batch of long document chunks hits
# the per-request token limit well before it hits 128 inputs. Four characters to
# a token is the usual rough English ratio, so this is about 30k tokens.
VOYAGE_MAX_BATCH_CHARS = 120_000

# What to use instead on an account that is being paced, where the limit on
# tokens per minute is the one that bites: about 8k tokens a request.
VOYAGE_PACED_BATCH_CHARS = 20_000

# Retries are ours, not the SDK's. Its tenacity controller waits at most 16
# seconds, and every attempt spends a request from the same per-minute budget
# that just rejected us — on a capped account that burns the whole retry budget
# inside one window and still fails.
VOYAGE_ATTEMPTS = 6

# How long to stand down when the provider says we are going too fast. A rate
# limit is measured per minute, so waiting out the whole window is the only
# wait that reliably clears it.
RATE_LIMIT_BACKOFF_SECONDS = 60.0

# The offline stand-in. Also 1024 dimensions, which is the only reason it can
# use the same column.
LOCAL_MODEL = "BAAI/bge-large-en-v1.5"
# bge models are trained with this prefix on the query side only.
LOCAL_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingError(RuntimeError):
    """The embedding provider could not be reached or refused the input."""


@dataclass
class Usage:
    """What a run cost, for the ingest report and the eval script."""

    requests: int = 0
    tokens: int = 0
    texts: int = 0

    def record(self, *, texts: int, tokens: int) -> None:
        self.requests += 1
        self.texts += texts
        self.tokens += tokens


@dataclass
class Embedder(ABC):
    """What the rest of the system is allowed to know about embeddings."""

    model: str
    dimension: int
    usage: Usage = field(default_factory=Usage)

    @property
    def name(self) -> str:
        """The identity stored on every chunk and filtered on by every search.

        Includes the dimension on purpose: `voyage-4` at 512 dimensions and
        `voyage-4` at 1024 are different spaces, and a cosine distance between
        them is meaningless rather than merely wrong.
        """
        return f"{self.model}@{self.dimension}"

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed things being stored. Order in, order out."""

    @abstractmethod
    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed several things people typed, in one request where possible.

        The eval script embeds twenty questions at a time; doing that one
        request each is twenty times the rate-limit budget for the same tokens.
        """

    async def embed_query(self, text: str) -> list[float]:
        """Embed something a person typed."""
        return (await self.embed_queries([text]))[0]

    async def aclose(self) -> None:  # noqa: B027 — optional hook
        """Release anything held open. Always called by the CLIs."""

    def _check(self, vectors: list[list[float]], expected: int) -> list[list[float]]:
        if len(vectors) != expected:
            raise EmbeddingError(f"asked for {expected} vectors, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dimension:
                raise EmbeddingError(
                    f"{self.model} returned {len(vector)} dimensions, not {self.dimension}. "
                    "The chunks table is fixed at 1024; change EMBEDDING_DIM and re-embed."
                )
        return vectors


def batched(texts: Sequence[str], max_items: int, max_chars: int) -> list[list[str]]:
    """Split into requests that fit, by count and by size.

    A single text longer than the whole size budget still goes out on its own:
    truncation is the provider's decision to make and report, not ours to make
    silently by dropping it.
    """
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and (len(current) >= max_items or size + len(text) > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append(current)
    return batches


class VoyageEmbedder(Embedder):
    """Voyage AI, over the official async SDK."""

    def __init__(self, api_key: str, model: str, dimension: int, max_rpm: int = 0) -> None:
        if not api_key:
            raise EmbeddingError(
                "VOYAGE_API_KEY is not set. Set it, or set EMBEDDING_PROVIDER=local "
                "to embed offline."
            )
        # The concrete module rather than the package: voyageai re-exports
        # AsyncClient at runtime but does not declare it, and mypy is right to
        # say so.
        from voyageai.client_async import AsyncClient

        super().__init__(model=model, dimension=dimension)
        # max_retries=1 means one attempt: the retrying is done here, where the
        # wait can be long enough to matter.
        self._client = AsyncClient(api_key=api_key, max_retries=1)
        # A Voyage account without a payment method on it is capped at three
        # requests a minute and ten thousand tokens a minute. Pacing spaces the
        # requests out; the smaller batch keeps each one inside the token cap.
        self._min_interval = 60.0 / max_rpm if max_rpm > 0 else 0.0
        self._max_chars = VOYAGE_PACED_BATCH_CHARS if max_rpm else VOYAGE_MAX_BATCH_CHARS
        self._next_slot = 0.0
        self._turn = asyncio.Lock()

    async def _wait_turn(self) -> None:
        if not self._min_interval:
            return
        async with self._turn:
            now = asyncio.get_running_loop().time()
            if now < self._next_slot:
                log.info("pacing: waiting %.1fs for the next request slot", self._next_slot - now)
                await asyncio.sleep(self._next_slot - now)
            self._next_slot = asyncio.get_running_loop().time() + self._min_interval

    async def _once(self, batch: list[str], input_type: str) -> list[list[float]]:
        """Exactly one call, with no retrying and no interpretation."""
        result = await self._client.embed(
            batch,
            model=self.model,
            input_type=input_type,
            output_dimension=self.dimension,
        )
        self.usage.record(texts=len(batch), tokens=result.total_tokens)
        log.info(
            "voyage %s: %d texts, %d tokens (%d total this run)",
            input_type,
            len(batch),
            result.total_tokens,
            self.usage.tokens,
        )
        return [[float(x) for x in vector] for vector in result.embeddings]

    async def _embed_batch(
        self, batch: list[str], input_type: str, attempt: int = 1
    ) -> list[list[float]]:
        """One batch, halving it whenever the provider says it was too much.

        A rate limit is two limits — requests per minute and tokens per minute
        — and which one we hit is not in the error. Waiting fixes the first;
        only a smaller batch fixes the second, because the same oversized
        request will be refused for as long as we keep sending it. Halving
        handles both without anyone having to configure their account's exact
        ceilings, and short of a rate limit it never happens at all.
        """
        import voyageai.error as voyage_error

        transient = (voyage_error.ServiceUnavailableError, voyage_error.ServerError)
        await self._wait_turn()
        try:
            return await self._once(batch, input_type)
        except voyage_error.RateLimitError as exc:
            if attempt >= VOYAGE_ATTEMPTS:
                raise EmbeddingError(f"RateLimitError: {exc}") from exc
            wait = max(self._min_interval, RATE_LIMIT_BACKOFF_SECONDS)
            log.warning(
                "rate limited by Voyage on %d texts; waiting %.0fs then %s (attempt %d of %d)",
                len(batch),
                wait,
                "halving the batch" if len(batch) > 1 else "trying again",
                attempt,
                VOYAGE_ATTEMPTS,
            )
            await asyncio.sleep(wait)
            if len(batch) == 1:
                return await self._embed_batch(batch, input_type, attempt + 1)
            half = len(batch) // 2
            first = await self._embed_batch(batch[:half], input_type, attempt + 1)
            return first + await self._embed_batch(batch[half:], input_type, attempt + 1)
        except transient as exc:
            if attempt >= VOYAGE_ATTEMPTS:
                raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc
            await asyncio.sleep(min(2.0**attempt, 30.0))
            return await self._embed_batch(batch, input_type, attempt + 1)
        except voyage_error.VoyageError as exc:
            # Auth, malformed input, anything else: retrying changes nothing.
            raise EmbeddingError(f"{type(exc).__name__}: {exc}") from exc

    async def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in batched(texts, VOYAGE_MAX_BATCH, self._max_chars):
            vectors.extend(await self._embed_batch(batch, input_type))
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._check(await self._embed(texts, "document"), len(texts))

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._check(await self._embed(texts, "query"), len(texts))


class LocalEmbedder(Embedder):
    """fastembed on the CPU, for working with no network and no bill.

    Optional: `uv sync --group local` installs it. Its vectors are not
    interchangeable with Voyage's, which is exactly why `name` records which
    made them — switching providers leaves the old chunks unsearchable rather
    than quietly comparing apples to oranges, until a re-ingest replaces them.
    """

    def __init__(self, model: str = LOCAL_MODEL, dimension: int = 1024) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover — depends on the extra
            raise EmbeddingError(
                "EMBEDDING_PROVIDER=local needs fastembed: `uv sync --group local`"
            ) from exc

        super().__init__(model=model, dimension=dimension)
        self._encoder: Any = TextEmbedding(model_name=model)

    async def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        def run() -> list[list[float]]:
            return [[float(x) for x in vector] for vector in self._encoder.embed(list(texts))]

        vectors = await asyncio.to_thread(run)
        # No tokens are billed, but the counters still say how much work ran.
        self.usage.record(texts=len(texts), tokens=0)
        return vectors

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._check(await self._encode(texts), len(texts))

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [LOCAL_QUERY_PREFIX + text for text in texts]
        return self._check(await self._encode(prefixed), len(texts))


def get_embedder(settings: Settings | None = None) -> Embedder:
    """Build the embedder this deployment is configured for."""
    config = settings or get_settings()
    provider = config.embedding_provider.strip().lower()

    if provider == "voyage":
        return VoyageEmbedder(
            config.voyage_api_key,
            config.embedding_model,
            config.embedding_dim,
            max_rpm=config.embedding_max_rpm,
        )
    if provider == "local":
        return LocalEmbedder(dimension=config.embedding_dim)
    raise EmbeddingError(f"unknown EMBEDDING_PROVIDER {config.embedding_provider!r}: voyage|local")


@lru_cache(maxsize=1)
def shared_embedder() -> Embedder:
    """One embedder for the whole process, for the API to use.

    The local embedder loads a model into memory when it is built, so making a
    new one per request would be absurd; the Voyage one is cheap to build but
    keeping one means its token counters add up across a process rather than
    resetting every request.
    """
    return get_embedder()
