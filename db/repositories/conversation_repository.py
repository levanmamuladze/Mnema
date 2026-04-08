from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Conversation, LearningInsight, LearningProgress, Message
from db.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_user(self, user_id: int, limit: int = 50) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_with_messages(self, conversation_id: int, user_id: int) -> Conversation | None:
        """Ownership-enforced fetch — always pass user_id."""
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .options(selectinload(Conversation.messages))
        )
        return result.scalar_one_or_none()

    async def get_owned(self, conversation_id: int, user_id: int) -> Conversation | None:
        """Fetch without messages, ownership enforced."""
        result = await self.session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def create_for_user(self, user_id: int, title: str | None = None) -> Conversation:
        return await self.create(user_id=user_id, title=title)

    async def update_title(self, conversation_id: int, user_id: int, title: str) -> Conversation | None:
        conv = await self.get_owned(conversation_id, user_id)
        if conv is None:
            return None
        conv.title = title
        self.session.add(conv)
        await self.session.flush()
        await self.session.refresh(conv)
        return conv

    async def delete_conversation(self, conversation_id: int, user_id: int) -> bool:
        conv = await self.get_owned(conversation_id, user_id)
        if conv is None:
            return False
        await self.session.delete(conv)
        await self.session.flush()
        return True

    async def touch_updated_at(self, conversation_id: int) -> None:
        """Update updated_at to now so conversation floats to top of list."""
        from sqlalchemy import update
        from sqlalchemy.sql import func
        await self.session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        await self.session.flush()

    async def add_message(self, conversation_id: int, role: str, content: str) -> Message:
        msg = Message(conversation_id=conversation_id, role=role, content=content)
        self.session.add(msg)
        await self.session.flush()
        await self.session.refresh(msg)
        return msg

    async def count_messages(self, conversation_id: int) -> int:
        from sqlalchemy import func as sqlfunc
        result = await self.session.execute(
            select(sqlfunc.count()).where(Message.conversation_id == conversation_id)
        )
        return result.scalar_one()

    async def get_recent_messages(self, conversation_id: int, limit: int = 10) -> list[Message]:
        result = await self.session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        return list(reversed(messages))


class LearningInsightRepository(BaseRepository[LearningInsight]):
    model = LearningInsight

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_user(self, user_id: int) -> list[LearningInsight]:
        result = await self.session.execute(
            select(LearningInsight)
            .where(LearningInsight.user_id == user_id)
            .order_by(LearningInsight.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_insight(
        self,
        user_id: int,
        topic: str,
        summary: str,
        source_message_id: int | None = None,
    ) -> LearningInsight:
        return await self.create(
            user_id=user_id,
            topic=topic,
            summary=summary,
            source_message_id=source_message_id,
        )


class LearningProgressRepository(BaseRepository[LearningProgress]):
    model = LearningProgress

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_all_for_user(self, user_id: int) -> list[LearningProgress]:
        result = await self.session.execute(
            select(LearningProgress)
            .where(LearningProgress.user_id == user_id)
            .order_by(LearningProgress.topic)
        )
        return list(result.scalars().all())

    async def upsert_topic(self, user_id: int, topic: str, delta: float) -> LearningProgress:
        """
        Increment progress for a topic by delta, capped at 100.
        Creates the row if it doesn't exist.
        """
        result = await self.session.execute(
            select(LearningProgress)
            .where(LearningProgress.user_id == user_id, LearningProgress.topic == topic)
        )
        prog = result.scalar_one_or_none()
        if prog is None:
            prog = LearningProgress(user_id=user_id, topic=topic, progress=min(delta, 100.0))
            self.session.add(prog)
        else:
            prog.progress = min(prog.progress + delta, 100.0)
            self.session.add(prog)
        await self.session.flush()
        await self.session.refresh(prog)
        return prog
