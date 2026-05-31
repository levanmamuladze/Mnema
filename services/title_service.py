"""
Conversation post-processing: auto-title generation.

After the first exchange, ask Ollama for a short conversation title. This
is fire-and-forget (errors are swallowed so they never break chat).
"""
import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


TITLE_GENERATION_PROMPT = """\
Generate a short, descriptive title (3-6 words) for this conversation based on the first exchange.
Respond with ONLY the title text, no quotes, no punctuation at the end.

User: {user_message}
Assistant: {assistant_reply}
"""


# Generous read budget for cold model loads; short connect budget fails fast.
_TITLE_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=15.0, pool=5.0)


async def _call_ollama(prompt: str, max_tokens: int = 200) -> str:
    """Single non-streaming Ollama call. Returns empty string on any error."""
    try:
        async with httpx.AsyncClient(timeout=_TITLE_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": max_tokens, "temperature": 0.1},
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
    except Exception:
        # Fire-and-forget: never raise (reply already streamed). logger.exception
        # keeps real failures (bad URL, auth) visible instead of silently swallowed.
        logger.exception("Ollama call failed in title service")
        return ""


async def generate_title(user_message: str, assistant_reply: str) -> str:
    """Returns a 3-6 word title string. Never raises."""
    prompt = TITLE_GENERATION_PROMPT.format(
        user_message=user_message[:300],
        assistant_reply=assistant_reply[:300],
    )
    title = await _call_ollama(prompt, max_tokens=20)
    if not title or len(title) > 100:
        return ""
    return title.strip(".!?").strip().capitalize()
