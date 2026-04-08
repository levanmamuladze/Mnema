import asyncio
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from core.config import settings


@lru_cache(maxsize=1)
def _get_model() -> SentenceTransformer:
    """Load model once and cache for the process lifetime."""
    return SentenceTransformer(settings.EMBED_MODEL, trust_remote_code=False)


class Embedder:
    def __init__(self) -> None:
        self.model = _get_model()

    def embed_single(self, text: str) -> list[float]:
        embedding = self.model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Synchronous batch embedding — always prefer over embed_single in a loop."""
        embeddings = self.model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [e.tolist() for e in embeddings]

    async def embed_single_async(self, text: str) -> list[float]:
        """Non-blocking version — runs sync model in thread pool executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_single, text)

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        """Non-blocking batch version."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.embed_batch, texts)
