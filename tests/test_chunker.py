"""
Unit tests for ingestion/chunker.py.

These tests pin down current behavior — they document what the chunker
actually does so any future change is intentional, not accidental.
"""
from __future__ import annotations

from ingestion.chunker import TextChunker


def test_empty_input_returns_empty_list():
    assert TextChunker().chunk("") == []
    assert TextChunker().chunk("   \n\n  \t  ") == []


def test_short_text_produces_single_chunk():
    text = "Hello world."
    chunks = TextChunker(chunk_size=200, chunk_overlap=20).chunk(text)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "Hello world."


def test_long_text_produces_multiple_chunks():
    sentence = "This is a sentence. "
    text = sentence * 200  # ~4000 chars
    chunks = TextChunker(chunk_size=200, chunk_overlap=20).chunk(text)
    assert len(chunks) > 1
    # All chunks are non-empty after the strip filter.
    assert all(c["content"].strip() for c in chunks)
    # Recursive descent must keep every chunk within (roughly) chunk_size —
    # no single oversized chunk should survive.
    assert all(len(c["content"]) <= 200 for c in chunks)


def test_chunks_preserve_content():
    """Every word in the input must appear in at least one chunk."""
    text = " ".join(f"token{i}" for i in range(100))
    chunks = TextChunker(chunk_size=100, chunk_overlap=20).chunk(text)
    joined = " ".join(c["content"] for c in chunks)
    for i in range(100):
        assert f"token{i}" in joined


def test_overlap_creates_continuity_between_adjacent_chunks():
    """
    With overlap > 0 we expect adjacent chunks to share at least one token.
    This is the property RAG retrieval depends on.
    """
    text = " ".join(f"word{i}" for i in range(200))  # 200 short tokens
    chunks = TextChunker(chunk_size=80, chunk_overlap=40).chunk(text)
    assert len(chunks) >= 2
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tokens = set(prev["content"].split())
        next_tokens = set(nxt["content"].split())
        assert prev_tokens & next_tokens, (
            f"No overlap between consecutive chunks:\n  prev={prev['content']!r}\n  next={nxt['content']!r}"
        )


def test_returns_list_of_dicts_with_content_key():
    chunks = TextChunker().chunk("Some text to split here.")
    assert isinstance(chunks, list)
    for c in chunks:
        assert isinstance(c, dict)
        assert "content" in c
        assert isinstance(c["content"], str)


def test_paragraph_boundary_respected_when_possible():
    """
    Given two paragraphs that each fit within chunk_size, we should NOT split
    mid-paragraph — paragraphs are the first separator the chunker tries.
    """
    p1 = "First paragraph stays together."
    p2 = "Second paragraph also stays together."
    text = f"{p1}\n\n{p2}"
    chunks = TextChunker(chunk_size=40, chunk_overlap=5).chunk(text)
    # Each paragraph (each well under 40 chars) should appear intact in some chunk.
    joined = "|".join(c["content"] for c in chunks)
    assert p1 in joined
    assert p2 in joined
