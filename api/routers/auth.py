import logging

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from api.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from api.schemas.user import UserOut
from core.config import settings
from core.exceptions import UnauthorizedError
from core.limiter import limiter
from core.security import create_access_token, create_refresh_token, decode_refresh_token
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
    body: RefreshRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    payload = decode_refresh_token(body.refresh_token)
    if payload is None:
        raise UnauthorizedError("Invalid or expired refresh token")

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
