"""
Tests for refresh-token revocation.

  1. create_refresh_token embeds a unique jti.
  2. RevokedTokenRepository denylists jti values idempotently and reports
     membership (the basis for logout-invalidation + rotation-reuse).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import security
from db.models import Base, User
from db.repositories.token_repository import RevokedTokenRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(User(id=1, username="u", email="u@x.io", hashed_password="x"))
        await s.commit()
        yield s
    await engine.dispose()


def test_refresh_token_has_unique_jti():
    t1 = security.create_refresh_token(1)
    t2 = security.create_refresh_token(1)
    p1 = security.decode_refresh_token(t1)
    p2 = security.decode_refresh_token(t2)
    assert p1["jti"] and p2["jti"]
    assert p1["jti"] != p2["jti"]


def test_access_token_has_no_jti():
    # Only refresh tokens are denylisted; access tokens are short-lived.
    payload = security.decode_access_token(security.create_access_token(1, False))
    assert "jti" not in payload


async def test_revoke_then_is_revoked(session):
    repo = RevokedTokenRepository(session)
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    assert await repo.is_revoked("abc") is False
    await repo.revoke("abc", 1, exp)
    await session.commit()
    assert await repo.is_revoked("abc") is True


async def test_revoke_is_idempotent(session):
    repo = RevokedTokenRepository(session)
    exp = datetime.now(timezone.utc) + timedelta(days=7)
    await repo.revoke("abc", 1, exp)
    await repo.revoke("abc", 1, exp)  # must not raise on PK conflict
    await session.commit()
    assert await repo.is_revoked("abc") is True


async def test_purge_expired(session):
    repo = RevokedTokenRepository(session)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    await repo.revoke("old", 1, past)
    await repo.revoke("new", 1, future)
    await session.commit()
    purged = await repo.purge_expired()
    await session.commit()
    assert purged == 1
    assert await repo.is_revoked("old") is False
    assert await repo.is_revoked("new") is True
