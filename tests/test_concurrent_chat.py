"""
Multi-user concurrency test for the chat / AI streaming path.

This is the automated answer to the review question "can N users use the AI
at the same time?". It drives services.chat_service.ChatService.stream_response
for many users concurrently (via asyncio.gather) and asserts the properties
that actually matter for multi-tenant safety:

  1. Isolation — each user's SSE stream contains ONLY that user's own content;
     no tokens bleed across concurrent streams.
  2. Per-conversation persistence — each conversation records exactly its own
     user message + assistant reply, keyed by conversation_id (no cross-write).
  3. The always-emit-`done` invariant holds for EVERY stream under load (the
     frame the frontend relies on to release its UI lock).
  4. The streams genuinely interleave on the event loop — i.e. this is real
     concurrency, not accidental sequential execution.

The LLM (Ollama) and DB (Postgres) are stubbed exactly the way
tests/test_chat_service.py stubs them, so the test is hermetic and fast: no
network, no database, no model.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from services import chat_service as cs

_DONE_FRAME = f"data: {json.dumps({'done': True})}\n\n"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_user(uid: int):
    """A distinct stub user. stream_response only reads id/role_ids/is_superuser."""
    user = MagicMock()
    user.id = uid
    user.role_ids = [uid]
    user.is_superuser = False
    return user


async def _drain(agen):
    """Collect every frame yielded by an async generator."""
    return [frame async for frame in agen]


def _streamed_tokens(frames: list[str]) -> list[str]:
    """Extract the token strings from the SSE token frames of one stream."""
    out: list[str] = []
    for f in frames:
        if '"token"' in f:
            payload = json.loads(f[len("data: "):].strip())
            if "token" in payload:
                out.append(payload["token"])
    return out


class _SharedConvStore:
    """Stand-in for ConversationRepository, backed by a per-conversation dict.

    One instance is shared across every concurrent stream (mirroring how
    ConversationRepository is monkeypatched elsewhere in the suite). Messages
    are keyed by conversation_id so the test can prove each conversation stayed
    isolated. asyncio is single-threaded so the unguarded dict access is safe;
    the `await asyncio.sleep(0)` calls force the scheduler to interleave the
    competing streams rather than run them back-to-back.
    """

    def __init__(self) -> None:
        self.messages: dict[int, list[tuple[str, str]]] = {}

    async def add_message(self, conversation_id, role, content):
        await asyncio.sleep(0)
        self.messages.setdefault(conversation_id, []).append((role, content))

    async def touch_updated_at(self, conversation_id):
        await asyncio.sleep(0)

    async def get_recent_messages(self, conversation_id, limit=11):
        await asyncio.sleep(0)
        return []

    async def count_messages(self, conversation_id):
        return 2  # != 1: don't spawn the auto-title task, keeping the test hermetic

    async def update_title(self, *a, **k):
        return None


def _session_factory():
    """An async-contextmanager factory yielding a fresh mock session per use."""

    @asynccontextmanager
    async def factory():
        session = MagicMock()
        session.commit = AsyncMock()
        yield session

    return factory


def _install_stub_rag(monkeypatch, execution_log: list[str]):
    """Inject stub rag.retriever / rag.pipeline modules.

    The pipeline echoes the user's OWN message back token-by-token, yielding
    control between tokens so concurrent streams interleave. Each token append
    records the owning user's marker into execution_log, letting the test
    confirm the streams actually ran concurrently.
    """

    class _StubRetriever:
        def __init__(self, *a, **k):
            pass

        async def retrieve(self, *a, **k):
            await asyncio.sleep(0)
            return []

        async def resolve_persona_role(self):
            return None

    class _EchoPipeline:
        def with_retriever(self, _r):
            return self

        async def _stream_from_chunks(self, query, chunks, history, role=None):
            marker = query.split()[0]
            for word in query.split():
                await asyncio.sleep(0)
                execution_log.append(marker)
                yield word + " "

    retr_mod = types.ModuleType("rag.retriever")
    retr_mod.Retriever = _StubRetriever
    pipe_mod = types.ModuleType("rag.pipeline")
    pipe_mod.RAGPipeline = _EchoPipeline
    monkeypatch.setitem(sys.modules, "rag.retriever", retr_mod)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipe_mod)


# ── Test ─────────────────────────────────────────────────────────────────────


async def test_many_users_chat_concurrently_stay_isolated(monkeypatch):
    N = 12
    store = _SharedConvStore()
    execution_log: list[str] = []

    monkeypatch.setattr(cs, "AsyncSessionLocal", _session_factory())
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: store)
    _install_stub_rag(monkeypatch, execution_log)

    conv_ids = [1000 + i for i in range(N)]
    messages = [f"u{i} alpha{i} bravo{i} charlie{i}" for i in range(N)]

    async def run_one(i: int):
        svc = cs.ChatService(user=_make_user(i))
        frames = await _drain(
            svc.stream_response(conversation_id=conv_ids[i], user_message=messages[i])
        )
        return i, frames

    results = await asyncio.gather(*(run_one(i) for i in range(N)))

    for i, frames in results:
        assert frames[-1] == _DONE_FRAME, f"user {i} stream never emitted done"
        assert not any('"error"' in f for f in frames), f"user {i} saw an error"
        streamed = "".join(_streamed_tokens(frames)).strip()
        assert streamed == messages[i], f"user {i} stream leaked: {streamed!r}"

    assert set(store.messages) == set(conv_ids)
    for i in range(N):
        saved = store.messages[conv_ids[i]]
        assert [role for role, _ in saved] == ["user", "assistant"]
        assert saved[0] == ("user", messages[i])
        assert saved[1][1].strip() == messages[i]

    # Sequential execution would make the first N tokens all belong to user 0.
    assert len(set(execution_log[:N])) > 1, "streams did not interleave"


async def test_concurrent_streams_all_emit_done_even_when_some_fail(monkeypatch):
    """Under load, a failure in some users' streams must not stop the others
    from completing — and every stream (failing or not) must still emit `done`.
    """
    N = 8
    store = _SharedConvStore()

    monkeypatch.setattr(cs, "AsyncSessionLocal", _session_factory())
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: store)

    class _StubRetriever:
        def __init__(self, *a, **k):
            pass

        async def retrieve(self, *a, **k):
            await asyncio.sleep(0)
            return []

        async def resolve_persona_role(self):
            return None

    class _FlakyPipeline:
        def with_retriever(self, _r):
            return self

        async def _stream_from_chunks(self, query, chunks, history, role=None):
            await asyncio.sleep(0)
            if query.endswith("BOOM"):
                raise RuntimeError("model exploded mid-stream")
            yield "ok "

    retr_mod = types.ModuleType("rag.retriever")
    retr_mod.Retriever = _StubRetriever
    pipe_mod = types.ModuleType("rag.pipeline")
    pipe_mod.RAGPipeline = _FlakyPipeline
    monkeypatch.setitem(sys.modules, "rag.retriever", retr_mod)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipe_mod)

    async def run_one(i: int):
        msg = f"hello {'BOOM' if i % 2 else 'ok'}"
        svc = cs.ChatService(user=_make_user(i))
        return await _drain(
            svc.stream_response(conversation_id=2000 + i, user_message=msg)
        )

    all_frames = await asyncio.gather(*(run_one(i) for i in range(N)))

    for i, frames in enumerate(all_frames):
        assert frames[-1] == _DONE_FRAME, f"user {i} never emitted done"
        if i % 2:
            assert any('"error"' in f for f in frames[:-1])
        else:
            assert any('"token"' in f for f in frames)
            assert not any('"error"' in f for f in frames)


# ── Heavy, multi-turn concurrency ────────────────────────────────────────────


class _HistoryConvStore:
    """A conversation store that actually threads history per conversation.

    Unlike _SharedConvStore, get_recent_messages returns the stored messages
    (as objects with .role/.content, like the real ORM rows) so the chat
    service builds a genuine multi-turn context window. Shared across every
    concurrent stream; keyed by conversation_id.
    """

    def __init__(self) -> None:
        self.messages: dict[int, list] = {}

    async def add_message(self, conversation_id, role, content):
        await asyncio.sleep(0)
        self.messages.setdefault(conversation_id, []).append(
            types.SimpleNamespace(role=role, content=content)
        )

    async def touch_updated_at(self, conversation_id):
        await asyncio.sleep(0)

    async def get_recent_messages(self, conversation_id, limit=11):
        await asyncio.sleep(0)
        return list(self.messages.get(conversation_id, [])[-limit:])

    async def count_messages(self, conversation_id):
        return len(self.messages.get(conversation_id, []))

    async def update_title(self, *a, **k):
        return None


async def test_heavy_multiturn_concurrency_keeps_users_isolated(monkeypatch, capsys):
    """30 users hold 5-turn conversations *concurrently*.

    Turns within a user are sequential (a real back-and-forth); across users
    everything runs at once. We assert the hard multi-tenant guarantees:

      * every one of the 150 streams completes with `done`, no errors;
      * each turn's reply contains only that user's own content;
      * the per-conversation transcript is correct and correctly ordered;
      * the history window fed to the model for user U never contains another
        user's messages (no context bleed across concurrent conversations).

    The auto-title background task fires on each user's first turn, so this
    also exercises that fire-and-forget path under concurrency.
    """
    N_USERS = 30
    N_TURNS = 5

    store = _HistoryConvStore()
    call_log: list[dict] = []
    history_violations: list[tuple] = []
    concurrency = {"active": 0, "peak": 0}

    monkeypatch.setattr(cs, "AsyncSessionLocal", _session_factory())
    monkeypatch.setattr(cs, "ConversationRepository", lambda _s: store)
    monkeypatch.setattr(cs.title_service, "generate_title", AsyncMock(return_value=""))

    # Track only THIS test's spawned tasks: the module-global _BACKGROUND_TASKS
    # also holds tasks from other tests bound to already-closed event loops.
    spawned: list[asyncio.Task] = []
    _real_spawn = cs.spawn_background_task

    def _tracking_spawn(coro):
        task = _real_spawn(coro)
        spawned.append(task)
        return task

    monkeypatch.setattr(cs, "spawn_background_task", _tracking_spawn)

    class _StubRetriever:
        def __init__(self, *a, **k):
            pass

        async def retrieve(self, *a, **k):
            await asyncio.sleep(0)
            return []

        async def resolve_persona_role(self):
            return None

    class _ContextEchoPipeline:
        def with_retriever(self, _r):
            return self

        async def _stream_from_chunks(self, query, chunks, history, role=None):
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
            try:
                own = query.split()[0]
                foreign = [
                    h["content"].split()[0]
                    for h in history
                    if h["content"].split()[0] != own
                ]
                call_log.append({"own": own, "n_history": len(history)})
                if foreign:
                    history_violations.append((own, foreign))
                for word in query.split():
                    await asyncio.sleep(0)
                    yield word + " "
            finally:
                concurrency["active"] -= 1

    retr_mod = types.ModuleType("rag.retriever")
    retr_mod.Retriever = _StubRetriever
    pipe_mod = types.ModuleType("rag.pipeline")
    pipe_mod.RAGPipeline = _ContextEchoPipeline
    monkeypatch.setitem(sys.modules, "rag.retriever", retr_mod)
    monkeypatch.setitem(sys.modules, "rag.pipeline", pipe_mod)

    conv_ids = [3000 + u for u in range(N_USERS)]

    async def run_user(u: int) -> int:
        svc = cs.ChatService(user=_make_user(u))
        tokens = 0
        for t in range(N_TURNS):
            msg = f"u{u} turn{t} alpha bravo charlie"
            frames = await _drain(
                svc.stream_response(conversation_id=conv_ids[u], user_message=msg)
            )
            assert frames[-1] == _DONE_FRAME, f"u{u} t{t} never finished"
            assert not any('"error"' in f for f in frames), f"u{u} t{t} errored"
            streamed = "".join(_streamed_tokens(frames)).strip()
            assert streamed == msg, f"u{u} t{t} leaked: {streamed!r}"
            tokens += len(_streamed_tokens(frames))
        return tokens

    token_counts = await asyncio.gather(*(run_user(u) for u in range(N_USERS)))

    if spawned:
        await asyncio.gather(*spawned, return_exceptions=True)

    # ── Hard guarantees ──────────────────────────────────────────────────────
    assert not history_violations, f"context bled across users: {history_violations[:5]}"

    assert set(store.messages) == set(conv_ids)
    for u in range(N_USERS):
        msgs = store.messages[conv_ids[u]]
        assert len(msgs) == 2 * N_TURNS, f"u{u} has {len(msgs)} msgs"
        assert [m.role for m in msgs] == ["user", "assistant"] * N_TURNS
        for t in range(N_TURNS):
            assert msgs[2 * t].content == f"u{u} turn{t} alpha bravo charlie"
            assert msgs[2 * t + 1].content.strip() == f"u{u} turn{t} alpha bravo charlie"

    # ── Metrics (surfaced for the report) ────────────────────────────────────
    total_streams = N_USERS * N_TURNS
    messages_persisted = sum(len(store.messages[c]) for c in conv_ids)
    max_history_depth = max(c["n_history"] for c in call_log)
    history_checks = sum(c["n_history"] for c in call_log)

    assert total_streams == len(call_log) == 150
    assert messages_persisted == 2 * total_streams
    assert max_history_depth == 2 * (N_TURNS - 1)  # 8: full prior transcript

    print("\n[HEAVY-CONCURRENCY-METRICS]")
    print(f"users {N_USERS}")
    print(f"turns_per_user {N_TURNS}")
    print(f"total_streams {total_streams}")
    print(f"peak_concurrent_streams {concurrency['peak']}")
    print(f"tokens_streamed {sum(token_counts)}")
    print(f"messages_persisted {messages_persisted}")
    print(f"max_history_depth {max_history_depth}")
    print(f"history_isolation_checks {history_checks}")
    print(f"context_bleed_violations {len(history_violations)}")
    print("[/HEAVY-CONCURRENCY-METRICS]")
