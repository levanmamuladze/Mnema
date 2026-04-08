from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DocumentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    filename: str
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at: datetime
    is_indexed: bool
    # Populated from the allowed_roles relationship
    allowed_roles: list[str] = []

    @classmethod
    def from_orm_with_roles(cls, doc) -> "DocumentOut":
        """Build from ORM object, extracting role strings from the relationship."""
        return cls(
            id=doc.id,
            filename=doc.filename,
            original_filename=doc.original_filename,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            uploaded_at=doc.uploaded_at,
            is_indexed=doc.is_indexed,
            allowed_roles=[dr.role for dr in doc.allowed_roles],
        )


class DocumentRolesUpdate(BaseModel):
    roles: list[str]


class FileUploadResult(BaseModel):
    """Result for a single file within a folder upload job."""
    filename: str
    status: str  # "failed" (only failures are tracked)
    error: Optional[str] = None


class FolderJobStatus(BaseModel):
    """Real-time progress snapshot for a folder upload job."""
    job_id: str
    status: str          # "running" | "completed" | "failed"
    total: int           # total files found in the folder
    processed: int       # files attempted so far
    succeeded: int       # files successfully uploaded & indexed
    failed: int          # files that could not be processed
    failed_files: list[FileUploadResult] = []


class LearningInsightOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    topic: str
    summary: str
    created_at: datetime
