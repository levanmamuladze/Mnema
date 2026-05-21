"""
Unit tests for api/schemas — Pydantic input validators.

These guard the API surface against malformed input. The validators are
pure and fast to test.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.user import UserCreate, UserRoleUpdate
from api.schemas.chat import ConversationUpdate, MessageCreate


# ── UserCreate ───────────────────────────────────────────────────────────────


def test_user_create_accepts_valid_input():
    u = UserCreate(
        username="jane_doe",
        email="jane@example.com",
        password="Strong1Pass",
        role="hr",
    )
    assert u.username == "jane_doe"
    assert u.role == "hr"
    assert u.is_admin is False  # default


def test_user_create_rejects_password_without_digit():
    with pytest.raises(ValidationError, match="digit"):
        UserCreate(
            username="x", email="x@y.com", password="NoDigitHere", role="hr"
        )


def test_user_create_rejects_password_without_uppercase():
    with pytest.raises(ValidationError, match="uppercase"):
        UserCreate(
            username="x", email="x@y.com", password="alllower1", role="hr"
        )


def test_user_create_rejects_short_password():
    with pytest.raises(ValidationError):
        UserCreate(
            username="x", email="x@y.com", password="Aa1", role="hr"
        )


def test_user_create_rejects_invalid_role():
    with pytest.raises(ValidationError, match="Role must be one of"):
        UserCreate(
            username="x", email="x@y.com", password="Strong1Pass", role="ceo"
        )


def test_user_create_rejects_username_with_bad_chars():
    """Username pattern allows only [a-zA-Z0-9_-]."""
    with pytest.raises(ValidationError):
        UserCreate(
            username="bad name with spaces",
            email="x@y.com",
            password="Strong1Pass",
            role="hr",
        )


def test_user_create_rejects_invalid_email():
    with pytest.raises(ValidationError):
        UserCreate(
            username="x", email="not-an-email", password="Strong1Pass", role="hr"
        )


# ── UserRoleUpdate ───────────────────────────────────────────────────────────


def test_user_role_update_accepts_valid_role():
    assert UserRoleUpdate(role="developer").role == "developer"
    assert UserRoleUpdate(role="hr").role == "hr"


def test_user_role_update_rejects_unknown_role():
    with pytest.raises(ValidationError):
        UserRoleUpdate(role="superuser")


# ── ConversationUpdate ───────────────────────────────────────────────────────


def test_conversation_update_rejects_empty_title():
    with pytest.raises(ValidationError):
        ConversationUpdate(title="")


def test_conversation_update_accepts_normal_title():
    assert ConversationUpdate(title="My chat").title == "My chat"


def test_conversation_update_rejects_too_long_title():
    with pytest.raises(ValidationError):
        ConversationUpdate(title="x" * 201)


# ── MessageCreate ────────────────────────────────────────────────────────────


def test_message_create_accepts_normal_content():
    assert MessageCreate(content="Hello there").content == "Hello there"


def test_message_create_rejects_empty_string():
    with pytest.raises(ValidationError):
        MessageCreate(content="")


def test_message_create_rejects_oversize_content():
    with pytest.raises(ValidationError):
        MessageCreate(content="x" * 10_001)
