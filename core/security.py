import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from passlib.context import CryptContext

from core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Typed token errors ────────────────────────────────────────────────────────
# decode_*_token return None on any failure; these subclasses let callers
# distinguish "expired, please refresh" from "tampered, log out".

class TokenError(Exception):
    """Base class for JWT decode failures."""


class TokenExpired(TokenError):
    """Signature was valid but the `exp` claim is in the past."""


class TokenInvalid(TokenError):
    """
    Signature, structure, or claim-type check failed. Anything that's not a
    legitimate expiry: forged signature, malformed JWT, wrong token type.
    """


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT ───────────────────────────────────────────────────────────────────────

def _create_token(data: dict[str, Any], expires_delta: timedelta) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    payload.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(user_id: int, is_superuser: bool) -> str:
    # is_superuser is informational only — every privileged endpoint re-reads
    # the User row, so this claim can't escalate. Used for client-side UI gating.
    return _create_token(
        data={"sub": str(user_id), "is_superuser": is_superuser, "type": "access"},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: int) -> str:
    # Unique jti per token enables individual revocation: the refresh flow
    # rotates tokens and denylists consumed jtis (logout + reuse detection).
    return _create_token(
        data={"sub": str(user_id), "type": "refresh", "jti": uuid.uuid4().hex},
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token_strict(token: str, expected_type: str) -> dict[str, Any]:
    """
    Strict decoder: raises TokenExpired / TokenInvalid with no ambiguity.

    Callers that need to differentiate "expired, please refresh" from
    "log out immediately" should use this. The older decode_*_token helpers
    still return None on any failure for backward compatibility.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except ExpiredSignatureError as exc:
        raise TokenExpired("Token has expired") from exc
    except JWTError as exc:
        raise TokenInvalid(f"Invalid token: {exc}") from exc

    if payload.get("type") != expected_type:
        raise TokenInvalid(
            f"Wrong token type: expected {expected_type!r}, got {payload.get('type')!r}"
        )
    return payload


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        return None


def decode_access_token(token: str) -> dict[str, Any] | None:
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def decode_refresh_token(token: str) -> dict[str, Any] | None:
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "refresh":
        return None
    return payload
