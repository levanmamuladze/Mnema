from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

VALID_ROLES = {"hr", "developer"}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    role: str = "developer"   # hr | developer
    is_admin: bool = False

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        return v

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


class UserRoleUpdate(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


class UserOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    username: str
    email: str
    role: str
    is_admin: bool
    is_active: bool
    created_at: datetime
