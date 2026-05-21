import json
import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from typing import Any

from db.models import User
from db.repositories.conversation_repository import (
    ConversationRepository,
    LearningProgressRepository,
)
from db.session import AsyncSessionLocal
from services import progress_service

# NOTE: `rag.retriever` and `rag.pipeline` pull in sentence_transformers
# transitively (~1 GB). They're imported lazily inside `stream_response` so
# this module stays importable in unit tests without the full ML stack.

logger = logging.getLogger(__name__)


# ── Background task registry ─────────────────────────────────────────────────
#
# Python only keeps a strong reference to a Task while a caller holds one. A
# bare `asyncio.create_task(coro)` whose return value is discarded can be
# garbage-collected mid-flight — see the warning in the asyncio docs:
#   https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
#
# The canonical fix is a module-level set the task adds itself to on schedule
# and removes itself from on completion. Module-level so its lifetime exceeds
# any single request that spawned the task.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Schedule a coroutine and keep a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


class ChatService:
    def __init__(self, user: User) -> None:
        self.user = user

    async def stream_response(
        self,
        conversation_id: int,
        user_message: str,
    ) -> AsyncGenerator[str, None]:

        # 1. Save user message + load history.
        # If this initial step itself fails we can't recover — yield a single
        # error frame followed by done so the frontend isn't left hanging.
        history_dicts: list[dict] = []
        message_count = 0
        try:
            async with AsyncSessionLocal() as session:
                conv_repo = ConversationRepository(session)
                await conv_repo.add_message(conversation_id, "user", user_message)
                await conv_repo.touch_updated_at(conversation_id)
                history = await conv_repo.get_recent_messages(conversation_id, limit=11)
                history_for_context = history[:-1] if history else []
                history_dicts = [
                    {"role": m.role, "content": m.content} for m in history_for_context
                ]
                message_count = await conv_repo.count_messages(conversation_id)
                await session.commit()
        except Exception:
            logger.exception("Failed to save user message for conversation %s", conversation_id)
            yield f"data: {json.dumps({'error': 'Could not save your message. Please retry.'})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
            return

        # 2-5. RAG retrieval, persist reply, optional auto-title, optional
        # progress tracking. Whatever fails in here, the finally block
        # guarantees a final 'done' frame so the frontend can release its
        # UI lock instead of waiting forever.
        full_response = ""
        try:
            try:
                # Lazy imports — see module docstring.
                from rag.pipeline import RAGPipeline
                from rag.retriever import Retriever

                async with AsyncSessionLocal() as session:
                    retriever = Retriever(
                        session,
                        user_role=self.user.role,
                        is_admin=self.user.is_admin,
                    )
                    rag = RAGPipeline().with_retriever(retriever)
                    chunks = await retriever.retrieve(user_message, top_k=None)

                async for token in rag._stream_from_chunks(
                    user_message, chunks, history_dicts
                ):
                    full_response += token
                    yield f"data: {json.dumps({'token': token})}\n\n"

            except Exception:
                logger.exception(
                    "Error during retrieval/streaming for conversation %s",
                    conversation_id,
                )
                error_msg = "Sorry, I encountered an error processing your request."
                yield f"data: {json.dumps({'error': error_msg})}\n\n"
                full_response = error_msg

            # Save assistant reply (best-effort — if this fails we still
            # want to emit done so the UI unlocks).
            try:
                async with AsyncSessionLocal() as session:
                    conv_repo = ConversationRepository(session)
                    await conv_repo.add_message(
                        conversation_id, "assistant", full_response
                    )
                    await session.commit()
            except Exception:
                logger.exception(
                    "Failed to persist assistant reply for conversation %s",
                    conversation_id,
                )

            # Auto-name after first full exchange (best-effort).
            if message_count == 2:
                try:
                    title = await progress_service.generate_title(
                        user_message, full_response
                    )
                    if title:
                        async with AsyncSessionLocal() as session:
                            conv_repo = ConversationRepository(session)
                            await conv_repo.update_title(
                                conversation_id, self.user.id, title
                            )
                            await session.commit()
                        yield f"data: {json.dumps({'title_update': title})}\n\n"
                except Exception:
                    logger.exception(
                        "Auto-title generation failed for conversation %s",
                        conversation_id,
                    )

            # Track learning progress (non-admins only).
            if not self.user.is_admin and full_response and len(full_response) > 50:
                spawn_background_task(
                    self._update_progress(user_message, full_response)
                )

        finally:
            # The single most important frame in the protocol: tells the
            # frontend to release its UI lock. Must fire on every path.
            yield f"data: {json.dumps({'done': True})}\n\n"

    async def _update_progress(self, user_message: str, assistant_reply: str) -> None:
        topics = await progress_service.extract_topics(user_message, assistant_reply)
        if not topics:
            return
        try:
            async with AsyncSessionLocal() as session:
                repo = LearningProgressRepository(session)
                for item in topics:
                    name = str(item.get("name", "")).strip()
                    delta = float(item.get("delta", 0))
                    if name and 0 < delta <= 15:
                        await repo.upsert_topic(self.user.id, name, delta)
                await session.commit()
        except Exception:
            # Progress tracking is a nice-to-have; a DB hiccup here must
            # never propagate. But log it — previously a bare `pass` hid
            # programming bugs (e.g. attribute errors) too.
            logger.exception(
                "Failed to update learning progress for user %s", self.user.id
            )