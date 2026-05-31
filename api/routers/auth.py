import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from api.schemas.user import UserOut
from core.config import settings
from core.exceptions import UnauthorizedError
from core.limiter import limiter
from core.security import (
    TokenExpired,
    TokenInvalid,
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_token_strict,
)
from db.repositories.token_repository import RevokedTokenRepository
from db.repositories.user_repository import UserRepository
from db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_ACCESS_MAX_AGE = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
_REFRESH_MAX_AGE = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Write both tokens as HttpOnly cookies.  JS can never read these values."""
    cookie_kwargs = dict(
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
    )
    response.set_cookie(
        key="access_token",
        value=access_token,
        max_age=_ACCESS_MAX_AGE,
        **cookie_kwargs,
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=_REFRESH_MAX_AGE,
        # Scoped so the refresh token is only sent to the refresh endpoint
        path="/auth/refresh",
        **cookie_kwargs,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    repo = UserRepository(session)
    user = await repo.authenticate(body.username, body.password)
    if user is None:
        logger.warning("Failed login attempt for username=%s ip=%s", body.username, request.client)
        raise UnauthorizedError("Invalid username or password")

    access_token = create_access_token(user.id, user.is_superuser)
    refresh_token = create_refresh_token(user.id)
    _set_auth_cookies(response, access_token, refresh_token)

    # Also return tokens in the body so non-browser API clients still work
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
async def refresh_token(
    request: Request,
    response: Response,
    body: RefreshRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    """
    Mint a fresh access + refresh token pair.

    Token sources, in order:
      1. HttpOnly cookie `refresh_token` (the browser flow).
      2. JSON body `{"refresh_token": "..."}` (Postman / curl / other API
         clients that don't speak cookies).

    Returns:
      - 401 with `detail="Refresh token expired"` — the cookie/body was a
        legitimate token whose `exp` has passed. Frontend should send the
        user to the login page (a re-login is the only recovery).
      - 401 with `detail="Invalid refresh token"` — anything else (no
        token, malformed, tampered signature, wrong type). Same UX as
        expired, but a distinct log line helps incident response.
    """
    # 1. Prefer HttpOnly cookie — JS cannot read it, so this is the safer source.
    token = request.cookies.get("refresh_token")

    # 2. Fall back to the body for non-browser API clients.
    if not token and body is not None:
        token = body.refresh_token

    if not token:
        raise UnauthorizedError("No refresh token provided")

    try:
        payload = decode_token_strict(token, expected_type="refresh")
    except TokenExpired:
        logger.info("Refresh attempted with expired token")
        raise UnauthorizedError("Refresh token expired")
    except TokenInvalid as exc:
        # Don't echo back the raw jose error to the client — that can leak
        # internal details. Log it server-side; return a generic message.
        logger.warning("Invalid refresh token: %s", exc)
        raise UnauthorizedError("Invalid refresh token")

    user_id = int(payload["sub"])
    jti = payload.get("jti")
    denylist = RevokedTokenRepository(session)

    # Rotation-reuse detection: a denylisted jti means the token was logged out
    # or already rotated (a replay) → reject. Legacy tokens without jti pass once.
    if jti is not None and await denylist.is_revoked(jti):
        logger.warning(
            "Refresh-token reuse detected for user %s (jti=%s)", user_id, jti
        )
        raise UnauthorizedError("Invalid refresh token")

    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found")

    # Consume the presented token: record its jti so it can't be replayed.
    if jti is not None:
        exp = payload.get("exp")
        expires_at = (
            datetime.fromtimestamp(exp, tz=timezone.utc)
            if exp is not None
            else datetime.now(timezone.utc)
        )
        await denylist.revoke(jti, user_id, expires_at)

    access_token = create_access_token(user.id, user.is_superuser)
    new_refresh = create_refresh_token(user.id)
    _set_auth_cookies(response, access_token, new_refresh)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Clear both auth cookies and revoke the refresh token.

    Clearing the cookie alone left a stolen refresh token valid for its
    full lifetime. We now also denylist its jti so it can't be replayed.
    Best-effort: a missing/garbage cookie still returns 204 (logout must
    never fail for the user).
    """
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/auth/refresh")

    token = request.cookies.get("refresh_token")
    if not token:
        return
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh":
        return
    jti = payload.get("jti")
    if jti is None:
        return
    exp = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(exp, tz=timezone.utc)
        if exp is not None
        else datetime.now(timezone.utc)
    )
    try:
        denylist = RevokedTokenRepository(session)
        await denylist.revoke(jti, int(payload["sub"]), expires_at)
    except Exception:
        logger.exception("Failed to revoke refresh token on logout")


@router.get("/me", response_model=UserOut)
async def get_me(
    current_user=Depends(get_current_user),
) -> UserOut:
    # from_orm_user projects role_memberships -> MembershipOut. The
    # repository already eager-loaded memberships in get_by_id, so this
    # never triggers lazy IO.
    return UserOut.from_orm_user(current_user)
