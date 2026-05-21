import logging

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
    decode_token_strict,
)
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

    access_token = create_access_token(user.id, user.is_admin)
    refresh_token = create_refresh_token(user.id)
    _set_auth_cookies(response, access_token, refresh_token)

    # Also return tokens in the body so non-browser API clients still work
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
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

    repo = UserRepository(session)
    user = await repo.get_by_id(int(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found")

    access_token = create_access_token(user.id, user.is_admin)
    new_refresh = create_refresh_token(user.id)
    _set_auth_cookies(response, access_token, new_refresh)

    return TokenResponse(access_token=access_token, refresh_token=new_refresh)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    """Clear both auth cookies.  Works regardless of whether the user is logged in."""
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token", path="/auth/refresh")


@router.get("/me", response_model=UserOut)
async def get_me(
    current_user=Depends(get_current_user),
) -> UserOut:
    return UserOut.model_validate(current_user)
