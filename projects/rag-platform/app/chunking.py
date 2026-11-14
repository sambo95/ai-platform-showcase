"""Heading-aware chunking for Markdown and plain text."""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING = re.compile(r"^#{1,6}\s+(.*)$")


@dataclass(frozen=True)
class Chunk:
    source: str
    index: int
    heading: str
    text: str


def _sections(text: str) -> list[tuple[str, str]]:
    """Split into (heading, body) pairs so a chunk never straddles two topics."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            sections.append((match.group(1).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(body).strip()) for h, body in sections if "\n".join(body).strip()]


def _pack(body: str, max_chars: int, overlap: int) -> list[str]:
    """Greedily pack paragraphs up to max_chars, carrying a tail overlap forward."""
    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = paragraph.strip()
        while len(paragraph) > max_chars:  # hard-split oversized paragraphs
            head, paragraph = paragraph[:max_chars], paragraph[max_chars - overlap :]
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) + 2 > max_chars:
            pieces.append(current)
            carry = current[-overlap:].strip() if overlap else ""
            # Drop the overlap rather than exceed max_chars.
            current = carry if len(carry) + len(paragraph) + 2 <= max_chars else ""
        current = f"{current}\n\n{paragraph}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_document(source: str, text: str, max_chars: int = 800, overlap: int = 120) -> list[Chunk]:
    if overlap >= max_chars:
        raise ValueError("overlap must be smaller than max_chars")
    chunks: list[Chunk] = []
    for heading, body in _sections(text):
        for piece in _pack(body, max_chars, overlap):
            # Prefix the heading: it is often the best retrieval signal in the section.
            content = f"{heading}\n{piece}" if heading else piece
            chunks.append(Chunk(source, len(chunks), heading, content))
    return chunks
