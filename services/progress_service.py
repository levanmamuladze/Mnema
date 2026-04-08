"""
Learning progress service.

After each assistant reply:
  1. Ask Ollama to extract which topics were covered and how deeply
  2. Upsert UserTopicProgress rows accordingly
  3. If this is the 2nd message exchange, generate an AI session title

Intentionally lightweight — uses one extra Ollama call per reply.
The call is fire-and-forget (errors are swallowed so they never break chat).
"""
import json
import logging
from typing import Any

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


TOPIC_EXTRACTION_PROMPT = """\
You are an educational analyst. Given a conversation exchange, extract the topics discussed and \
rate how well the user demonstrated understanding of each topic.

Respond with ONLY a valid JSON object, no markdown, no explanation.
Format: {{"topics": [{{"name": "topic name", "delta": 5}}]}}

Rules:
- topic name: short (2-5 words), lowercase, relevant to the domain
- delta: integer 1-15 representing how much understanding was shown
  * 1-4: user just asked a basic question
  * 5-9: user showed some understanding or asked a follow-up
  * 10-15: user demonstrated clear comprehension or asked an insightful question
- Return 1-3 topics maximum
- Return empty topics array if the exchange was off-topic or a greeting

User message: {user_message}
Assistant reply: {assistant_reply}
"""

TITLE_GENERATION_PROMPT = """\
Generate a short, descriptive title (3-6 words) for this conversation based on the first exchange.
Respond with ONLY the title text, no quotes, no punctuation at the end.

User: {user_message}
Assistant: {assistant_reply}
"""


async def _call_ollama(prompt: str, max_tokens: int = 200) -> str:
    """Single non-streaming Ollama call. Returns empty string on any error."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
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
    except Exception as e:
        logger.warning(f"Ollama call failed in progress service: {e}")
        return ""


async def extract_topics(user_message: str, assistant_reply: str) -> list[dict[str, Any]]:
    """Returns list of {name, delta} dicts. Never raises."""
    prompt = TOPIC_EXTRACTION_PROMPT.format(
        user_message=user_message[:500],
        assistant_reply=assistant_reply[:500],
    )
    raw = await _call_ollama(prompt, max_tokens=200)
    if not raw:
        return []
    try:
        # Strip any accidental markdown fences
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        data = json.loads(clean)
        return data.get("topics", [])
    except (json.JSONDecodeError, KeyError):
        return []


async def generate_title(user_message: str, assistant_reply: str) -> str:
    """Returns a 3-6 word title string. Never raises."""
    prompt = TITLE_GENERATION_PROMPT.format(
        user_message=user_message[:300],
        assistant_reply=assistant_reply[:300],
    )
    title = await _call_ollama(prompt, max_tokens=20)
    if not title or len(title) > 100:
        return ""
    # Capitalize first letter, strip trailing punctuation
    return title.strip(".!?").strip().capitalize()
