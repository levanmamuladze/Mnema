from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ForbiddenError, UnauthorizedError
from core.security import decode_access_token
from db.models import User
from db.repositories.user_repository import UserRepository
from db.session import get_session

# Keep the Bearer scheme for non-browser API clients (curl, Postman, etc.)
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    # 1. Prefer HttpOnly cookie (browser clients) — JS can never read or steal this
    token = request.cookies.get("access_token")

    # 2. Fall back to Authorization: Bearer header (API clients, Postman, etc.)
    if not token and credentials is not None:
        token = credentials.credentials

    if not token:
        raise UnauthorizedError("No token provided")

    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedError("Invalid or expired token")

    user_id = int(payload["sub"])
    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)

    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")

    return user


async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise ForbiddenError("Admin access required")
    return current_user
