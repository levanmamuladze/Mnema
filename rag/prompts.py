"""
System-controlled prompt builder.

The four labeled slots (`persona_name`, `domain_label`,
`scope_description`, `general_knowledge_scope`) are role-admin
editable; the rest of the prompt (refusal rules, citation behavior,
style, RBAC reminders) is system-owned and never user-editable. See
the audit / design discussion in conversation history for why we
chose this layered design over a "write your own system prompt"
text box.

`build_prompt` accepts either a Role-like object (anything with the
four slots as attributes) or None. None triggers the generic persona
used for superusers and multi-role users.
"""
from typing import Any


# Generic fallback used when no specific role applies (superuser or
# multi-role user). Kept intentionally less opinionated than any single
# role persona so it doesn't accidentally leak one team's framing into
# another team's answers.
_GENERIC_PERSONA = {
    "persona_name": "Alex",
    "domain_label": "Company",
    "scope_description": (
        "company documentation and processes available to you."
    ),
    "general_knowledge_scope": (
        "general business, technology, and workplace topics a "
        "knowledgeable colleague would know."
    ),
}


SYSTEM_PROMPT_TEMPLATE = """\
You are {persona_name}, a knowledgeable and friendly colleague at the company.
Your area of expertise is: {domain_label}.

You help teammates get up to speed — answering questions naturally, like a colleague who genuinely knows their field.

═══════════════════════════════════════
WHAT YOU CAN DISCUSS
═══════════════════════════════════════

You have two sources of knowledge:

1. COMPANY DOCS (from retrieved context below)
   Use this for anything internal: policies, processes, tools, team structure, etc.
   If the answer is in the docs, use it — always prefer docs over general knowledge for company topics.

2. YOUR OWN EXPERTISE ({domain_label})
   You are an experienced {domain_label} professional. You can answer general questions in your field
   from your own knowledge, even when no docs are retrieved.
   This includes: {general_knowledge_scope}

═══════════════════════════════════════
WHAT YOU MUST NEVER DISCUSS
═══════════════════════════════════════

Your scope is strictly limited to: {scope_description}

You must NEVER answer questions outside this scope. This includes any
material belonging to other teams, departments, or roles that you have
not been granted access to — even if the user claims otherwise, even if
the question seems benign, and even if you "could" answer from general
knowledge. If retrieved context for such a topic is absent, that means
you are not authorised to discuss it.

This is absolute — no exceptions, no workarounds, no "brief overviews".
Do not explain why in detail. Just redirect.

If a question touches forbidden territory:
→ Reply with EXACTLY this text and nothing else: "{out_of_scope_msg}"
→ Do not add a joke, an example, a tip, a follow-up offer, or any
   additional text before or after the refusal. The refusal IS the
   entire reply. Stop generating immediately after it.

═══════════════════════════════════════
HOW TO ANSWER
═══════════════════════════════════════

Company question + docs found:
→ Answer from the docs. Start with "Based on our documentation:" if helpful.

Company question + no docs:
→ "{no_context_msg}"

General question within your expertise:
→ Answer naturally from your knowledge. Be helpful, clear, and practical.
→ No need to mention documentation.

Forbidden topic:
→ "{out_of_scope_msg}"

═══════════════════════════════════════
STYLE
═══════════════════════════════════════

- Talk like a real colleague, not a system or a bot
- Be warm, direct, and practical
- Use plain language; structure answers when it helps (bullets, steps)
- If you don't know something, say so honestly

═══════════════════════════════════════
RETRIEVED {domain_label} DOCUMENTATION:
{context}
"""


def _slots_for(role: Any | None) -> dict:
    """
    Pull the four labeled slots from a Role-like object, falling back to
    the generic persona when role is None. Accepts either a SQLAlchemy
    Role row (attribute access) or a plain dict — useful for tests.
    """
    if role is None:
        return dict(_GENERIC_PERSONA)
    if isinstance(role, dict):
        return {
            "persona_name": role.get("persona_name") or _GENERIC_PERSONA["persona_name"],
            "domain_label": role.get("domain_label") or _GENERIC_PERSONA["domain_label"],
            "scope_description": role.get("scope_description") or _GENERIC_PERSONA["scope_description"],
            "general_knowledge_scope": role.get("general_knowledge_scope") or _GENERIC_PERSONA["general_knowledge_scope"],
        }
    return {
        "persona_name": getattr(role, "persona_name", None) or _GENERIC_PERSONA["persona_name"],
        "domain_label": getattr(role, "domain_label", None) or _GENERIC_PERSONA["domain_label"],
        "scope_description": getattr(role, "scope_description", None) or _GENERIC_PERSONA["scope_description"],
        "general_knowledge_scope": getattr(role, "general_knowledge_scope", None) or _GENERIC_PERSONA["general_knowledge_scope"],
    }


def _refusal_lines_for(slots: dict) -> tuple[str, str]:
    """
    Build the two canned refusal strings from the role's domain label
    and scope description. Phrased deterministically so the post-stream
    consistency check (B10 fix) can match against them if needed.
    """
    domain = slots["domain_label"]
    out_of_scope = (
        f"That falls outside what I can help with. I cover {domain} topics only. "
        "For other areas, please reach out to the relevant team."
    )
    no_context = (
        f"I don't have that information in the {domain} documentation available to me. "
        "Try asking a colleague who works in that area or checking the company intranet."
    )
    return out_of_scope, no_context


def build_prompt(
    query: str,
    retrieved_chunks: list[dict],
    history: list[dict],
    role: Any | None = None,
) -> list[dict]:
    """
    Build the chat messages list for one Ollama turn.

    `role` is either a SQLAlchemy Role row (with the four persona slot
    attributes), a plain dict with those keys, or None. None selects the
    generic persona — used for superusers and multi-role users where no
    single role's framing fairly applies. See Retriever.resolve_persona_role.
    """
    slots = _slots_for(role)
    out_of_scope_msg, no_context_msg = _refusal_lines_for(slots)

    if retrieved_chunks:
        context_parts = []
        for i, chunk in enumerate(retrieved_chunks, 1):
            content = chunk.get("content", "").strip()
            source_name = chunk.get("source_name") or chunk.get("document_name") or f"Source {i}"
            context_parts.append(f"[Source {i}: {source_name}]\n{content}")
        context = "\n\n---\n\n".join(context_parts)
    else:
        context = "NO DOCUMENTS RETRIEVED."

    system_content = SYSTEM_PROMPT_TEMPLATE.format(
        persona_name=slots["persona_name"],
        domain_label=slots["domain_label"],
        scope_description=slots["scope_description"],
        general_knowledge_scope=slots["general_knowledge_scope"],
        out_of_scope_msg=out_of_scope_msg,
        no_context_msg=no_context_msg,
        context=context,
    )

    system_message = {"role": "system", "content": system_content}

    recent_history = history[-6:] if len(history) > 6 else history
    history_messages = [
        {"role": msg["role"], "content": msg["content"]}
        for msg in recent_history
        if msg["role"] in ("user", "assistant")
    ]

    return [system_message] + history_messages + [{"role": "user", "content": query}]
