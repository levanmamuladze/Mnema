from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.security import hash_password, verify_password
from db.models import Role, User, UserRole
from db.repositories.base import BaseRepository


# from_orm_user reads membership.role; eager-load it here or the access
# triggers async lazy IO (MissingGreenlet).
_USER_LOAD_OPTS = (
    selectinload(User.role_memberships).selectinload(UserRole.role),
)


class UserRepository(BaseRepository[User]):
    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_username(self, username: str) -> User | None:
        result = await self.session.execute(
            select(User)
            .where(User.username == username)
            .options(*_USER_LOAD_OPTS)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User)
            .where(User.email == email)
            .options(*_USER_LOAD_OPTS)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, id: int) -> User | None:
        # Eager-load memberships: every auth check reads them, and lazy IO
        # under async is a footgun.
        result = await self.session.execute(
            select(User)
            .where(User.id == id)
            .options(*_USER_LOAD_OPTS)
        )
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[User]:
        result = await self.session.execute(
            select(User)
            .options(*_USER_LOAD_OPTS)
            .order_by(User.id)
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create_user(
        self,
        username: str,
        email: str,
        plain_password: str,
        is_superuser: bool = False,
        role_assignments: list[tuple[int, bool]] | None = None,
    ) -> User:
        """
        Create a user and (optionally) assign them to one or more roles
        in a single transaction.

        `role_assignments` is a list of `(role_id, is_role_admin)` pairs.
        A non-superuser with no role assignments will exist but won't be
        able to retrieve any documents — callers should usually pass at
        least one role unless creating a superuser-only account.

        Role IDs are validated against the roles table before insert so
        a typo doesn't silently leave the user role-less.
        """
        if await self.get_by_username(username):
            raise ConflictError(f"Username '{username}' is already taken")
        if await self.get_by_email(email):
            raise ConflictError(f"Email '{email}' is already registered")

        if role_assignments:
            role_ids = [rid for rid, _ in role_assignments]
            valid_ids = await self._validate_role_ids(role_ids)
            invalid = set(role_ids) - valid_ids
            if invalid:
                raise ValidationError(
                    f"Unknown role id(s): {sorted(invalid)}"
                )

        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(plain_password),
            is_superuser=is_superuser,
        )
        self.session.add(user)
        await self.session.flush()

        if role_assignments:
            # De-duplicate on role_id; later entries win.
            dedup: dict[int, bool] = {}
            for rid, is_role_admin in role_assignments:
                dedup[rid] = bool(is_role_admin) or dedup.get(rid, False)
            for rid, is_role_admin in dedup.items():
                self.session.add(
                    UserRole(user_id=user.id, role_id=rid, is_role_admin=is_role_admin)
                )
            await self.session.flush()

        # Re-fetch fully hydrated so the router can project via from_orm_user.
        return await self.get_by_id(user.id)

    async def _validate_role_ids(self, role_ids: list[int]) -> set[int]:
        if not role_ids:
            return set()
        result = await self.session.execute(
            select(Role.id).where(Role.id.in_(role_ids))
        )
        return {row[0] for row in result.all()}

    async def set_role_memberships(
        self,
        user_id: int,
        role_assignments: list[tuple[int, bool]],
    ) -> User:
        """
        Replace the user's role memberships with the given list of
        (role_id, is_role_admin) pairs.

        Atomic from the API's perspective: existing memberships are
        deleted and the new set inserted in one transaction.
        """
        user = await self.get_by_id(id=user_id)
        if user is None:
            raise NotFoundError("User", user_id)

        role_ids = [rid for rid, _ in role_assignments]
        if role_ids:
            valid_ids = await self._validate_role_ids(role_ids)
            invalid = set(role_ids) - valid_ids
            if invalid:
                raise ValidationError(f"Unknown role id(s): {sorted(invalid)}")

        from sqlalchemy import delete as sql_delete
        await self.session.execute(
            sql_delete(UserRole).where(UserRole.user_id == user_id)
        )

        dedup: dict[int, bool] = {}
        for rid, is_role_admin in role_assignments:
            dedup[rid] = bool(is_role_admin) or dedup.get(rid, False)
        for rid, is_role_admin in dedup.items():
            self.session.add(
                UserRole(user_id=user_id, role_id=rid, is_role_admin=is_role_admin)
            )

        await self.session.flush()
        return await self.get_by_id(user_id)

    async def count_superusers(self) -> int:
        """Number of accounts with is_superuser=True. Used to refuse deleting the last one."""
        result = await self.session.execute(
            select(func.count()).select_from(User).where(User.is_superuser.is_(True))
        )
        return int(result.scalar_one())

    async def authenticate(self, username: str, password: str) -> User | None:
        user = await self.get_by_username(username)
        if user is None:
            return None
        if not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user
