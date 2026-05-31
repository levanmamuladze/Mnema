"""
Route-level tests for POST /auth/refresh — verify the cookie-first flow,
body fallback, and that the strict decoder gives clearer error messages.

We don't spin up the full app (its lifespan needs a real Postgres). Instead
we mount just the auth router and override get_session with a stub. We then
patch UserRepository in api.routers.auth to return a stand-in user.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt

from core.config import settings
from core.security import create_refresh_token


class _StubUser:
    def __init__(self, user_id: int = 1, is_active: bool = True, is_superuser: bool = False):
        self.id = user_id
        self.is_active = is_active
        self.is_superuser = is_superuser


@pytest.fixture
def app(monkeypatch):
    """A minimal FastAPI app with only the auth router and stubbed deps."""
    from api.routers import auth as auth_router_module
    from db.session import get_session

    app = FastAPI()
    app.include_router(auth_router_module.router)

    # The router only touches the session via UserRepository, which we mock.
    async def _fake_session():
        yield None

    app.dependency_overrides[get_session] = _fake_session

    # Default to an active user (happy path); tests override .get_by_id as needed.
    stub_repo = AsyncMock()
    stub_repo.get_by_id = AsyncMock(return_value=_StubUser())

    def _make_repo(_session):
        return stub_repo

    monkeypatch.setattr(auth_router_module, "UserRepository", _make_repo)

    # Stub the refresh-token denylist (DB session is None here): nothing is
    # revoked. Reuse tests override RevokedTokenRepository themselves.
    stub_denylist = AsyncMock()
    stub_denylist.is_revoked = AsyncMock(return_value=False)
    stub_denylist.revoke = AsyncMock(return_value=None)
    monkeypatch.setattr(
        auth_router_module, "RevokedTokenRepository", lambda _session: stub_denylist
    )
    return app, stub_repo


@pytest.fixture
async def client(app):
    """An httpx AsyncClient bound to the test app via ASGITransport."""
    fastapi_app, _ = app
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as ac:
        yield ac


# ── Token source precedence ──────────────────────────────────────────────────


async def test_refresh_uses_cookie_when_present(client):
    rt = create_refresh_token(user_id=1)
    resp = await client.post("/auth/refresh", cookies={"refresh_token": rt})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"


async def test_refresh_falls_back_to_body_when_no_cookie(client):
    rt = create_refresh_token(user_id=1)
    resp = await client.post("/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 200, resp.text


async def test_refresh_prefers_cookie_over_body(client):
    """If both are present, the cookie wins. The body token is ignored entirely."""
    cookie_rt = create_refresh_token(user_id=1)
    # Body holds a STRUCTURALLY VALID but wrong-signature token. If the route
    # were reading from body, this would 401. Cookie-first means we never see it.
    body_rt = "header.payload.bogussig"
    resp = await client.post(
        "/auth/refresh",
        cookies={"refresh_token": cookie_rt},
        json={"refresh_token": body_rt},
    )
    assert resp.status_code == 200, resp.text


# ── Error paths ──────────────────────────────────────────────────────────────


async def test_refresh_returns_401_with_no_token(client):
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401
    assert "No refresh token" in resp.json()["detail"]


async def test_refresh_returns_specific_expired_message(client):
    """An expired refresh token gets `detail="Refresh token expired"`."""
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    expired = jwt.encode(
        {"sub": "1", "type": "refresh", "exp": past, "iat": past},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    resp = await client.post("/auth/refresh", cookies={"refresh_token": expired})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Refresh token expired"


async def test_refresh_returns_invalid_for_garbage(client):
    resp = await client.post("/auth/refresh", cookies={"refresh_token": "not-a-jwt"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


async def test_refresh_returns_invalid_when_access_token_used(client):
    """An access token presented as a refresh token must be rejected."""
    from core.security import create_access_token

    access = create_access_token(user_id=1, is_superuser=False)
    resp = await client.post("/auth/refresh", cookies={"refresh_token": access})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


async def test_refresh_rejects_reused_token(app, client, monkeypatch):
    """A refresh token whose jti is already on the denylist (logged out or
    already rotated) is rejected as a replay."""
    from api.routers import auth as auth_router_module

    reuse_denylist = AsyncMock()
    reuse_denylist.is_revoked = AsyncMock(return_value=True)
    reuse_denylist.revoke = AsyncMock(return_value=None)
    monkeypatch.setattr(
        auth_router_module, "RevokedTokenRepository", lambda _s: reuse_denylist
    )

    rt = create_refresh_token(user_id=1)
    resp = await client.post("/auth/refresh", cookies={"refresh_token": rt})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid refresh token"


async def test_refresh_returns_401_for_inactive_user(app, client):
    """User exists but is_active=False — same 401 surface as 'no token'."""
    _, stub_repo = app
    stub_repo.get_by_id = AsyncMock(return_value=_StubUser(is_active=False))

    rt = create_refresh_token(user_id=1)
    resp = await client.post("/auth/refresh", cookies={"refresh_token": rt})
    assert resp.status_code == 401


async def test_refresh_sets_new_cookies_on_success(client):
    rt = create_refresh_token(user_id=1)
    resp = await client.post("/auth/refresh", cookies={"refresh_token": rt})
    assert resp.status_code == 200

    # httpx exposes Set-Cookie headers as a list on resp.headers
    set_cookie = resp.headers.get_list("set-cookie")
    cookie_blob = "\n".join(set_cookie)
    assert "access_token=" in cookie_blob
    assert "refresh_token=" in cookie_blob
    # Refresh cookie is path-scoped so it's only sent back to /auth/refresh.
    assert "Path=/auth/refresh" in cookie_blob
    # HttpOnly is the whole point of this design — verify it's there.
    assert "HttpOnly" in cookie_blob
