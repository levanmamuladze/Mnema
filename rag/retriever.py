from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from db.models import Role
from db.repositories.document_repository import DocumentChunkRepository
from rag.embedder import Embedder
from rag.reranker import Reranker


class Retriever:
    """
    Dense retrieval with optional cross-encoder reranking and RBAC.

    RBAC rules:
      - Superusers see every chunk in the corpus.
      - Everyone else sees chunks belonging to documents tagged with at
        least one of their role memberships (set union, not intersection).
    """

    def __init__(
        self,
        session: AsyncSession,
        user_role_ids: list[int],
        is_superuser: bool = False,
    ) -> None:
        self.session = session
        self.user_role_ids = list(user_role_ids)
        self.is_superuser = is_superuser
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

        if self.is_superuser:
            rows = await self.chunk_repo.get_top_chunks_superuser(
                query_embedding=query_embedding,
                top_k=candidate_k,
            )
        else:
            rows = await self.chunk_repo.get_top_chunks_for_roles(
                query_embedding=query_embedding,
                role_ids=self.user_role_ids,
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

    async def resolve_persona_role(self) -> Role | None:
        """
        Pick which Role's persona the prompt should use this turn.

        Heuristic:
          - 0 roles or superuser → None (generic persona)
          - 1 role               → that role
          - >1 roles             → None (generic persona)

        Rationale: when a user belongs to multiple roles, no single
        role's persona fairly represents them. Picking arbitrarily would
        leak domain framing from one team into another team's answers.
        The generic persona is intentionally less opinionated.
        """
        if self.is_superuser:
            return None
        if len(self.user_role_ids) != 1:
            return None
        result = await self.session.execute(
            select(Role).where(Role.id == self.user_role_ids[0])
        )
        return result.scalar_one_or_none()
