from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ValidationError
from db.models import VALID_ROLES, DocumentRole
from db.repositories.base import BaseRepository


class DocumentRoleRepository(BaseRepository[DocumentRole]):
    model = DocumentRole

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_roles_for_document(self, document_id: int) -> list[str]:
        """Return the list of role strings for a document."""
        result = await self.session.execute(
            select(DocumentRole.role).where(DocumentRole.document_id == document_id)
        )
        return list(result.scalars().all())

    async def set_roles(self, document_id: int, roles: list[str]) -> list[str]:
        """
        Replace ALL role assignments for a document in one operation.
        Validates role names before writing.
        """
        invalid = set(roles) - VALID_ROLES
        if invalid:
            raise ValidationError(
                f"Invalid role(s): {', '.join(sorted(invalid))}. "
                f"Allowed: {', '.join(sorted(VALID_ROLES))}"
            )
        if not roles:
            raise ValidationError("At least one role must be assigned to a document.")

        # Delete existing assignments
        await self.session.execute(
            delete(DocumentRole).where(DocumentRole.document_id == document_id)
        )
        await self.session.flush()

        # Insert new assignments (deduplicated)
        for role in set(roles):
            self.session.add(DocumentRole(document_id=document_id, role=role))

        await self.session.flush()
        return list(set(roles))

    async def user_can_access(self, document_id: int, user_role: str) -> bool:
        """Returns True if the user's role is in the document's allowed roles."""
        result = await self.session.execute(
            select(DocumentRole).where(
                DocumentRole.document_id == document_id,
                DocumentRole.role == user_role,
            )
        )
        return result.scalar_one_or_none() is not None
