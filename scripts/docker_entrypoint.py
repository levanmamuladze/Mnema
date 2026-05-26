"""
Docker entrypoint — runs on every container startup.

All steps are idempotent: safe to run on every restart.
Order: wait for postgres → enable pgvector → create tables → create index
       → wait for ollama → verify model → seed admin → exec uvicorn.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_db_url():
    from core.config import settings
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "")
    user_pass, host_db = url.split("@", 1)
    db_user, db_pass = user_pass.split(":", 1)
    host_port, db_name = host_db.split("/", 1)
    parts = host_port.split(":")
    host = parts[0]
    port = int(parts[1]) if len(parts) > 1 else 5432
    return host, port, db_user, db_pass, db_name


async def _plain_connect():
    import asyncpg
    host, port, db_user, db_pass, db_name = _parse_db_url()
    return await asyncpg.connect(host=host, port=port, user=db_user, password=db_pass, database=db_name)


async def wait_for_postgres(max_attempts: int = 30, delay: float = 2.0) -> None:
    print("Waiting for PostgreSQL...", flush=True)
    for attempt in range(1, max_attempts + 1):
        try:
            conn = await _plain_connect()
            await conn.close()
            print(f"  [ok] PostgreSQL ready (attempt {attempt})", flush=True)
            return
        except Exception as e:
            print(f"  [{attempt}/{max_attempts}] Not ready: {e}", flush=True)
            await asyncio.sleep(delay)
    print("PostgreSQL did not become ready. Exiting.", flush=True)
    sys.exit(1)


async def enable_vector_extension() -> None:
    print("Enabling pgvector extension...", flush=True)
    conn = await _plain_connect()
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        print("  [ok] pgvector extension enabled", flush=True)
    finally:
        await conn.close()


async def create_tables() -> None:
    print("Creating/verifying tables...", flush=True)
    from db.session import init_db
    await init_db()
    print("  [ok] Tables ready", flush=True)


async def create_vector_index() -> None:
    print("Creating HNSW vector index...", flush=True)
    from pgvector.asyncpg import register_vector
    conn = await _plain_connect()
    try:
        await register_vector(conn)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)
        print("  [ok] HNSW index ready", flush=True)
    finally:
        await conn.close()


async def wait_for_ollama(max_attempts: int = 60, delay: float = 2.0) -> None:
    """
    Block until the Ollama daemon is serving HTTP and the configured model
    is loaded. Without this, the first /api/chat call from a real user can
    race past the daemon's startup and return ConnectError.
    """
    import httpx
    from core.config import settings

    base = settings.OLLAMA_BASE_URL.rstrip("/")
    tags_url = f"{base}/api/tags"
    print(f"Waiting for Ollama at {base} ...", flush=True)

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(tags_url)
                if resp.status_code == 200:
                    models = [m.get("name", "") for m in resp.json().get("models", [])]
                    target = settings.OLLAMA_MODEL
                    if any(name == target or name.startswith(target + ":") for name in models):
                        print(f"  [ok] Ollama ready, model present: {target}", flush=True)
                        return
                    print(
                        f"  [{attempt}/{max_attempts}] Ollama up but model "
                        f"{target!r} not loaded yet (have: {models or 'none'})",
                        flush=True,
                    )
                else:
                    print(
                        f"  [{attempt}/{max_attempts}] Ollama responded HTTP "
                        f"{resp.status_code}",
                        flush=True,
                    )
        except Exception as e:
            print(f"  [{attempt}/{max_attempts}] Ollama not ready: {e}", flush=True)
        await asyncio.sleep(delay)

    # Don't hard-fail — let the app start anyway. Chat calls will surface
    # the error to the user with the new pipeline error frames. This keeps
    # the API surface (auth, admin) usable even if the LLM is down.
    print(
        "  [warn] Ollama / model not confirmed after wait window. "
        "App will start; chat may fail until model loads.",
        flush=True,
    )


async def seed_admin() -> None:
    print("Seeding admin user...", flush=True)
    from core.exceptions import ConflictError
    from db.repositories.user_repository import UserRepository
    from db.session import AsyncSessionLocal

    username = os.getenv("ADMIN_USERNAME", "admin")
    email    = os.getenv("ADMIN_EMAIL",    "admin@company.com")
    password = os.getenv("ADMIN_PASSWORD", "Admin123!")

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        try:
            user = await repo.create_user(username=username, email=email,
                                          plain_password=password, is_admin=True)
            await session.commit()
            print(f"  [ok] Admin created: {user.username}", flush=True)
        except ConflictError:
            print(f"  [skip] Admin '{username}' already exists", flush=True)


async def main() -> None:
    print("\n========================================", flush=True)
    print("  Onboarding AI - Docker startup", flush=True)
    print("========================================\n", flush=True)
    await wait_for_postgres()
    await enable_vector_extension()
    await create_tables()
    await create_vector_index()
    await wait_for_ollama()
    await seed_admin()
    print("\n  [ok] Startup complete - launching app\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
