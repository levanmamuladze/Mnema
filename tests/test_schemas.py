"""
Unit tests for api/schemas — Pydantic input validators.

These guard the API surface against malformed input. The validators are
pure and fast to test. With the role rework, UserCreate no longer carries
a `role` string; roles are passed as a list of (role_id, is_role_admin)
assignments instead, and validity of role_ids is checked by the
repository (not the schema).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas.chat import ConversationUpdate, MessageCreate
from api.schemas.role import RoleCreate
from api.schemas.user import UserCreate, UserRolesUpdate


# ── UserCreate ───────────────────────────────────────────────────────────────


def test_user_create_accepts_valid_input():
    u = UserCreate(
        username="jane_doe",
        email="jane@example.com",
        password="Strong1Pass",
    )
    assert u.username == "jane_doe"
    assert u.is_superuser is False
    assert u.role_assignments == []


def test_user_create_accepts_role_assignments():
    u = UserCreate(
        username="jane_doe",
        email="jane@example.com",
        password="Strong1Pass",
        role_assignments=[{"role_id": 1, "is_role_admin": True}, {"role_id": 2}],
    )
    assert u.role_assignments[0].role_id == 1
    assert u.role_assignments[0].is_role_admin is True
    assert u.role_assignments[1].role_id == 2
    assert u.role_assignments[1].is_role_admin is False


def test_user_create_rejects_password_without_digit():
    with pytest.raises(ValidationError, match="digit"):
        UserCreate(username="x", email="x@y.com", password="NoDigitHere")


def test_user_create_rejects_password_without_uppercase():
    with pytest.raises(ValidationError, match="uppercase"):
        UserCreate(username="x", email="x@y.com", password="alllower1")


def test_user_create_rejects_short_password():
    with pytest.raises(ValidationError):
        UserCreate(username="x", email="x@y.com", password="Aa1")


def test_user_create_rejects_username_with_bad_chars():
    with pytest.raises(ValidationError):
        UserCreate(
            username="bad name with spaces",
            email="x@y.com",
            password="Strong1Pass",
        )


def test_user_create_rejects_invalid_email():
    with pytest.raises(ValidationError):
        UserCreate(username="x", email="not-an-email", password="Strong1Pass")


# ── UserRolesUpdate ──────────────────────────────────────────────────────────


def test_user_roles_update_accepts_empty_list():
    """Allowed: a payload that strips every role from a user. The
    server-side delete_user / role-membership flows decide whether
    that's actually allowed for this user (e.g. last superuser)."""
    payload = UserRolesUpdate(role_assignments=[])
    assert payload.role_assignments == []


def test_user_roles_update_accepts_multiple_assignments():
    payload = UserRolesUpdate(role_assignments=[
        {"role_id": 1, "is_role_admin": True},
        {"role_id": 2, "is_role_admin": False},
    ])
    assert len(payload.role_assignments) == 2


# ── RoleCreate ───────────────────────────────────────────────────────────────


def test_role_create_accepts_valid_payload():
    r = RoleCreate(
        name="sales",
        display_name="Sales Team",
        persona_name="Sam",
        domain_label="Sales",
        scope_description="sales playbooks and CRM processes",
        general_knowledge_scope="sales methodology and negotiation",
    )
    assert r.name == "sales"


def test_role_create_normalises_name_to_lowercase():
    r = RoleCreate(
        name="  Sales  ",
        display_name="Sales Team",
        persona_name="Sam",
        domain_label="Sales",
        scope_description="x",
        general_knowledge_scope="y",
    )
    assert r.name == "sales"


def test_role_create_rejects_uppercase_or_special_chars_in_name():
    with pytest.raises(ValidationError):
        RoleCreate(
            name="Sales Team!",
            display_name="Sales Team",
            persona_name="Sam",
            domain_label="Sales",
            scope_description="x",
            general_knowledge_scope="y",
        )


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
