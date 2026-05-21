"""
Unit tests for rag/prompts.py.

The prompt builder is the security boundary between user roles. These tests
verify that HR users never see engineering instructions and vice versa, and
that retrieved context is wired in correctly.
"""
from __future__ import annotations

from rag.prompts import build_prompt


# ── Basic shape ──────────────────────────────────────────────────────────────


def test_first_message_is_system():
    msgs = build_prompt("Q", [], [], user_role="hr")
    assert msgs[0]["role"] == "system"


def test_last_message_is_the_user_query():
    msgs = build_prompt("How does PTO work?", [], [], user_role="hr")
    assert msgs[-1] == {"role": "user", "content": "How does PTO work?"}


# ── Role-specific personas ───────────────────────────────────────────────────


def test_hr_role_uses_hr_persona():
    sys = build_prompt("Q", [], [], user_role="hr")[0]["content"]
    assert "Morgan" in sys
    assert "HR" in sys


def test_developer_role_uses_dev_persona():
    sys = build_prompt("Q", [], [], user_role="developer")[0]["content"]
    assert "Dev" in sys
    assert "Engineering" in sys


def test_unknown_role_falls_back_to_default():
    """An unrecognised role must not crash — it falls back to the default config."""
    sys = build_prompt("Q", [], [], user_role="ceo")[0]["content"]
    # Default persona is 'Alex' with 'Company' domain.
    assert "Alex" in sys
    assert "Company" in sys


def test_none_role_falls_back_to_default():
    sys = build_prompt("Q", [], [], user_role=None)[0]["content"]
    assert "Alex" in sys


# ── Forbidden-scope separation ───────────────────────────────────────────────


def test_hr_system_prompt_forbids_engineering_topics():
    sys = build_prompt("Q", [], [], user_role="hr")[0]["content"]
    # The HR persona's forbidden_scope explicitly lists engineering.
    assert "engineering" in sys.lower()


def test_dev_system_prompt_forbids_hr_topics():
    sys = build_prompt("Q", [], [], user_role="developer")[0]["content"]
    assert "hr policies" in sys.lower() or "payroll" in sys.lower()


# ── Retrieved context wiring ─────────────────────────────────────────────────


def test_retrieved_chunks_appear_in_system_prompt():
    chunks = [
        {"content": "Vacation policy: 20 days per year.", "source_name": "handbook.pdf"},
        {"content": "Sick leave: unlimited with manager approval.", "source_name": "handbook.pdf"},
    ]
    sys = build_prompt("PTO?", chunks, [], user_role="hr")[0]["content"]
    assert "20 days per year" in sys
    assert "unlimited with manager approval" in sys


def test_no_chunks_yields_placeholder_text():
    sys = build_prompt("Q", [], [], user_role="hr")[0]["content"]
    assert "NO DOCUMENTS RETRIEVED" in sys


# ── History handling ─────────────────────────────────────────────────────────


def test_history_messages_are_included_between_system_and_query():
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    msgs = build_prompt("new question", [], history, user_role="hr")
    # system, history×2, user
    assert len(msgs) == 4
    assert msgs[1] == {"role": "user", "content": "earlier question"}
    assert msgs[2] == {"role": "assistant", "content": "earlier answer"}
    assert msgs[3] == {"role": "user", "content": "new question"}


def test_long_history_is_truncated_to_last_six_messages():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(20)
    ]
    msgs = build_prompt("Q", [], history, user_role="hr")
    # 1 system + 6 history + 1 user = 8
    assert len(msgs) == 8
    # The last six historical messages should be m14..m19.
    history_in_prompt = [m["content"] for m in msgs[1:-1]]
    assert history_in_prompt == [f"m{i}" for i in range(14, 20)]


def test_non_user_non_assistant_history_entries_are_filtered():
    """`system` messages slipped into history (e.g. tool outputs) must be dropped."""
    history = [
        {"role": "user", "content": "real user msg"},
        {"role": "system", "content": "should be filtered"},
        {"role": "assistant", "content": "real assistant msg"},
    ]
    msgs = build_prompt("Q", [], history, user_role="hr")
    contents = [m["content"] for m in msgs]
    assert "should be filtered" not in contents
    assert "real user msg" in contents
    assert "real assistant msg" in contents
