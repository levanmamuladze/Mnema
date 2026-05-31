from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from api.schemas.role import RoleMembershipAssign


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    is_superuser: bool = False
    # (role_id, is_role_admin) grants; an empty list = no retrieval access
    # until the user is added to a role.
    role_assignments: list[RoleMembershipAssign] = Field(default_factory=list)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        return v


class UserRolesUpdate(BaseModel):
    """Replace a user's full set of role memberships in one call."""
    role_assignments: list[RoleMembershipAssign] = Field(default_factory=list)


class MembershipOut(BaseModel):
    """One row of a user's role memberships, projected to the API."""
    model_config = {"from_attributes": True}

    role_id: int
    role_name: str
    role_display_name: str
    is_role_admin: bool


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    username: str
    email: str
    is_superuser: bool
    is_active: bool
    created_at: datetime
    memberships: list[MembershipOut] = Field(default_factory=list)

    @classmethod
    def from_orm_user(cls, user) -> "UserOut":
        """Build from a SQLAlchemy User with role_memberships eager-loaded.

        Done explicitly (rather than via from_attributes alone) because
        the MembershipOut projection pulls fields from the related Role
        object, not from UserRole itself.
        """
        memberships = []
        for m in user.role_memberships:
            # role must be eager-loaded (UserRepository.get_by_id/list_all) —
            # attribute access here would be illegal async lazy IO otherwise.
            role = m.role
            memberships.append(MembershipOut(
                role_id=m.role_id,
                role_name=role.name if role else "",
                role_display_name=role.display_name if role else "",
                is_role_admin=m.is_role_admin,
            ))
        return cls(
            id=user.id,
            username=user.username,
            email=user.email,
            is_superuser=user.is_superuser,
            is_active=user.is_active,
            created_at=user.created_at,
            memberships=memberships,
        )
