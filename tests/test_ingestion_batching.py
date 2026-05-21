"""
Tests for the bulk-insert path added in PR-6.

Covers:
  - DocumentChunkRepository.bulk_create_chunks: one add_all, one flush,
    no per-row flushes regardless of count.
  - IngestionService.index_document calls bulk_create_chunks once with
    all (content, embedding) pairs, not in a loop.
  - Empty input is handled gracefully (returns 0 inserts).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Repository-level: bulk_create_chunks ─────────────────────────────────────


async def test_bulk_create_chunks_does_one_flush_for_many_rows():
    """A 500-chunk document must produce exactly one session.flush() call."""
    from db.repositories.document_repository import DocumentChunkRepository

    session = MagicMock()
    session.add_all = MagicMock()
    session.flush = AsyncMock()

    repo = DocumentChunkRepository(session)
    pairs = [(f"chunk {i}", [0.1, 0.2, 0.3]) for i in range(500)]

    count = await repo.bulk_create_chunks(document_id=42, contents_and_embeddings=pairs)

    assert count == 500
    # One add_all call carrying all 500 ORM objects
    session.add_all.assert_called_once()
    added = session.add_all.call_args.args[0]
    assert len(added) == 500
    # Each object has the right document_id and a monotonic chunk_index
    for i, obj in enumerate(added):
        assert obj.document_id == 42
        assert obj.chunk_index == i
        assert obj.content == f"chunk {i}"
        assert obj.embedding == [0.1, 0.2, 0.3]
    # And one flush — not 500
    session.flush.assert_awaited_once()


async def test_bulk_create_chunks_handles_empty_input():
    """Empty list short-circuits to 0 without touching the session."""
    from db.repositories.document_repository import DocumentChunkRepository

    session = MagicMock()
    session.add_all = MagicMock()
    session.flush = AsyncMock()

    repo = DocumentChunkRepository(session)
    count = await repo.bulk_create_chunks(document_id=1, contents_and_embeddings=[])

    assert count == 0
    session.add_all.assert_not_called()
    session.flush.assert_not_called()


async def test_bulk_create_chunks_preserves_order_and_indexing():
    """chunk_index must equal position in the input list."""
    from db.repositories.document_repository import DocumentChunkRepository

    session = MagicMock()
    session.add_all = MagicMock()
    session.flush = AsyncMock()

    repo = DocumentChunkRepository(session)
    pairs = [
        ("alpha", [1.0]),
        ("beta", [2.0]),
        ("gamma", [3.0]),
    ]
    await repo.bulk_create_chunks(document_id=7, contents_and_embeddings=pairs)

    added = session.add_all.call_args.args[0]
    assert [o.content for o in added] == ["alpha", "beta", "gamma"]
    assert [o.chunk_index for o in added] == [0, 1, 2]
    assert [o.embedding for o in added] == [[1.0], [2.0], [3.0]]


# ── Service-level: IngestionService uses the bulk path ───────────────────────


async def test_ingestion_service_uses_bulk_path(monkeypatch):
    """
    The service must call bulk_create_chunks exactly once, with all pairs
    in a single call — not create_chunk in a loop.
    """
    # Heavy deps (Embedder pulls sentence_transformers, DocumentLoader pulls
    # pymupdf). Stub them BEFORE importing IngestionService.
    import sys
    import types

    fake_embedder_mod = types.ModuleType("rag.embedder")
    class _StubEmbedder:
        def __init__(self):
            pass
        async def embed_batch_async(self, texts):
            return [[0.1, 0.2] for _ in texts]
    fake_embedder_mod.Embedder = _StubEmbedder
    monkeypatch.setitem(sys.modules, "rag.embedder", fake_embedder_mod)

    fake_loader_mod = types.ModuleType("ingestion.loader")
    class _StubLoader:
        def load(self, path, mime):
            return "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    fake_loader_mod.DocumentLoader = _StubLoader
    monkeypatch.setitem(sys.modules, "ingestion.loader", fake_loader_mod)

    # Now import the service.
    import importlib
    import services.ingestion_service as svc_mod
    importlib.reload(svc_mod)

    # Stand-in document.
    doc = MagicMock()
    doc.id = 99
    doc.file_path = "/fake/path.txt"
    doc.mime_type = "text/plain"
    doc.original_filename = "test.txt"

    session = MagicMock()
    svc = svc_mod.IngestionService(session)

    # Spy on both: the new bulk path AND the old per-row path.
    svc.chunk_repo.bulk_create_chunks = AsyncMock(return_value=3)
    svc.chunk_repo.create_chunk = AsyncMock()  # must NOT be called
    svc.doc_repo.mark_indexed = AsyncMock()

    await svc.index_document(doc)

    # Bulk path called exactly once.
    svc.chunk_repo.bulk_create_chunks.assert_awaited_once()
    # Per-row path NOT called.
    svc.chunk_repo.create_chunk.assert_not_awaited()
    # Document marked indexed at the end.
    svc.doc_repo.mark_indexed.assert_awaited_once_with(99)

    # Verify the bulk call was made with the right shape — list of
    # (content, embedding) pairs, one per non-empty chunk produced.
    call = svc.chunk_repo.bulk_create_chunks.await_args
    assert call.kwargs["document_id"] == 99
    pairs = call.kwargs["contents_and_embeddings"]
    assert len(pairs) >= 1
    for content, embedding in pairs:
        assert isinstance(content, str) and content.strip()
        assert embedding == [0.1, 0.2]
