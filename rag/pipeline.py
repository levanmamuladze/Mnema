import json
from collections.abc import AsyncGenerator

import httpx

from core.config import settings
from rag.prompts import build_prompt
from rag.retriever import Retriever


class RAGPipeline:
    """
    Stateless RAG pipeline. Inject a Retriever before calling query().
    One instance per request — do not share across requests.
    """

    def __init__(self, retriever: Retriever | None = None) -> None:
        self._retriever = retriever

    def with_retriever(self, retriever: Retriever) -> "RAGPipeline":
        self._retriever = retriever
        return self

    async def query(
        self,
        query: str,
        history: list[dict],
    ) -> AsyncGenerator[str, None]:
        if self._retriever is None:
            raise RuntimeError(
                "RAGPipeline requires a retriever. Call .with_retriever(retriever) first."
            )
        chunks = await self._retriever.retrieve(query, top_k=settings.TOP_K_RETRIEVAL)
        async for token in self._stream_from_chunks(query, chunks, history):
            yield token

    async def _stream_from_chunks(
        self,
        query: str,
        chunks: list[dict],
        history: list[dict],
    ) -> AsyncGenerator[str, None]:
        """
        Build prompt from pre-retrieved chunks and stream from Ollama.
        Called directly by ChatService so retrieval and streaming can use
        separate DB session lifecycles.
        """
        # Admins see all docs — use the generic "Company" config, not a role-specific one
        effective_role = None if (self._retriever and self._retriever.is_admin) else (
            self._retriever.user_role if self._retriever else None
        )
        messages = build_prompt(
            query=query,
            retrieved_chunks=chunks,
            history=history,
            user_role=effective_role,
        )
        async for token in self._stream_ollama(messages):
            yield token

    async def _stream_ollama(
        self,
        messages: list[dict],
    ) -> AsyncGenerator[str, None]:
        url = f"{settings.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": settings.OLLAMA_MODEL,
            "messages": messages,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if data.get("done", False):
                                break
                        except json.JSONDecodeError:
                            continue
        except httpx.ConnectError:
            yield (
                f"\n\n[Error: Could not connect to Ollama at {settings.OLLAMA_BASE_URL}. "
                "Make sure it is running.]"
            )
        except httpx.TimeoutException:
            yield "\n\n[Error: Ollama request timed out after 120 seconds]"
        except httpx.HTTPStatusError as e:
            yield f"\n\n[Error: Ollama returned HTTP {e.response.status_code}]"
