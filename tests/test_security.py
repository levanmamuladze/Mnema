"""
Unit tests for core/security.py.

Covers:
  - bcrypt password hashing round trip
  - JWT access/refresh token creation + decoding
  - Token type confusion (refresh token must not decode as access, and vice versa)
  - Tampered tokens are rejected
"""
from __future__ import annotations

from core.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)


# ── Passwords ────────────────────────────────────────────────────────────────


def test_hash_password_round_trip():
    hashed = hash_password("Sw0rdfish!")
    assert verify_password("Sw0rdfish!", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_hash_password_produces_different_hashes_each_call():
    """bcrypt embeds a per-call salt, so two hashes of the same input must differ."""
    a = hash_password("samepassword")
    b = hash_password("samepassword")
    assert a != b
    # But both still verify.
    assert verify_password("samepassword", a)
    assert verify_password("samepassword", b)


# ── JWT round trips ──────────────────────────────────────────────────────────


def test_access_token_round_trip():
    tok = create_access_token(user_id=42, is_admin=True)
    payload = decode_access_token(tok)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["is_admin"] is True
    assert payload["type"] == "access"


def test_refresh_token_round_trip():
    tok = create_refresh_token(user_id=42)
    payload = decode_refresh_token(tok)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["type"] == "refresh"


# ── Token type confusion guards ──────────────────────────────────────────────


def test_refresh_token_does_not_decode_as_access():
    """A refresh token presented to the access decoder must be rejected."""
    rt = create_refresh_token(user_id=1)
    assert decode_access_token(rt) is None


def test_access_token_does_not_decode_as_refresh():
    """Symmetric guard: access tokens are not valid refresh tokens."""
    at = create_access_token(user_id=1, is_admin=False)
    assert decode_refresh_token(at) is None


# ── Tamper detection ─────────────────────────────────────────────────────────


def test_tampered_signature_rejected():
    tok = create_access_token(user_id=1, is_admin=False)
    # Flip the very last char of the signature — anything decodable -> still wrong sig.
    tampered = tok[:-1] + ("A" if tok[-1] != "A" else "B")
    assert decode_access_token(tampered) is None


def test_garbage_string_rejected():
    assert decode_access_token("not.a.jwt") is None
    assert decode_refresh_token("not.a.jwt") is None
    assert decode_access_token("") is None
