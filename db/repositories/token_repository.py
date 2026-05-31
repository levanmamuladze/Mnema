from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RevokedToken


class RevokedTokenRepository:
    """Server-side refresh-token denylist (logout + rotation-reuse)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_revoked(self, jti: str) -> bool:
        result = await self.session.execute(
            select(RevokedToken.jti).where(RevokedToken.jti == jti)
        )
        return result.scalar_one_or_none() is not None

    async def revoke(self, jti: str, user_id: int, expires_at: datetime) -> None:
        """Add a jti to the denylist. Idempotent — re-revoking is a no-op.

        Uses INSERT ... ON CONFLICT DO NOTHING so a double logout (or a
        race between logout and refresh) can't raise on the PK.
        """
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as _insert
        else:
            from sqlalchemy.dialects.sqlite import insert as _insert
        stmt = (
            _insert(RevokedToken)
            .values(jti=jti, user_id=user_id, expires_at=expires_at)
            .on_conflict_do_nothing(index_elements=["jti"])
        )
        await self.session.execute(stmt)

    async def purge_expired(self) -> int:
        """Delete denylist rows whose underlying token has already expired."""
        result = await self.session.execute(
            delete(RevokedToken).where(
                RevokedToken.expires_at < datetime.now(timezone.utc)
            )
        )
        return result.rowcount or 0
