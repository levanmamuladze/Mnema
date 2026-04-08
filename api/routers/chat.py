from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_session
from api.schemas.chat import (
    ConversationCreate,
    ConversationOut,
    ConversationUpdate,
    ConversationWithMessages,
    MessageCreate,
    ProgressOut,
    ProgressItem,
)
from api.schemas.document import DocumentOut, LearningInsightOut
from core.exceptions import ForbiddenError, NotFoundError
from db.models import User
from db.repositories.conversation_repository import (
    ConversationRepository,
    LearningInsightRepository,
    LearningProgressRepository,
)
from db.repositories.document_repository import DocumentRepository
from services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["Chat"])


# ── Conversations ─────────────────────────────────────────────────────────────

@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    repo = ConversationRepository(session)
    convs = await repo.get_by_user(current_user.id)
    return [ConversationOut.model_validate(c) for c in convs]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    body: ConversationCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    repo = ConversationRepository(session)
    conv = await repo.create_for_user(current_user.id, title=body.title)
    return ConversationOut.model_validate(conv)


@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessages)
async def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationWithMessages:
    repo = ConversationRepository(session)
    # Ownership enforced in the query itself — no IDOR possible
    conv = await repo.get_with_messages(conversation_id, current_user.id)
    if conv is None:
        raise NotFoundError("Conversation", conversation_id)
    return ConversationWithMessages.model_validate(conv)


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: int,
    body: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    repo = ConversationRepository(session)
    conv = await repo.update_title(conversation_id, current_user.id, body.title)
    if conv is None:
        raise NotFoundError("Conversation", conversation_id)
    return ConversationOut.model_validate(conv)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = ConversationRepository(session)
    deleted = await repo.delete_conversation(conversation_id, current_user.id)
    if not deleted:
        raise NotFoundError("Conversation", conversation_id)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: MessageCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    # Ownership check — short-lived session closes before streaming starts
    conv_repo = ConversationRepository(session)
    conv = await conv_repo.get_owned(conversation_id, current_user.id)
    if conv is None:
        raise NotFoundError("Conversation", conversation_id)

    service = ChatService(user=current_user)
    return StreamingResponse(
        service.stream_response(
            conversation_id=conversation_id,
            user_message=body.content,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Progress ──────────────────────────────────────────────────────────────────

@router.get("/progress", response_model=ProgressOut)
async def get_progress(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ProgressOut:
    repo = LearningProgressRepository(session)
    items = await repo.get_all_for_user(current_user.id)
    progress_items = [ProgressItem.model_validate(i) for i in items]
    overall = (sum(i.progress for i in progress_items) / len(progress_items)) if progress_items else 0.0
    return ProgressOut(items=progress_items, overall=round(overall, 1))


# ── Documents + Insights ──────────────────────────────────────────────────────

@router.get("/documents", response_model=list[DocumentOut])
async def list_my_documents(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    repo = DocumentRepository(session)
    if current_user.is_admin:
        docs = await repo.get_all_documents()
    else:
        docs = await repo.get_documents_for_role(current_user.role)
    return [DocumentOut.from_orm_with_roles(d) for d in docs]


@router.get("/learning-insights", response_model=list[LearningInsightOut])
async def get_learning_insights(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[LearningInsightOut]:
    repo = LearningInsightRepository(session)
    insights = await repo.get_by_user(current_user.id)
    return [LearningInsightOut.model_validate(i) for i in insights]
