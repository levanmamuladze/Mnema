from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Document, DocumentChunk, DocumentRole
from db.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_all_documents(self) -> list[Document]:
        """Superuser view — all documents with their role assignments loaded."""
        result = await self.session.execute(
            select(Document)
            .options(
                selectinload(Document.role_assignments).selectinload(DocumentRole.role)
            )
            .order_by(Document.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def get_documents_for_admin_roles(self, admin_role_ids: list[int]) -> list[Document]:
        """
        Role-admin document view — return every document that touches at
        least one of the caller's admin roles. The full role-assignment
        list is loaded so the UI can show whether a doc is shared with
        other teams (in which case edit/delete is restricted server-side).
        """
        if not admin_role_ids:
            return []
        result = await self.session.execute(
            select(Document)
            .join(DocumentRole, DocumentRole.document_id == Document.id)
            .where(DocumentRole.role_id.in_(admin_role_ids))
            .options(
                selectinload(Document.role_assignments).selectinload(DocumentRole.role)
            )
            .order_by(Document.uploaded_at.desc())
        )
        return list(result.scalars().unique().all())

    async def get_documents_for_roles(self, role_ids: list[int]) -> list[Document]:
        """
        Return every document where at least one of the document's role
        assignments matches one of the user's role IDs. With an empty
        list this returns no rows (intentional fail-closed for users
        with no role memberships).
        """
        if not role_ids:
            return []
        result = await self.session.execute(
            select(Document)
            .join(DocumentRole, DocumentRole.document_id == Document.id)
            .where(DocumentRole.role_id.in_(role_ids))
            .options(
                selectinload(Document.role_assignments).selectinload(DocumentRole.role)
            )
            .order_by(Document.uploaded_at.desc())
        )
        return list(result.scalars().unique().all())

    async def create_document(
        self,
        filename: str,
        original_filename: str,
        file_path: str,
        file_size: int,
        mime_type: str,
        uploaded_by_admin: int,
    ) -> Document:
        return await self.create(
            filename=filename,
            original_filename=original_filename,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            uploaded_by_admin=uploaded_by_admin,
        )

    async def reassign_uploader(self, from_user_id: int, to_user_id: int) -> int:
        """
        Transfer ownership of every document uploaded by `from_user_id` to
        `to_user_id`. Used before deleting a user so the FK on
        documents.uploaded_by_admin (which has no ON DELETE clause) does
        not block the user-delete operation. Returns the number of rows
        that were reassigned.
        """
        result = await self.session.execute(
            update(Document)
            .where(Document.uploaded_by_admin == from_user_id)
            .values(uploaded_by_admin=to_user_id)
        )
        await self.session.flush()
        return result.rowcount or 0

    async def mark_indexed(self, document_id: int) -> None:
        doc = await self.get_by_id_or_raise(document_id)
        doc.is_indexed = True
        self.session.add(doc)
        await self.session.flush()


class DocumentChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_document(self, document_id: int) -> list[DocumentChunk]:
        result = await self.session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def create_chunk(
        self,
        document_id: int,
        content: str,
        chunk_index: int,
        embedding: list[float] | None = None,
    ) -> DocumentChunk:
        return await self.create(
            document_id=document_id,
            content=content,
            chunk_index=chunk_index,
            embedding=embedding,
        )

    async def bulk_create_chunks(
        self,
        document_id: int,
        contents_and_embeddings: list[tuple[str, list[float] | None]],
    ) -> int:
        if not contents_and_embeddings:
            return 0
        chunk_objs = [
            DocumentChunk(
                document_id=document_id,
                content=content,
                chunk_index=i,
                embedding=embedding,
            )
            for i, (content, embedding) in enumerate(contents_and_embeddings)
        ]
        self.session.add_all(chunk_objs)
        await self.session.flush()
        return len(chunk_objs)

    async def get_top_chunks_for_roles(
        self,
        query_embedding: list[float],
        role_ids: list[int],
        top_k: int,
    ) -> list[tuple[float, DocumentChunk]]:
        """
        Role-filtered pgvector retrieval. Returns only chunks belonging
        to documents tagged with at least one of `role_ids`.

        Implemented as a semi-join (IN subquery) rather than a JOIN to
        avoid row multiplication when a document is tagged with several
        of the user's roles AND/OR the user holds several roles that
        match the same document. A plain JOIN would multiply the chunk
        rows by the number of (document_role × user_role) matches; a
        DISTINCT on top of it conflicts with Postgres's rule that
        ORDER BY expressions must be in the SELECT list. The subquery
        keeps each chunk row unique and the ordering valid.
        """
        if not role_ids:
            return []
        allowed_doc_ids = (
            select(DocumentRole.document_id)
            .where(DocumentRole.role_id.in_(role_ids))
        )
        result = await self.session.execute(
            select(
                DocumentChunk,
                DocumentChunk.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .where(
                DocumentChunk.document_id.in_(allowed_doc_ids),
                DocumentChunk.embedding.isnot(None),
            )
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        rows = result.all()
        return [(float(row.distance), row.DocumentChunk) for row in rows]

    async def get_top_chunks_superuser(
        self,
        query_embedding: list[float],
        top_k: int,
    ) -> list[tuple[float, DocumentChunk]]:
        """Superuser retrieval — no role filter, sees all indexed chunks."""
        result = await self.session.execute(
            select(
                DocumentChunk,
                DocumentChunk.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .where(DocumentChunk.embedding.isnot(None))
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        rows = result.all()
        return [(float(row.distance), row.DocumentChunk) for row in rows]

    async def delete_by_document(self, document_id: int) -> None:
        await self.session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )
        await self.session.flush()
