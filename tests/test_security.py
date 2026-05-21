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
    TokenExpired,
    TokenInvalid,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    decode_token_strict,
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
    # Flip a middle character of the signature. The LAST base64url char of an
    # HS256 signature has 4 unused padding bits, so changing it can produce
    # the same underlying bytes — using a middle position keeps the test
    # deterministic across runs.
    head, _, sig = tok.rpartition(".")
    mid = len(sig) // 2
    new_char = "A" if sig[mid] != "A" else "B"
    tampered = f"{head}.{sig[:mid]}{new_char}{sig[mid + 1:]}"
    assert decode_access_token(tampered) is None


def test_garbage_string_rejected():
    assert decode_access_token("not.a.jwt") is None
    assert decode_refresh_token("not.a.jwt") is None
    assert decode_access_token("") is None


# ── Strict decoder: TokenExpired vs TokenInvalid distinction ─────────────────


def test_strict_decoder_round_trip_access():
    tok = create_access_token(user_id=7, is_admin=False)
    payload = decode_token_strict(tok, expected_type="access")
    assert payload["sub"] == "7"


def test_strict_decoder_round_trip_refresh():
    tok = create_refresh_token(user_id=7)
    payload = decode_token_strict(tok, expected_type="refresh")
    assert payload["sub"] == "7"


def test_strict_decoder_raises_token_invalid_on_wrong_type():
    """Access token passed to a refresh-expected decoder -> TokenInvalid."""
    import pytest

    access = create_access_token(user_id=1, is_admin=False)
    with pytest.raises(TokenInvalid, match="Wrong token type"):
        decode_token_strict(access, expected_type="refresh")


def test_strict_decoder_raises_token_invalid_on_malformed():
    import pytest

    with pytest.raises(TokenInvalid):
        decode_token_strict("not-a-real-jwt", expected_type="access")


def test_strict_decoder_raises_token_invalid_on_tampered_signature():
    import pytest

    tok = create_access_token(user_id=1, is_admin=False)
    # See note in test_tampered_signature_rejected — flip a middle byte.
    head, _, sig = tok.rpartition(".")
    mid = len(sig) // 2
    new_char = "A" if sig[mid] != "A" else "B"
    tampered = f"{head}.{sig[:mid]}{new_char}{sig[mid + 1:]}"
    with pytest.raises(TokenInvalid):
        decode_token_strict(tampered, expected_type="access")


def test_strict_decoder_raises_token_expired_on_expired_token():
    """Forge an already-expired token by passing a past `exp` claim."""
    import pytest
    from datetime import datetime, timedelta, timezone
    from jose import jwt
    from core.config import settings

    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    expired = jwt.encode(
        {"sub": "1", "type": "access", "exp": past, "iat": past},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    with pytest.raises(TokenExpired):
        decode_token_strict(expired, expected_type="access")
