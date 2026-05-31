from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from api.schemas.role import RoleSummary


class DocumentOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    filename: str
    original_filename: str
    file_size: int
    mime_type: str
    uploaded_at: datetime
    is_indexed: bool
    # Projected as RoleSummary so the frontend gets display_name without a refetch.
    allowed_roles: list[RoleSummary] = Field(default_factory=list)

    @classmethod
    def from_orm_with_roles(cls, doc) -> "DocumentOut":
        """Build from a SQLAlchemy Document with role_assignments + role eager-loaded."""
        roles = []
        for dr in doc.role_assignments:
            if dr.role is None:
                # Defensive: skip orphaned assignments rather than 500.
                continue
            roles.append(RoleSummary(
                id=dr.role.id,
                name=dr.role.name,
                display_name=dr.role.display_name,
            ))
        return cls(
            id=doc.id,
            filename=doc.filename,
            original_filename=doc.original_filename,
            file_size=doc.file_size,
            mime_type=doc.mime_type,
            uploaded_at=doc.uploaded_at,
            is_indexed=doc.is_indexed,
            allowed_roles=roles,
        )


class DocumentRolesUpdate(BaseModel):
    """Replace a document's role assignments with the given role IDs."""
    role_ids: list[int] = Field(..., min_length=1)

    @field_validator("role_ids")
    @classmethod
    def _dedup(cls, v: list[int]) -> list[int]:
        seen: set[int] = set()
        out: list[int] = []
        for rid in v:
            if rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
        return out
