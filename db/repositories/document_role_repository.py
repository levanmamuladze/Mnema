from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ValidationError
from db.models import DocumentRole, Role
from db.repositories.base import BaseRepository


class DocumentRoleRepository(BaseRepository[DocumentRole]):
    model = DocumentRole

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_role_ids_for_document(self, document_id: int) -> list[int]:
        result = await self.session.execute(
            select(DocumentRole.role_id).where(DocumentRole.document_id == document_id)
        )
        return list(result.scalars().all())

    async def set_role_ids(self, document_id: int, role_ids: list[int]) -> list[int]:
        """
        Replace ALL role assignments for a document in one operation.
        Validates that every role_id exists; raises if any do not.
        Refuses to clear the assignment list entirely — a document with
        no role assignments would be visible only to superusers, which
        is almost always a footgun rather than the intent.
        """
        # De-duplicate, preserving order for the error message below.
        seen: set[int] = set()
        ordered: list[int] = []
        for rid in role_ids:
            if rid not in seen:
                seen.add(rid)
                ordered.append(rid)

        if not ordered:
            raise ValidationError("At least one role must be assigned to a document.")

        result = await self.session.execute(
            select(Role.id).where(Role.id.in_(ordered))
        )
        valid_ids = {row[0] for row in result.all()}
        invalid = [r for r in ordered if r not in valid_ids]
        if invalid:
            raise ValidationError(f"Unknown role id(s): {invalid}")

        await self.session.execute(
            delete(DocumentRole).where(DocumentRole.document_id == document_id)
        )
        await self.session.flush()

        for rid in ordered:
            self.session.add(DocumentRole(document_id=document_id, role_id=rid))

        await self.session.flush()
        return ordered
