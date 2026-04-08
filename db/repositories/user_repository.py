from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, ValidationError
from core.security import hash_password, verify_password
from db.models import VALID_ROLES, User
from db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def create_user(
        self,
        username: str,
        email: str,
        plain_password: str,
        is_admin: bool = False,
        role: str = "developer",
    ) -> User:
        # Validate role for non-admin users
        if not is_admin and role not in VALID_ROLES:
            raise ValidationError(
                f"Invalid role '{role}'. Allowed: {', '.join(sorted(VALID_ROLES))}"
            )

        if await self.get_by_username(username):
            raise ConflictError(f"Username '{username}' is already taken")
        if await self.get_by_email(email):
            raise ConflictError(f"Email '{email}' is already registered")

        return await self.create(
            username=username,
            email=email,
            hashed_password=hash_password(plain_password),
            is_admin=is_admin,
            role=role,
        )

    async def update_role(self, user_id: int, new_role: str) -> User:
        """Admin can reassign a user's role at any time."""
        if new_role not in VALID_ROLES:
            raise ValidationError(
                f"Invalid role '{new_role}'. Allowed: {', '.join(sorted(VALID_ROLES))}"
            )
        user = await self.get_by_id_or_raise(user_id)
        if user.is_admin:
            raise ValidationError("Cannot change the role of an admin account.")
        return await self.update(user, role=new_role)

    async def authenticate(self, username: str, password: str) -> User | None:
        user = await self.get_by_username(username)
        if user is None:
            return None
        if not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user
