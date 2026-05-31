"""
Unit tests for rag/prompts.py.

The prompt builder is the security boundary between user roles. With
roles now dynamic (authored per deployment), we no longer hardcode
"HR" / "Engineering" — instead each role carries its own four labeled
slots which the builder substitutes into the system-controlled
template. These tests verify the substitution + the static safety
scaffold (refusal lines, citation behavior, history truncation).
"""
from __future__ import annotations

from rag.prompts import build_prompt


def _hr_role():
    return {
        "persona_name": "Morgan",
        "domain_label": "HR",
        "scope_description": "HR policies, onboarding, payroll, leave.",
        "general_knowledge_scope": "HR best practices and people management.",
    }


def _dev_role():
    return {
        "persona_name": "Dev",
        "domain_label": "Engineering",
        "scope_description": "code, architecture, deployments, tooling.",
        "general_knowledge_scope": "software engineering and DevOps.",
    }


# ── Basic shape ──────────────────────────────────────────────────────────────


def test_first_message_is_system():
    msgs = build_prompt("Q", [], [], role=_hr_role())
    assert msgs[0]["role"] == "system"


def test_last_message_is_the_user_query():
    msgs = build_prompt("How does PTO work?", [], [], role=_hr_role())
    assert msgs[-1] == {"role": "user", "content": "How does PTO work?"}


# ── Role substitution ────────────────────────────────────────────────────────


def test_role_persona_name_appears_in_system_prompt():
    sys = build_prompt("Q", [], [], role=_hr_role())[0]["content"]
    assert "Morgan" in sys
    assert "HR" in sys


def test_different_role_swaps_persona_completely():
    sys = build_prompt("Q", [], [], role=_dev_role())[0]["content"]
    assert "Dev" in sys
    assert "Engineering" in sys
    # And the HR persona must NOT leak in.
    assert "Morgan" not in sys


def test_none_role_uses_generic_persona():
    sys = build_prompt("Q", [], [], role=None)[0]["content"]
    # Generic persona = Alex / Company.
    assert "Alex" in sys
    assert "Company" in sys


# ── Refusal lines are derived deterministically from the role ────────────────


def test_refusal_line_mentions_role_domain():
    """The canned out-of-scope line is built from the role's domain_label
    so the model can be told to emit it verbatim, and so a post-stream
    consistency check could match against it without role-specific config."""
    sys = build_prompt("Q", [], [], role=_hr_role())[0]["content"]
    assert "I cover HR topics only" in sys

    sys2 = build_prompt("Q", [], [], role=_dev_role())[0]["content"]
    assert "I cover Engineering topics only" in sys2


# ── Retrieved context wiring ─────────────────────────────────────────────────


def test_retrieved_chunks_appear_in_system_prompt():
    chunks = [
        {"content": "Vacation policy: 20 days per year.", "source_name": "handbook.pdf"},
        {"content": "Sick leave: unlimited with manager approval.", "source_name": "handbook.pdf"},
    ]
    sys = build_prompt("PTO?", chunks, [], role=_hr_role())[0]["content"]
    assert "20 days per year" in sys
    assert "unlimited with manager approval" in sys


def test_no_chunks_yields_placeholder_text():
    sys = build_prompt("Q", [], [], role=_hr_role())[0]["content"]
    assert "NO DOCUMENTS RETRIEVED" in sys


# ── History handling ─────────────────────────────────────────────────────────


def test_history_messages_are_included_between_system_and_query():
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    msgs = build_prompt("new question", [], history, role=_hr_role())
    assert len(msgs) == 4
    assert msgs[1] == {"role": "user", "content": "earlier question"}
    assert msgs[2] == {"role": "assistant", "content": "earlier answer"}
    assert msgs[3] == {"role": "user", "content": "new question"}


def test_long_history_is_truncated_to_last_six_messages():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(20)
    ]
    msgs = build_prompt("Q", [], history, role=_hr_role())
    assert len(msgs) == 8  # 1 system + 6 history + 1 user
    history_in_prompt = [m["content"] for m in msgs[1:-1]]
    assert history_in_prompt == [f"m{i}" for i in range(14, 20)]


def test_non_user_non_assistant_history_entries_are_filtered():
    history = [
        {"role": "user", "content": "real user msg"},
        {"role": "system", "content": "should be filtered"},
        {"role": "assistant", "content": "real assistant msg"},
    ]
    msgs = build_prompt("Q", [], history, role=_hr_role())
    contents = [m["content"] for m in msgs]
    assert "should be filtered" not in contents
    assert "real user msg" in contents
    assert "real assistant msg" in contents


# ── Safety scaffold is system-owned ──────────────────────────────────────────


def test_scaffold_includes_strict_refusal_instruction():
    """The prompt must instruct the model to emit the refusal alone — this
    is the B10 fix from earlier rolled into the new dynamic builder."""
    sys = build_prompt("Q", [], [], role=_hr_role())[0]["content"]
    assert "EXACTLY this text and nothing else" in sys
    assert "Stop generating immediately after it." in sys
