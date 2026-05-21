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
        await asyncio.sleep(0)  # yield
        finished.set()

    # Schedule but don't hold the returned Task ourselves.
    cs.spawn_background_task(work())

    # While running, the task must be in the registry.
    await started.wait()
    assert len(cs._BACKGROUND_TASKS) >= 1

    # After completion, the discard callback must remove it.
    await finished.wait()
    # Give the loop one tick so the done_callback fires.
    await asyncio.sleep(0)
    # The task we just spawned is no longer in the set (other tests may have
    # left tasks in flight, so we check membership not size==0).
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


def _make_user(*, is_admin: bool = False):
    user = MagicMock()
    user.id = 1
    user.role = "developer"
    user.is_admin = is_admin
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

    # Last frame must be the done sentinel.
    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    # And there must be a preceding error frame so the user sees something useful.
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

    # Make the lazy rag.retriever import explode.
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

    service = cs.ChatService(user=_make_user(is_admin=True))  # admin: skip progress
    frames = await _drain(service.stream_response(conversation_id=1, user_message="hi"))

    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    # Error frame should appear before done.
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

    # A retriever that returns no chunks and a pipeline that yields two tokens.
    import sys
    import types

    class _StubRetriever:
        def __init__(self, *a, **k):
            pass
        async def retrieve(self, *a, **k):
            return []

    class _StubPipeline:
        def with_retriever(self, _r):
            return self
        async def _stream_from_chunks(self, *a, **k):
            for tok in ("hello", " world"):
                yield tok

    retr_mod = types.ModuleType("rag.retriever")
    retr_mod.Retriever = _StubRetriever
    pipe_mod = types.ModuleType("rag.pipeline")
    pipe_mod.RAGPipeline = _StubPipeline
    monkeypatch.setitem(sys.modules, "rag.retriever", retr_mod)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipe_mod)

    service = cs.ChatService(user=_make_user(is_admin=True))
    frames = await _drain(service.stream_response(conversation_id=1, user_message="hi"))

    # Token frames first, then done.
    assert any('"token"' in f and "hello" in f for f in frames)
    assert any('"token"' in f and "world" in f for f in frames)
    assert frames[-1] == f"data: {json.dumps({'done': True})}\n\n"
    # No error frame on the happy path.
    assert not any('"error"' in f for f in frames)
