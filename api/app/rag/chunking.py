"""Cutting a document into passages worth retrieving.

A chunk has two jobs at once: it has to be small enough that its vector means
one thing, and complete enough that the answer is inside it rather than split
across the seam. Around five hundred tokens with a little overlap is the
setting that has survived contact with most corpora, and the overlap is what
saves the sentence that happens to land on a boundary.

Deliberately not token-exact. A real tokenizer here would mean downloading the
model's vocabulary to decide where to cut a policy document, and the provider
truncates anything over-long anyway. Four characters to a token is close enough
for a size budget.
"""

from __future__ import annotations

import re

CHARS_PER_TOKEN = 4
TARGET_TOKENS = 500
TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN
OVERLAP_CHARS = 200

_PARAGRAPH = re.compile(r"\n\s*\n")
# Sentence ends, for when a single paragraph is longer than a whole chunk.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _hard_wrap(text: str, size: int) -> list[str]:
    """Last resort: a run of text with no paragraph or sentence break in it."""
    return [text[i : i + size] for i in range(0, len(text), size)]


def _pieces(text: str, size: int) -> list[str]:
    """Break into units no larger than `size`, cutting at the best seam left."""
    out: list[str] = []
    for paragraph in _PARAGRAPH.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            out.append(paragraph)
            continue
        for sentence in _SENTENCE.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= size:
                out.append(sentence)
            else:
                out.extend(_hard_wrap(sentence, size))
    return out


def _tail(text: str, overlap: int) -> str:
    """The end of a chunk, to repeat at the start of the next one.

    Snapped to a word boundary: half a word helps nothing and embeds oddly.
    """
    if overlap <= 0 or len(text) <= overlap:
        return text
    window = text[-overlap:]
    space = window.find(" ")
    return window[space + 1 :] if space != -1 else window


def split_text(
    text: str, target_chars: int = TARGET_CHARS, overlap_chars: int = OVERLAP_CHARS
) -> list[str]:
    """Split prose into overlapping passages, largest seam first.

    Returns an empty list for empty input rather than one empty chunk: there is
    nothing to embed, and an empty vector would still cost a request.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for piece in _pieces(text, target_chars):
        if not current:
            current = piece
            continue
        if len(current) + 2 + len(piece) <= target_chars:
            current = f"{current}\n\n{piece}"
            continue
        chunks.append(current)
        overlap = _tail(current, overlap_chars)
        current = f"{overlap}\n\n{piece}" if overlap else piece

    if current:
        chunks.append(current)
    return chunks
