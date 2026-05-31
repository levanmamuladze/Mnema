from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Document
from db.repositories.document_repository import DocumentChunkRepository, DocumentRepository
from ingestion.chunker import TextChunker
from ingestion.loader import DocumentLoader
from rag.embedder import Embedder


class IngestionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.doc_repo = DocumentRepository(session)
        self.chunk_repo = DocumentChunkRepository(session)
        self.loader = DocumentLoader()
        self.chunker = TextChunker()
        self.embedder = Embedder()

    async def index_document(self, document: Document) -> None:
        """Full pipeline: load → chunk → embed → store."""
        text = self.loader.load(document.file_path, document.mime_type)
        if not text.strip():
            raise ValueError(
                f"Document '{document.original_filename}' produced no extractable text. "
                "The file may be empty, image-only, or in an unsupported format."
            )

        chunks = self.chunker.chunk(text)
        if not chunks:
            raise ValueError(
                f"Document '{document.original_filename}' could not be split into chunks. "
                "The extracted text may be too short or contain only whitespace."
            )

        texts = [c["content"] for c in chunks]
        embeddings = await self.embedder.embed_batch_async(texts)

        # Bulk insert: one flush for the whole document, not per chunk.
        await self.chunk_repo.bulk_create_chunks(
            document_id=document.id,
            contents_and_embeddings=list(
                zip((c["content"] for c in chunks), embeddings)
            ),
        )

        await self.doc_repo.mark_indexed(document.id)

    async def reindex_document(self, document_id: int) -> None:
        """Delete existing chunks and re-index from scratch."""
        await self.chunk_repo.delete_by_document(document_id)
        doc = await self.doc_repo.get_by_id_or_raise(document_id)
        doc.is_indexed = False
        self.session.add(doc)
        await self.session.flush()
        await self.index_document(doc)
