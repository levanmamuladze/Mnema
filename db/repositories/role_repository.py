"""
Repository for the dynamic `roles` table and its `user_roles`
associations.

Naming rules and validation rules for a role's `name` field live here
because they're enforced wherever a role is created or renamed — the
schema layer cannot enforce uniqueness against the database, and the
router layer should not be reaching into row state.
"""
import re

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.exceptions import ConflictError, NotFoundError, ValidationError
from db.models import Role, UserRole
from db.repositories.base import BaseRepository


# Role.name is the internal slug used in retrieval queries; keep it
# URL/identifier-safe.
_ROLE_NAME_RE = re.compile(r"^[a-z0-9_-]{2,50}$")


class RoleRepository(BaseRepository[Role]):
    model = Role

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @staticmethod
    def _normalise_name(name: str) -> str:
        return name.strip().lower()

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _ROLE_NAME_RE.match(name):
            raise ValidationError(
                "Role name must be 2–50 chars of lowercase letters, "
                "digits, underscore, or hyphen."
            )

    async def list_all(self, limit: int = 100, offset: int = 0) -> list[Role]:
        result = await self.session.execute(
            select(Role).order_by(Role.display_name).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> Role | None:
        result = await self.session.execute(
            select(Role).where(Role.name == self._normalise_name(name))
        )
        return result.scalar_one_or_none()

    async def get_with_members(self, role_id: int) -> Role | None:
        result = await self.session.execute(
            select(Role)
            .where(Role.id == role_id)
            .options(selectinload(Role.memberships).selectinload(UserRole.user))
        )
        return result.scalar_one_or_none()

    async def create_role(
        self,
        *,
        name: str,
        display_name: str,
        persona_name: str,
        domain_label: str,
        scope_description: str,
        general_knowledge_scope: str,
        created_by: int | None = None,
    ) -> Role:
        name = self._normalise_name(name)
        self._validate_name(name)
        if await self.get_by_name(name):
            raise ConflictError(f"A role named '{name}' already exists.")

        return await self.create(
            name=name,
            display_name=display_name.strip(),
            persona_name=persona_name.strip(),
            domain_label=domain_label.strip(),
            scope_description=scope_description.strip(),
            general_knowledge_scope=general_knowledge_scope.strip(),
            created_by=created_by,
        )

    async def update_role(
        self,
        role_id: int,
        *,
        display_name: str | None = None,
        persona_name: str | None = None,
        domain_label: str | None = None,
        scope_description: str | None = None,
        general_knowledge_scope: str | None = None,
    ) -> Role:
        """Partial update; `name` is intentionally not editable — it's the
        stable identifier used by retrieval queries and audit logs."""
        role = await self.get_by_id_or_raise(role_id)
        updates: dict[str, str] = {}
        if display_name is not None:
            updates["display_name"] = display_name.strip()
        if persona_name is not None:
            updates["persona_name"] = persona_name.strip()
        if domain_label is not None:
            updates["domain_label"] = domain_label.strip()
        if scope_description is not None:
            updates["scope_description"] = scope_description.strip()
        if general_knowledge_scope is not None:
            updates["general_knowledge_scope"] = general_knowledge_scope.strip()
        if updates:
            return await self.update(role, **updates)
        return role

    async def delete_role(self, role_id: int) -> None:
        """Delete a role plus its user_roles and document_roles via cascade.

        Documents that ONLY had this role become unreachable to non-
        superusers afterwards; callers are responsible for warning the
        user before invoking this. (No automatic doc cleanup — that
        would lose data.)
        """
        role = await self.get_by_id_or_raise(role_id)
        await self.delete(role)

    # ── Membership management ────────────────────────────────────────

    async def get_membership(self, user_id: int, role_id: int) -> UserRole | None:
        result = await self.session.execute(
            select(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == role_id
            )
        )
        return result.scalar_one_or_none()

    async def add_member(
        self, user_id: int, role_id: int, is_role_admin: bool = False
    ) -> UserRole:
        """Idempotent: re-adding an existing member just (optionally) flips
        the admin flag if the new value is more permissive."""
        existing = await self.get_membership(user_id, role_id)
        if existing is not None:
            if is_role_admin and not existing.is_role_admin:
                existing.is_role_admin = True
                self.session.add(existing)
                await self.session.flush()
            return existing
        return await self.create(
            user_id=user_id, role_id=role_id, is_role_admin=is_role_admin
        )

    async def remove_member(self, user_id: int, role_id: int) -> bool:
        result = await self.session.execute(
            delete(UserRole).where(
                UserRole.user_id == user_id, UserRole.role_id == role_id
            )
        )
        return (result.rowcount or 0) > 0

    async def set_member_admin(
        self, user_id: int, role_id: int, is_role_admin: bool
    ) -> UserRole:
        """Promote or demote an existing member's admin flag. Raises if
        the user is not currently a member of this role."""
        existing = await self.get_membership(user_id, role_id)
        if existing is None:
            raise NotFoundError(
                f"Membership(user={user_id}, role={role_id})"
            )
        existing.is_role_admin = bool(is_role_admin)
        self.session.add(existing)
        await self.session.flush()
        await self.session.refresh(existing)
        return existing
