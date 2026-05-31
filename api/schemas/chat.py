from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ConversationOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)


class MessageOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    role: str
    content: str
    created_at: datetime


class ConversationWithMessages(ConversationOut):
    messages: list[MessageOut] = []
