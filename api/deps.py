from collections.abc import Callable

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


async def require_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Reject the request unless the caller has the global superuser flag.

    Superusers bypass every RBAC check; they're the people responsible
    for managing roles, role-admins, and other superusers.
    """
    if not current_user.is_superuser:
        raise ForbiddenError("Superuser access required")
    return current_user


async def require_uploader(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Reject the request unless the caller can upload/manage documents.

    Eligible callers: superusers (full access) and any user that holds
    `is_role_admin=True` on at least one role. Per-endpoint logic must
    still verify the *specific* role_ids the caller is acting on are
    within `current_user.admin_role_ids` (or skip that check for
    superusers).
    """
    if current_user.is_superuser:
        return current_user
    if not current_user.admin_role_ids:
        raise ForbiddenError("Role-admin or superuser access required")
    return current_user


def require_role_admin_of(*, role_id_param: str = "role_id") -> Callable:
    """
    Build a dependency that checks the caller is a role-admin of the
    role referenced by a path parameter.

    Usage:

        @router.put("/admin/roles/{role_id}")
        async def edit_role(
            role_id: int,
            actor: User = Depends(require_role_admin_of()),
        ): ...

    Superusers always satisfy this check (via User.is_admin_of, which
    short-circuits on is_superuser).
    """
    async def _dep(
        request: Request,
        current_user: User = Depends(get_current_user),
    ) -> User:
        raw = request.path_params.get(role_id_param)
        if raw is None:
            # Misconfigured route — 403 not 500 so we don't leak structure.
            raise ForbiddenError("Role context required")
        try:
            role_id = int(raw)
        except (TypeError, ValueError):
            raise ForbiddenError("Role context required")
        if not current_user.is_admin_of(role_id):
            raise ForbiddenError("Role-admin access required")
        return current_user

    return _dep
