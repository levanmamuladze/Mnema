import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_session, require_admin
from api.schemas.document import DocumentOut, DocumentRolesUpdate, FileUploadResult, FolderJobStatus
from api.schemas.user import UserCreate, UserOut, UserRoleUpdate
from core.config import settings
from core.exceptions import FileTooLargeError, NotFoundError, ValidationError
from db.models import User
from db.repositories.document_repository import DocumentRepository
from db.repositories.document_role_repository import DocumentRoleRepository
from db.repositories.user_repository import UserRepository
from services.folder_upload_service import create_job, get_job, run_folder_job
from services.ingestion_service import IngestionService

router = APIRouter(prefix="/admin", tags=["Admin"])

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
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
    ".pdf", ".txt", ".md", ".docx", ".py", ".js", ".ts", ".tsx", ".jsx", ".java",
    ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".kt", ".swift",
    ".scala", ".sql", ".sh", ".bash", ".zsh", ".yaml", ".yml", ".json", ".xml", ".html",
    ".css", ".toml", ".ini", ".cfg",
}


def _is_allowed_upload(file: UploadFile) -> bool:
    suffix = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    return content_type in ALLOWED_MIME_TYPES or suffix in ALLOWED_EXTENSIONS


@router.post("/users/create", response_model=UserOut)
async def create_user(
    body: UserCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    repo = UserRepository(session)
    user = await repo.create_user(
        username=body.username,
        email=body.email,
        plain_password=body.password,
        is_admin=body.is_admin,
        role=body.role,
    )
    return UserOut.model_validate(user)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    repo = UserRepository(session)
    users = await repo.list_all()
    return [UserOut.model_validate(u) for u in users]


@router.put("/users/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: int,
    body: UserRoleUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    repo = UserRepository(session)
    user = await repo.update_role(user_id, body.role)
    return UserOut.model_validate(user)


@router.post("/documents/upload", response_model=DocumentOut)
async def upload_document(
    file: UploadFile = File(...),
    roles: str = Form(..., description="Comma-separated roles: hr, developer, or hr,developer"),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise FileTooLargeError(settings.MAX_UPLOAD_SIZE_MB)

    if not _is_allowed_upload(file):
        raise ValidationError(
            f"Unsupported file type: {file.content_type or 'unknown'}. "
            "Upload a PDF, DOCX, text document, or supported source-code file."
        )

    role_list = [r.strip().lower() for r in roles.split(",") if r.strip()]
    if not role_list:
        raise ValidationError("At least one role must be specified.")

    upload_dir = Path(settings.UPLOAD_DIR) / str(admin.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Strip any relative path the browser may include (e.g. "folder/file.txt"
    # sent by webkitdirectory inputs) so the filename never contains a separator.
    safe_filename = Path(file.filename or "unknown").name
    unique_filename = f"{uuid.uuid4().hex}_{safe_filename}"
    file_path = upload_dir / unique_filename

    with open(file_path, "wb") as f:
        f.write(content)

    doc_repo = DocumentRepository(session)
    doc = await doc_repo.create_document(
        filename=unique_filename,
        original_filename=safe_filename,
        file_path=str(file_path),
        file_size=len(content),
        mime_type=file.content_type or "application/octet-stream",
        uploaded_by_admin=admin.id,
    )

    role_repo = DocumentRoleRepository(session)
    await role_repo.set_roles(doc.id, role_list)

    ingestion_service = IngestionService(session)
    await ingestion_service.index_document(doc)

    doc = await doc_repo.get_by_id_or_raise(doc.id)
    await session.refresh(doc, ["allowed_roles"])
    return DocumentOut.from_orm_with_roles(doc)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    repo = DocumentRepository(session)
    docs = await repo.get_all_documents()
    return [DocumentOut.from_orm_with_roles(d) for d in docs]


@router.put("/documents/{document_id}/roles", response_model=DocumentOut)
async def update_document_roles(
    document_id: int,
    body: DocumentRolesUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    doc_repo = DocumentRepository(session)
    doc = await doc_repo.get_by_id_or_raise(document_id)

    role_repo = DocumentRoleRepository(session)
    await role_repo.set_roles(document_id, body.roles)

    await session.refresh(doc, ["allowed_roles"])
    return DocumentOut.from_orm_with_roles(doc)


@router.post("/documents/upload-folder", response_model=FolderJobStatus, status_code=202)
async def upload_folder(
    folder_path: str = Form(..., description="Absolute local path, UNC/SMB path, or OneDrive URL"),
    roles: str = Form(..., description="Comma-separated roles: hr, developer, or hr,developer"),
    username: Optional[str] = Form(None, description="Username for network share authentication"),
    password: Optional[str] = Form(None, description="Password or OneDrive access token"),
    onedrive_path: Optional[str] = Form(None, description="Manual OneDrive folder path override, e.g. Documents/inventyou"),
    admin: User = Depends(require_admin),
) -> FolderJobStatus:
    """
    Start a background job that scans *folder_path* and uploads + indexes
    every valid file inside it.  Returns immediately with a job_id.

    Poll GET /admin/documents/upload-folder/{job_id} for live progress.

    Supported path types:
      - Local absolute path:          /data/docs  or  C:\\Docs
      - UNC / SMB network share:      \\\\server\\share\\folder
        (requires username + password)
    """
    role_list = [r.strip().lower() for r in roles.split(",") if r.strip()]
    if not role_list:
        raise ValidationError("At least one role must be specified.")

    job_id = create_job()
    asyncio.create_task(
        run_folder_job(
            job_id=job_id,
            folder_path=folder_path,
            role_list=role_list,
            admin_id=admin.id,
            username=username or None,
            password=password or None,
            onedrive_path=onedrive_path or None,
        )
    )

    job = get_job(job_id)
    return FolderJobStatus(job_id=job_id, **job)


@router.get("/documents/upload-folder/{job_id}", response_model=FolderJobStatus)
async def get_folder_upload_status(
    job_id: str,
    admin: User = Depends(require_admin),
) -> FolderJobStatus:
    """Return the current progress snapshot for a folder upload job."""
    job = get_job(job_id)
    if job is None:
        raise NotFoundError("FolderUploadJob", job_id)
    return FolderJobStatus(
        job_id=job_id,
        status=job["status"],
        total=job["total"],
        processed=job["processed"],
        succeeded=job["succeeded"],
        failed=job["failed"],
        failed_files=[FileUploadResult(**f) for f in job["failed_files"]],
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = DocumentRepository(session)
    doc = await repo.get_by_id_or_raise(document_id)

    if os.path.exists(doc.file_path):
        os.remove(doc.file_path)

    await repo.delete(doc)
