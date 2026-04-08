from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Document, DocumentChunk, DocumentRole
from db.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_all_documents(self) -> list[Document]:
        """Admin view — all documents with role assignments loaded."""
        result = await self.session.execute(
            select(Document)
            .options(selectinload(Document.allowed_roles))
            .order_by(Document.uploaded_at.desc())
        )
        return list(result.scalars().all())

    async def get_documents_for_role(self, user_role: str) -> list[Document]:
        """User view — only documents this role is authorised to access."""
        result = await self.session.execute(
            select(Document)
            .join(DocumentRole, DocumentRole.document_id == Document.id)
            .where(DocumentRole.role == user_role)
            .options(selectinload(Document.allowed_roles))
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

    async def get_top_chunks_for_role(
        self,
        query_embedding: list[float],
        user_role: str,
        top_k: int,
    ) -> list[tuple[float, DocumentChunk]]:
        """
        Role-filtered pgvector retrieval.
        Only returns chunks from documents the user's role can access.
        """
        result = await self.session.execute(
            select(
                DocumentChunk,
                DocumentChunk.embedding.cosine_distance(query_embedding).label("distance"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(DocumentRole, DocumentRole.document_id == Document.id)
            .where(
                DocumentRole.role == user_role,
                DocumentChunk.embedding.isnot(None),
            )
            .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        rows = result.all()
        return [(float(row.distance), row.DocumentChunk) for row in rows]

    async def get_top_chunks_admin(
        self,
        query_embedding: list[float],
        top_k: int,
    ) -> list[tuple[float, DocumentChunk]]:
        """
        Admin-only retrieval — no role filter, sees all indexed chunks.
        """
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

    def get_embedding(self, chunk: DocumentChunk) -> list[float] | None:
        if chunk.embedding is None:
            return None
        return chunk.embedding

    async def delete_by_document(self, document_id: int) -> None:
        chunks = await self.get_by_document(document_id)
        for chunk in chunks:
            await self.session.delete(chunk)
        await self.session.flush()