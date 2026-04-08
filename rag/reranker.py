import asyncio
from functools import lru_cache

from sentence_transformers import CrossEncoder

from core.config import settings


@lru_cache(maxsize=1)
def _get_reranker() -> CrossEncoder:
    return CrossEncoder(settings.RERANKER_MODEL, trust_remote_code=False)


class Reranker:
    def __init__(self) -> None:
        self.model = _get_reranker()

    def rerank(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        if not chunks:
            return []

        pairs = [(query, chunk["content"]) for chunk in chunks]
        scores = self.model.predict(pairs, batch_size=16, show_progress_bar=False)

        reranked = []
        for chunk, score in zip(chunks, scores):
            enriched = dict(chunk)
            enriched["rerank_score"] = float(score)
            reranked.append(enriched)

        reranked.sort(key=lambda item: item["rerank_score"], reverse=True)
        return reranked[:top_k]

    async def rerank_async(self, query: str, chunks: list[dict], top_k: int) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.rerank, query, chunks, top_k)
