from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.config import settings

# No register_vector here — pgvector.sqlalchemy.Vector handles encoding/decoding
# via SQLAlchemy's text protocol. Registering the asyncpg codec on top of that
# causes a conflict: Vector.process_bind_param returns a string '[-0.04,...]',
# then asyncpg's codec tries to binary-encode that string and fails.
# The asyncpg codec is only needed for direct asyncpg usage (docker_entrypoint.py).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """Create all tables. Use Alembic migrations in production."""
    from db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
