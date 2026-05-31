import json
import logging
from collections.abc import AsyncGenerator

import httpx

from core.config import settings
from rag.prompts import build_prompt
from rag.retriever import Retriever

logger = logging.getLogger(__name__)


# read is per-event (fires only if Ollama stalls between tokens), so the
# generous budget covers a cold model load without killing a slow stream.
_OLLAMA_TIMEOUT = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)


class RAGPipeline:
    """
    Stateless RAG pipeline. Inject a Retriever via with_retriever(), then
    call _stream_from_chunks() with pre-retrieved chunks.
    One instance per request — do not share across requests.
    """

    def __init__(self, retriever: Retriever | None = None) -> None:
        self._retriever = retriever

    def with_retriever(self, retriever: Retriever) -> "RAGPipeline":
        self._retriever = retriever
        return self

    async def _stream_from_chunks(
        self,
        query: str,
        chunks: list[dict],
        history: list[dict],
        role: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Build prompt from pre-retrieved chunks and stream from Ollama.
        Called directly by ChatService so retrieval and streaming can use
        separate DB session lifecycles.

        `role` is the resolved persona — a plain dict of the four persona
        slots, or None for the generic persona. It MUST be resolved by the
        caller while the DB session is still open; resolving it here used
        to run a query on an already-closed session for single-role users.
        """
        messages = build_prompt(
            query=query,
            retrieved_chunks=chunks,
            history=history,
            role=role,
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
            "options": {"temperature": 0.6},
        }
        logger.info(
            "Streaming from Ollama: url=%s model=%s messages=%d",
            url, settings.OLLAMA_MODEL, len(messages),
        )

        try:
            async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code >= 400:
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
