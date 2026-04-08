from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.repositories.document_repository import DocumentChunkRepository
from rag.embedder import Embedder
from rag.reranker import Reranker


class Retriever:
    """Dense retrieval with optional cross-encoder reranking and RBAC enforcement."""

    def __init__(self, session: AsyncSession, user_role: str, is_admin: bool = False) -> None:
        self.session = session
        self.user_role = user_role
        self.is_admin = is_admin
        self.chunk_repo = DocumentChunkRepository(session)
        self.embedder = Embedder()
        self.reranker = Reranker() if settings.RERANKER_ENABLED else None

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        candidate_k: int | None = None,
    ) -> list[dict]:
        top_k = top_k or settings.TOP_K_RETRIEVAL
        candidate_k = max(candidate_k or settings.RETRIEVAL_CANDIDATES, top_k)

        query_embedding = await self.embedder.embed_single_async(query)

        if self.is_admin:
            rows = await self.chunk_repo.get_top_chunks_admin(
                query_embedding=query_embedding,
                top_k=candidate_k,
            )
        else:
            rows = await self.chunk_repo.get_top_chunks_for_role(
                query_embedding=query_embedding,
                user_role=self.user_role,
                top_k=candidate_k,
            )

        chunks = [
            {
                "content": chunk.content,
                "document_id": chunk.document_id,
                "chunk_id": chunk.id,
                "score": round(1 - distance, 4),
            }
            for distance, chunk in rows
        ]

        if self.reranker and chunks:
            chunks = await self.reranker.rerank_async(query, chunks, top_k=top_k)
        else:
            chunks = chunks[:top_k]

        return chunks
