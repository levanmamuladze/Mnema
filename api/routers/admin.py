import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_superuser, require_uploader
from core.limiter import limiter
from api.schemas.document import (
    DocumentOut,
    DocumentRolesUpdate,
)
from api.schemas.role import (
    RoleCreate,
    RoleMembershipAssign,
    RoleOut,
    RoleUpdate,
)
from api.schemas.user import UserCreate, UserOut, UserRolesUpdate
from core.config import settings
from core.exceptions import FileTooLargeError, ForbiddenError, NotFoundError, ValidationError
from db.models import User
from db.repositories.document_repository import DocumentRepository
from db.repositories.document_role_repository import DocumentRoleRepository
from db.repositories.role_repository import RoleRepository
from db.repositories.user_repository import UserRepository
from services.ingestion_service import IngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/csv",
    "application/json",
    "application/xml",
    "text/x-python",
    "application/javascript",
    "text/javascript",
    "text/x-java-source",
    "text/x-c",
    "text/x-c++src",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ALLOWED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".java", ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp",
    ".kt", ".swift", ".scala", ".sql", ".sh", ".bash", ".zsh", ".yaml", ".yml",
    ".json", ".jsonl", ".ndjson", ".xml", ".html", ".css", ".toml", ".ini", ".cfg",
    ".log", ".rst", ".tex", ".env",
}


def _is_allowed_upload(file: UploadFile) -> bool:
    suffix = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    return content_type in ALLOWED_MIME_TYPES or suffix in ALLOWED_EXTENSIONS


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an upload into memory without ever buffering more than max_bytes.

    The previous `await file.read()` slurped the entire body before the
    size check, so a multi-GB upload could exhaust memory before being
    rejected. Reading in bounded 1 MB blocks caps peak memory at the limit
    and rejects oversized files as soon as they cross it.
    """
    blocks: list[bytes] = []
    total = 0
    while True:
        block = await file.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise FileTooLargeError(settings.MAX_UPLOAD_SIZE_MB)
        blocks.append(block)
    return b"".join(blocks)


def _check_actor_can_assign_roles(actor: User, role_ids: list[int]) -> None:
    """Raise 403 unless the actor can assign every role in `role_ids`.

    Superusers can assign any role. Role-admins can only assign roles
    they themselves administer — assigning a doc to a role outside
    that set would let them broaden access for teams they don't own.
    """
    if actor.is_superuser:
        return
    not_allowed = [rid for rid in role_ids if rid not in actor.admin_role_ids]
    if not_allowed:
        raise ForbiddenError(
            f"You can only assign documents to roles you administer. "
            f"Disallowed: {not_allowed}"
        )


def _check_actor_can_manage_doc(actor: User, current_role_ids: list[int]) -> None:
    """Raise 403 unless the actor may edit/delete a document.

    A role-admin can manage a document only if EVERY role currently
    on the doc is in their admin set. This blocks a role-admin from
    deleting (or yanking access for) a doc shared with other teams
    — only a superuser can do that.
    """
    if actor.is_superuser:
        return
    not_owned = [rid for rid in current_role_ids if rid not in actor.admin_role_ids]
    if not_owned:
        raise ForbiddenError(
            "This document is shared with roles you do not administer. "
            "Ask a superuser to manage it."
        )


def _parse_role_ids_form(raw: str) -> list[int]:
    """
    Folder/upload form posts send role IDs as a comma-separated string.
    Parse and validate the shape (positive integers); existence is
    re-checked by the repository so the error message is consistent
    with the JSON paths.
    """
    out: list[int] = []
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            raise ValidationError(f"Invalid role id: {tok!r}")
        if n <= 0:
            raise ValidationError(f"Invalid role id: {tok!r}")
        out.append(n)
    if not out:
        raise ValidationError("At least one role id must be specified.")
    return out


# ── Users ────────────────────────────────────────────────────────────

@router.post("/users/create", response_model=UserOut)
async def create_user(
    body: UserCreate,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    repo = UserRepository(session)
    role_assignments = [
        (m.role_id, m.is_role_admin) for m in body.role_assignments
    ]
    user = await repo.create_user(
        username=body.username,
        email=body.email,
        plain_password=body.password,
        is_superuser=body.is_superuser,
        role_assignments=role_assignments,
    )
    logger.info(
        "admin_audit create_user actor_id=%s new_user_id=%s username=%r "
        "is_superuser=%s roles=%s",
        actor.id, user.id, user.username, user.is_superuser,
        [(m.role_id, m.is_role_admin) for m in user.role_memberships],
    )
    return UserOut.from_orm_user(user)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    repo = UserRepository(session)
    users = await repo.list_all()
    return [UserOut.from_orm_user(u) for u in users]


@router.put("/users/{user_id}/roles", response_model=UserOut)
async def set_user_roles(
    user_id: int,
    body: UserRolesUpdate,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    repo = UserRepository(session)
    role_assignments = [
        (m.role_id, m.is_role_admin) for m in body.role_assignments
    ]
    user = await repo.set_role_memberships(user_id, role_assignments)
    logger.info(
        "admin_audit set_user_roles actor_id=%s target_user_id=%s "
        "new_roles=%s",
        actor.id, user_id,
        [(m.role_id, m.is_role_admin) for m in user.role_memberships],
    )
    return UserOut.from_orm_user(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Remove a user and everything tied to their account.

    Safeguards:
      - You cannot delete your own account.
      - You cannot delete the last remaining superuser.

    Documents the user uploaded are reassigned to the deleting actor so
    the documents.uploaded_by_admin FK does not block the operation.
    """
    if user_id == actor.id:
        raise ValidationError("You cannot delete your own account.")

    user_repo = UserRepository(session)
    target = await user_repo.get_by_id(user_id)
    if target is None:
        raise NotFoundError("User", user_id)

    if target.is_superuser:
        count = await user_repo.count_superusers()
        if count <= 1:
            raise ValidationError(
                "Cannot delete the last remaining superuser. "
                "Create another superuser first, then delete this one."
            )

    doc_repo = DocumentRepository(session)
    reassigned = await doc_repo.reassign_uploader(
        from_user_id=user_id, to_user_id=actor.id
    )

    await user_repo.delete(target)
    logger.info(
        "admin_audit delete_user actor_id=%s deleted_user_id=%s "
        "deleted_username=%r was_superuser=%s docs_reassigned=%d",
        actor.id, user_id, target.username, target.is_superuser, reassigned,
    )


# ── Roles ────────────────────────────────────────────────────────────

@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    actor: User = Depends(require_uploader),
    session: AsyncSession = Depends(get_session),
) -> list[RoleOut]:
    """List roles visible to the caller.

    Superusers see every role (used for the full admin UI). Role-admins
    see only the roles they administer — that's the set the document
    upload form must offer them, and it's the only role detail their
    UI needs.
    """
    repo = RoleRepository(session)
    roles = await repo.list_all()
    if actor.is_superuser:
        return [RoleOut.model_validate(r) for r in roles]
    admin_ids = set(actor.admin_role_ids)
    return [RoleOut.model_validate(r) for r in roles if r.id in admin_ids]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(
    body: RoleCreate,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> RoleOut:
    repo = RoleRepository(session)
    role = await repo.create_role(
        name=body.name,
        display_name=body.display_name,
        persona_name=body.persona_name,
        domain_label=body.domain_label,
        scope_description=body.scope_description,
        general_knowledge_scope=body.general_knowledge_scope,
        created_by=actor.id,
    )
    logger.info(
        "admin_audit create_role actor_id=%s role_id=%s name=%r",
        actor.id, role.id, role.name,
    )
    return RoleOut.model_validate(role)


@router.put("/roles/{role_id}", response_model=RoleOut)
async def update_role(
    role_id: int,
    body: RoleUpdate,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> RoleOut:
    repo = RoleRepository(session)
    role = await repo.update_role(
        role_id,
        display_name=body.display_name,
        persona_name=body.persona_name,
        domain_label=body.domain_label,
        scope_description=body.scope_description,
        general_knowledge_scope=body.general_knowledge_scope,
    )
    logger.info(
        "admin_audit update_role actor_id=%s role_id=%s",
        actor.id, role.id,
    )
    return RoleOut.model_validate(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Delete a role. Cascades drop membership rows and document
    assignments. Documents that had ONLY this role become unreachable
    to non-superusers afterwards — the UI should warn before invoking.
    """
    repo = RoleRepository(session)
    role = await repo.get_by_id_or_raise(role_id)
    name = role.name
    await repo.delete_role(role_id)
    logger.info(
        "admin_audit delete_role actor_id=%s role_id=%s name=%r",
        actor.id, role_id, name,
    )


@router.put("/roles/{role_id}/members/{user_id}", response_model=UserOut)
async def set_role_member(
    role_id: int,
    user_id: int,
    body: RoleMembershipAssign,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Add a user to a role (or update their admin flag inside it)."""
    if body.role_id != role_id:
        raise ValidationError("role_id in path and body must match.")
    role_repo = RoleRepository(session)
    await role_repo.get_by_id_or_raise(role_id)

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)
    if user is None:
        raise NotFoundError("User", user_id)

    await role_repo.add_member(user_id, role_id, body.is_role_admin)
    refreshed = await user_repo.get_by_id(user_id)
    logger.info(
        "admin_audit set_role_member actor_id=%s role_id=%s "
        "target_user_id=%s is_role_admin=%s",
        actor.id, role_id, user_id, body.is_role_admin,
    )
    return UserOut.from_orm_user(refreshed)


@router.delete("/roles/{role_id}/members/{user_id}", status_code=204)
async def remove_role_member(
    role_id: int,
    user_id: int,
    actor: User = Depends(require_superuser),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = RoleRepository(session)
    removed = await repo.remove_member(user_id, role_id)
    if not removed:
        raise NotFoundError("Membership", f"user={user_id},role={role_id}")
    logger.info(
        "admin_audit remove_role_member actor_id=%s role_id=%s target_user_id=%s",
        actor.id, role_id, user_id,
    )


# ── Documents ────────────────────────────────────────────────────────

@router.post("/documents/upload", response_model=DocumentOut)
@limiter.limit("20/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    role_ids: str = Form(
        ..., description="Comma-separated role IDs the document grants access to"
    ),
    actor: User = Depends(require_uploader),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    content = await _read_upload_capped(file, settings.max_upload_size_bytes)

    if not _is_allowed_upload(file):
        raise ValidationError(
            f"Unsupported file type: {file.content_type or 'unknown'}. "
            "Upload a PDF, DOCX, text document, or supported source-code file."
        )

    parsed_role_ids = _parse_role_ids_form(role_ids)
    _check_actor_can_assign_roles(actor, parsed_role_ids)

    upload_dir = Path(settings.UPLOAD_DIR) / str(actor.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = Path(file.filename or "unknown").name
    unique_filename = f"{uuid.uuid4().hex}_{safe_filename}"
    file_path = upload_dir / unique_filename

    with open(file_path, "wb") as f:
        f.write(content)

    try:
        doc_repo = DocumentRepository(session)
        doc = await doc_repo.create_document(
            filename=unique_filename,
            original_filename=safe_filename,
            file_path=str(file_path),
            file_size=len(content),
            mime_type=file.content_type or "application/octet-stream",
            uploaded_by_admin=actor.id,
        )

        role_repo = DocumentRoleRepository(session)
        await role_repo.set_role_ids(doc.id, parsed_role_ids)

        ingestion_service = IngestionService(session)
        try:
            await ingestion_service.index_document(doc)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

        # Re-fetch with role rows eager-loaded for the response.
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from db.models import Document, DocumentRole
        result = await session.execute(
            select(Document)
            .where(Document.id == doc.id)
            .options(
                selectinload(Document.role_assignments).selectinload(DocumentRole.role)
            )
        )
        doc = result.scalar_one()
    except Exception:
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to clean up orphaned upload %s", file_path)
        raise

    logger.info(
        "admin_audit upload_document actor_id=%s doc_id=%s filename=%r "
        "role_ids=%s size=%d",
        actor.id, doc.id, safe_filename, parsed_role_ids, len(content),
    )
    return DocumentOut.from_orm_with_roles(doc)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    actor: User = Depends(require_uploader),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    repo = DocumentRepository(session)
    if actor.is_superuser:
        docs = await repo.get_all_documents()
    else:
        docs = await repo.get_documents_for_admin_roles(actor.admin_role_ids)
    return [DocumentOut.from_orm_with_roles(d) for d in docs]


@router.put("/documents/{document_id}/roles", response_model=DocumentOut)
async def update_document_roles(
    document_id: int,
    body: DocumentRolesUpdate,
    actor: User = Depends(require_uploader),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    doc_repo = DocumentRepository(session)
    doc = await doc_repo.get_by_id_or_raise(document_id)

    role_repo = DocumentRoleRepository(session)
    # Role-admin may only manage docs whose roles are all in their admin set,
    # and assign only roles they administer (both raise 403).
    doc_role_repo = DocumentRoleRepository(session)
    existing_role_ids = await doc_role_repo.get_role_ids_for_document(document_id)
    _check_actor_can_manage_doc(actor, existing_role_ids)
    _check_actor_can_assign_roles(actor, body.role_ids)

    await role_repo.set_role_ids(document_id, body.role_ids)

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from db.models import Document, DocumentRole
    result = await session.execute(
        select(Document)
        .where(Document.id == doc.id)
        .options(
            selectinload(Document.role_assignments).selectinload(DocumentRole.role)
        )
    )
    doc = result.scalar_one()
    logger.info(
        "admin_audit set_document_roles actor_id=%s doc_id=%s role_ids=%s",
        actor.id, document_id, body.role_ids,
    )
    return DocumentOut.from_orm_with_roles(doc)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    actor: User = Depends(require_uploader),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = DocumentRepository(session)
    doc = await repo.get_by_id_or_raise(document_id)

    role_repo = DocumentRoleRepository(session)
    existing_role_ids = await role_repo.get_role_ids_for_document(document_id)
    _check_actor_can_manage_doc(actor, existing_role_ids)

    original_filename = doc.original_filename
    file_path = doc.file_path

    if os.path.exists(file_path):
        os.remove(file_path)

    await repo.delete(doc)
    logger.info(
        "admin_audit delete_document actor_id=%s doc_id=%s filename=%r",
        actor.id, document_id, original_filename,
    )
