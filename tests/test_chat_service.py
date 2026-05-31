"""
Tests for services/chat_service.py.

Two behaviors PR-5 introduced:

  1. `spawn_background_task` holds a strong reference until the task is
     done, so the canonical 'task gets GC'd mid-flight' bug can't occur.

  2. `stream_response` ALWAYS emits a final `data: {"done": true}` frame —
     even when intermediate steps raise — so the SSE frontend never hangs
     with its UI lock held.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from services import chat_service as cs


# ── spawn_background_task ────────────────────────────────────────────────────


async def test_spawn_background_task_keeps_reference_until_done():
    """
    Without the registry, a 'create_task and forget' could be GC'd mid-flight.
    Here we don't keep our own reference; the registry must keep the task alive.
    """
    started = asyncio.Event()
    finished = asyncio.Event()

    async def work():
        started.set()
        await asyncio.sleep(0)
        finished.set()

    cs.spawn_background_task(work())

    await started.wait()
    assert len(cs._BACKGROUND_TASKS) >= 1

    await finished.wait()
    await asyncio.sleep(0)
    # Check membership, not size==0 — other tests may leave tasks in flight.
    assert not any(
        t for t in cs._BACKGROUND_TASKS if t.get_coro().__name__ == "work"
    )


async def test_spawn_background_task_returns_task():
    async def noop():
        return 42

    t = cs.spawn_background_task(noop())
    assert isinstance(t, asyncio.Task)
    assert await t == 42


# ── stream_response always emits done ────────────────────────────────────────


def _fake_session_factory(session: MagicMock):
    """Build an async-contextmanager factory that yields the given mock session."""
    @asynccontextmanager
    async def factory():
        yield session
    return factory


def _make_user(*, is_superuser: bool = False, role_ids: list[int] | None = None):
    user = MagicMock()
    user.id = 1
    user.role_ids = role_ids if role_ids is not None else [1]
    user.is_superuser = is_superuser
    return user


async def _drain(agen):
    """Collect all yielded frames from an async generator into a list."""
    return [frame async for frame in agen]


async def test_stream_emits_done_when_user_save_fails(monkeypatch):
    """
    If the very first DB write (saving the user message) raises, the function
    must still emit a single error frame followed by done, then return cleanly.
    """
    failing_session = MagicMock()
    conv_repo = AsyncMock()
    conv_repo.add_message.side_effect = RuntimeError("DB is on fire")

    monkeypatch.setattr(cs, "AsyncSessionLocal", _fake_session_factory(failing_session))
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: conv_repo)

    service = cs.ChatService(user=_make_user())
    frames = await _drain(service.stream_response(conversation_id=1, user_message="hi"))

    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    assert any('"error"' in f for f in frames[:-1])


async def test_stream_emits_done_when_retrieval_fails(monkeypatch):
    """
    Retrieval/streaming raises mid-flight. The existing inner except catches
    it, the assistant-save and auto-title best-efforts run, and the finally
    block guarantees done is the LAST frame.
    """
    session = MagicMock()
    session.commit = AsyncMock()

    conv_repo = AsyncMock()
    conv_repo.add_message = AsyncMock()
    conv_repo.touch_updated_at = AsyncMock()
    conv_repo.get_recent_messages = AsyncMock(return_value=[])
    conv_repo.count_messages = AsyncMock(return_value=1)

    monkeypatch.setattr(cs, "AsyncSessionLocal", _fake_session_factory(session))
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: conv_repo)

    # Make the lazy rag import explode.
    import sys
    import types
    bad_retriever = types.ModuleType("rag.retriever")
    class _BoomRetriever:
        def __init__(self, *a, **k):
            raise RuntimeError("retrieval pipeline exploded")
    bad_retriever.Retriever = _BoomRetriever
    bad_pipeline = types.ModuleType("rag.pipeline")
    bad_pipeline.RAGPipeline = MagicMock
    monkeypatch.setitem(sys.modules, "rag.retriever", bad_retriever)
    monkeypatch.setitem(sys.modules, "rag.pipeline", bad_pipeline)

    service = cs.ChatService(user=_make_user(is_superuser=True))
    frames = await _drain(service.stream_response(conversation_id=1, user_message="hi"))

    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    assert any('"error"' in f for f in frames[:-1])


async def test_stream_emits_done_on_happy_path(monkeypatch):
    """Sanity check: the well-behaved path also ends with done."""
    session = MagicMock()
    session.commit = AsyncMock()

    conv_repo = AsyncMock()
    conv_repo.add_message = AsyncMock()
    conv_repo.touch_updated_at = AsyncMock()
    conv_repo.get_recent_messages = AsyncMock(return_value=[])
    conv_repo.count_messages = AsyncMock(return_value=1)  # not 2, so no auto-title

    monkeypatch.setattr(cs, "AsyncSessionLocal", _fake_session_factory(session))
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: conv_repo)

    import sys
    import types

    class _StubRetriever:
        def __init__(self, *a, **k):
            pass
        async def retrieve(self, *a, **k):
            return []
        async def resolve_persona_role(self):
            # Resolved eagerly inside the session block (None = generic persona).
            return None

    class _StubPipeline:
        def with_retriever(self, _r):
            return self
        async def _stream_from_chunks(self, query, chunks, history, role=None):
            for tok in ("hello", " world"):
                yield tok

    retr_mod = types.ModuleType("rag.retriever")
    retr_mod.Retriever = _StubRetriever
    pipe_mod = types.ModuleType("rag.pipeline")
    pipe_mod.RAGPipeline = _StubPipeline
    monkeypatch.setitem(sys.modules, "rag.retriever", retr_mod)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipe_mod)

    service = cs.ChatService(user=_make_user(is_superuser=True))
    frames = await _drain(service.stream_response(conversation_id=1, user_message="hi"))

    assert any('"token"' in f and "hello" in f for f in frames)
    assert any('"token"' in f and "world" in f for f in frames)
    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    assert not any('"error"' in f for f in frames)
