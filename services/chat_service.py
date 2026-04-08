import json
import asyncio
import logging
from collections.abc import AsyncGenerator

from db.models import User

logger = logging.getLogger(__name__)
from db.repositories.conversation_repository import (
    ConversationRepository,
    LearningProgressRepository,
)
from db.session import AsyncSessionLocal
from rag.pipeline import RAGPipeline
from rag.retriever import Retriever
from services import progress_service


class ChatService:
    def __init__(self, user: User) -> None:
        self.user = user

    async def stream_response(
        self,
        conversation_id: int,
        user_message: str,
    ) -> AsyncGenerator[str, None]:

        # 1. Save user message + load history
        history_dicts: list[dict] = []
        message_count = 0
        async with AsyncSessionLocal() as session:
            conv_repo = ConversationRepository(session)
            await conv_repo.add_message(conversation_id, "user", user_message)
            await conv_repo.touch_updated_at(conversation_id)
            history = await conv_repo.get_recent_messages(conversation_id, limit=11)
            history_for_context = history[:-1] if history else []
            history_dicts = [{"role": m.role, "content": m.content} for m in history_for_context]
            message_count = await conv_repo.count_messages(conversation_id)
            await session.commit()

        # 2. RAG retrieval — admins bypass role filter
        full_response = ""
        try:
            async with AsyncSessionLocal() as session:
                retriever = Retriever(
                    session,
                    user_role=self.user.role,
                    is_admin=self.user.is_admin,
                )
                rag = RAGPipeline().with_retriever(retriever)
                chunks = await retriever.retrieve(user_message, top_k=None)

            async for token in rag._stream_from_chunks(user_message, chunks, history_dicts):
                full_response += token
                yield f"data: {json.dumps({'token': token})}\n\n"

        except Exception:
            logger.exception("Error in stream_response for conversation %s", conversation_id)
            error_msg = "Sorry, I encountered an error processing your request."
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
            full_response = error_msg

        # 3. Save assistant reply
        async with AsyncSessionLocal() as session:
            conv_repo = ConversationRepository(session)
            await conv_repo.add_message(conversation_id, "assistant", full_response)
            await session.commit()

        # 4. Auto-name after first full exchange
        if message_count == 2:
            title = await progress_service.generate_title(user_message, full_response)
            if title:
                async with AsyncSessionLocal() as session:
                    conv_repo = ConversationRepository(session)
                    await conv_repo.update_title(conversation_id, self.user.id, title)
                    await session.commit()
                yield f"data: {json.dumps({'title_update': title})}\n\n"

        # 5. Track learning progress (non-admins only — admins don't need tracking)
        if not self.user.is_admin and full_response and len(full_response) > 50:
            asyncio.create_task(
                self._update_progress(user_message, full_response)
            )

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
            pass