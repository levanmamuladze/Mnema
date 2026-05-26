import json
import logging
from collections.abc import AsyncGenerator

import httpx

from core.config import settings
from rag.prompts import build_prompt
from rag.retriever import Retriever

logger = logging.getLogger(__name__)


# A 7B model can need 30–120 s to load into memory on the first /api/chat
# call after Ollama starts. The previous single 120 s value applied to every
# phase (connect / read / write / pool), and the read budget was burned
# during model load — the first token never arrived in time, the stream
# errored, and the user saw nothing. Read budget here is per-event, so it
# only triggers when Ollama stops sending tokens, not over total runtime.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


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
            "options": {"temperature": 0.3},
        }
        logger.info(
            "Streaming from Ollama: url=%s model=%s messages=%d",
            url, settings.OLLAMA_MODEL, len(messages),
        )

        try:
            async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code >= 400:
                        # Read the body so the error log shows what Ollama
                        # actually complained about (e.g. "model not found").
                        body = await response.aread()
                        text = body.decode("utf-8", errors="replace")[:500]
                        logger.error(
                            "Ollama HTTP %s: %s",
                            response.status_code, text,
                        )
                        yield (
                            f"\n\n[Ollama error {response.status_code}: {text}]"
                        )
                        return

                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            logger.warning("Non-JSON line from Ollama: %r", line[:200])
                            continue
                        if "error" in data:
                            logger.error("Ollama returned error: %s", data["error"])
                            yield f"\n\n[Ollama error: {data['error']}]"
                            return
                        token = data.get("message", {}).get("content", "")
                        if token:
                            yield token
                        if data.get("done", False):
                            return
        except httpx.ConnectError as exc:
            logger.exception("Ollama ConnectError")
            yield (
                f"\n\n[Could not connect to Ollama at {settings.OLLAMA_BASE_URL}: {exc}]"
            )
        except httpx.ReadTimeout:
            logger.exception("Ollama read timeout (no token for >%s s)", _OLLAMA_TIMEOUT.read)
            yield (
                f"\n\n[Ollama stopped responding (no token for {int(_OLLAMA_TIMEOUT.read)}s). "
                "The model may have failed to load — check `docker logs ollama`.]"
            )
        except httpx.TimeoutException as exc:
            logger.exception("Ollama timeout")
            yield f"\n\n[Ollama timed out: {exc}]"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected Ollama streaming failure")
            yield f"\n\n[Unexpected error talking to Ollama: {exc}]"
