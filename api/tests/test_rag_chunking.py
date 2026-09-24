"""Splitting prose into passages.

No database and no provider: this is arithmetic on strings, and it is worth
testing on its own because every retrieval failure that starts with "the answer
was in the document but search never found it" ends here.
"""

from __future__ import annotations

from itertools import pairwise

from app.rag.chunking import OVERLAP_CHARS, TARGET_CHARS, split_text

PARAGRAPH = (
    "Sealed product is final sale once it leaves the shop. This is not us being "
    "difficult: we cannot resell a box that has been out of our sight, and neither "
    "can anyone else. Check the seal at the counter. "
)


def test_short_text_is_one_passage() -> None:
    """A returns policy that fits in one chunk gets one chunk, not one per
    paragraph: splitting it would put the question in a different vector from
    its answer."""
    assert split_text("Open Tuesday to Sunday.\n\nClosed Mondays.") == [
        "Open Tuesday to Sunday.\n\nClosed Mondays."
    ]


def test_empty_text_produces_nothing() -> None:
    """An empty passage would still cost a request to embed."""
    assert split_text("") == []
    assert split_text("   \n\n  ") == []


def test_long_text_splits_near_the_target_size() -> None:
    text = "\n\n".join(PARAGRAPH for _ in range(20))
    chunks = split_text(text)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= TARGET_CHARS + OVERLAP_CHARS + 2


def test_consecutive_passages_overlap() -> None:
    """The overlap is what saves a sentence that lands on a boundary.

    Without it, a policy sentence split across two chunks is in neither of
    them, and the one question it answers is the one search cannot.
    """
    text = "\n\n".join(f"Paragraph {i}. {PARAGRAPH}" for i in range(12))
    chunks = split_text(text)

    assert len(chunks) >= 2
    for earlier, later in pairwise(chunks):
        tail = earlier[-OVERLAP_CHARS:]
        shared = later[: len(tail)]
        assert any(word in tail for word in shared.split()[:3]), "no overlap between passages"


def test_a_paragraph_longer_than_a_chunk_is_split_at_sentences() -> None:
    """Falling back to a smaller seam rather than cutting mid-word."""
    sentence = "The shop is on Gay Street in downtown Knoxville and opens at eleven. "
    chunks = split_text(sentence * 80)

    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)
    # Nothing was dropped: every chunk is made of whole sentences.
    assert all(chunk.rstrip().endswith(".") for chunk in chunks)
