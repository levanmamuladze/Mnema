import json
import asyncio
import logging
from collections.abc import AsyncGenerator, Coroutine
from typing import Any

from db.models import User
from db.repositories.conversation_repository import ConversationRepository
from db.session import AsyncSessionLocal
from services import title_service

# rag.retriever/pipeline pull in sentence_transformers (~1 GB); imported lazily
# inside stream_response so this module stays unit-test importable.

logger = logging.getLogger(__name__)


# ── Background task registry ─────────────────────────────────────────────────
# A module-level set holds a strong ref so fire-and-forget tasks aren't
# garbage-collected mid-flight (asyncio.create_task docs).
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Schedule a coroutine and keep a strong reference until it finishes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _persona_to_dict(role: Any | None) -> dict | None:
    """Materialise a Role row's four persona slots into a plain dict.

    Done while the DB session is still open so the prompt builder never
    touches a detached/expired ORM instance after the session closes.
    """
    if role is None:
        return None
    return {
        "persona_name": role.persona_name,
        "domain_label": role.domain_label,
        "scope_description": role.scope_description,
        "general_knowledge_scope": role.general_knowledge_scope,
    }


class ChatService:
    def __init__(self, user: User) -> None:
        self.user = user

    async def stream_response(
        self,
        conversation_id: int,
        user_message: str,
    ) -> AsyncGenerator[str, None]:

        # 1. Save user message + load history. On failure, emit one error
        # frame + done so the frontend isn't left hanging.
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

        # Retrieve, stream, persist reply, optional auto-title. The finally
        # block guarantees a final 'done' frame on every path.
        full_response = ""
        try:
            try:
                from rag.pipeline import RAGPipeline
                from rag.retriever import Retriever

                logger.info(
                    "chat[conv=%s user=%s roles=%s superuser=%s]: retrieving",
                    conversation_id, self.user.id,
                    self.user.role_ids, self.user.is_superuser,
                )
                async with AsyncSessionLocal() as session:
                    retriever = Retriever(
                        session,
                        user_role_ids=self.user.role_ids,
                        is_superuser=self.user.is_superuser,
                    )
                    rag = RAGPipeline().with_retriever(retriever)
                    chunks = await retriever.retrieve(user_message, top_k=None)
                    # Resolve persona inside the session block — doing it after
                    # caused a closed-session query for single-role users.
                    persona_slots = _persona_to_dict(
                        await retriever.resolve_persona_role()
                    )

                logger.info(
                    "chat[conv=%s]: retrieved %d chunks, streaming from LLM",
                    conversation_id, len(chunks),
                )
                async for token in rag._stream_from_chunks(
                    user_message, chunks, history_dicts, role=persona_slots
                ):
                    full_response += token
                    yield f"data: {json.dumps({'token': token})}\n\n"

                logger.info(
                    "chat[conv=%s]: stream complete (%d chars)",
                    conversation_id, len(full_response),
                )

            except Exception:
                logger.exception(
                    "Error during retrieval/streaming for conversation %s",
                    conversation_id,
                )
                error_msg = "Sorry, I encountered an error processing your request."
                yield f"data: {json.dumps({'error': error_msg})}\n\n"
                full_response = error_msg

            # Save assistant reply (best-effort).
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

            # Auto-title after the first turn (message_count == 1: only the
            # user message is saved at sampling time). Spawned as a background
            # task so the slow Ollama title call doesn't block the 'done' frame.
            if message_count == 1 and full_response:
                spawn_background_task(
                    self._update_title(conversation_id, user_message, full_response)
                )

        finally:
            # Always emit done so the frontend releases its UI lock.
            yield f"data: {json.dumps({'done': True})}\n\n"

    async def _update_title(
        self,
        conversation_id: int,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Generate an auto-title and persist it. Never raises."""
        try:
            title = await title_service.generate_title(user_message, assistant_reply)
            if not title:
                return
            async with AsyncSessionLocal() as session:
                conv_repo = ConversationRepository(session)
                await conv_repo.update_title(conversation_id, self.user.id, title)
                await session.commit()
        except Exception:
            logger.exception(
                "Auto-title generation failed for conversation %s", conversation_id
            )