from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# Same regex the repository uses; checked here so the 422 skips a DB round-trip.
import re
_ROLE_NAME_RE = re.compile(r"^[a-z0-9_-]{2,50}$")


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50)
    display_name: str = Field(..., min_length=1, max_length=100)
    persona_name: str = Field(..., min_length=1, max_length=50)
    domain_label: str = Field(..., min_length=1, max_length=50)
    scope_description: str = Field(..., min_length=1, max_length=2000)
    general_knowledge_scope: str = Field(..., min_length=1, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name_pattern(cls, v: str) -> str:
        v = v.strip().lower()
        if not _ROLE_NAME_RE.match(v):
            raise ValueError(
                "Role name must be 2–50 chars of lowercase letters, "
                "digits, underscore, or hyphen."
            )
        return v


class RoleUpdate(BaseModel):
    # name is not updatable — it's the stable identifier used by queries and audit logs.
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    persona_name: Optional[str] = Field(None, min_length=1, max_length=50)
    domain_label: Optional[str] = Field(None, min_length=1, max_length=50)
    scope_description: Optional[str] = Field(None, min_length=1, max_length=2000)
    general_knowledge_scope: Optional[str] = Field(None, min_length=1, max_length=2000)


class RoleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    display_name: str
    persona_name: str
    domain_label: str
    scope_description: str
    general_knowledge_scope: str
    created_at: datetime
    created_by: Optional[int] = None


class RoleSummary(BaseModel):
    """Compact role projection embedded in user/document responses."""
    model_config = {"from_attributes": True}

    id: int
    name: str
    display_name: str


class RoleMembershipAssign(BaseModel):
    """One entry in a user-create/update payload's role assignments."""
    role_id: int
    is_role_admin: bool = False
