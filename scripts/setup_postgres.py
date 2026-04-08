"""
One-time PostgreSQL setup script.
Run this ONCE after installing PostgreSQL and BEFORE starting the app.

Usage:
    python scripts/setup_postgres.py
    python scripts/setup_postgres.py --password mypassword

What it does:
    1. Connects as the postgres superuser
    2. Creates the application database and user
    3. Grants privileges
    4. Enables the pgvector extension
    5. Creates all tables
    6. Creates the HNSW vector index for fast similarity search
    7. Seeds the first admin user
"""
import argparse
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Step 1: Create DB + user + extension as superuser ──────────────────────────

async def setup_database(pg_password: str, app_password: str) -> None:
    import asyncpg

    print("\n[1/5] Connecting as postgres superuser...")
    try:
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password=pg_password,
            database="postgres",
        )
    except Exception as e:
        print(f"\n❌ Could not connect as postgres superuser: {e}")
        print("\nMake sure:")
        print("  • PostgreSQL 17 is installed and running")
        print("  • The postgres superuser password matches what you set during installation")
        print("  • Run as: python scripts/setup_postgres.py --pg-password YOUR_POSTGRES_PASSWORD")
        sys.exit(1)

    print("[2/5] Creating database and application user...")
    # Use DO blocks to handle 'already exists' gracefully
    await conn.execute(f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'onboarding_user') THEN
                CREATE ROLE onboarding_user WITH LOGIN PASSWORD '{app_password}';
            ELSE
                ALTER ROLE onboarding_user WITH PASSWORD '{app_password}';
            END IF;
        END $$;
    """)

    # Check if DB exists
    exists = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = 'onboarding_ai'"
    )
    if not exists:
        await conn.execute("CREATE DATABASE onboarding_ai OWNER onboarding_user")
        print("   ✅ Created database: onboarding_ai")
    else:
        print("   ℹ️  Database onboarding_ai already exists — skipping creation")

    await conn.execute(
        "GRANT ALL PRIVILEGES ON DATABASE onboarding_ai TO onboarding_user"
    )
    await conn.close()

    print("[3/5] Enabling pgvector extension...")
    # Connect to the application database to install the extension
    conn2 = await asyncpg.connect(
        host="localhost",
        port=5432,
        user="postgres",
        password=pg_password,
        database="onboarding_ai",
    )
    await conn2.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn2.execute(
        "GRANT ALL ON SCHEMA public TO onboarding_user"
    )
    await conn2.close()
    print("   ✅ pgvector extension enabled")


# ── Step 2: Create tables via SQLAlchemy ───────────────────────────────────────

async def create_tables() -> None:
    print("[4/5] Creating tables...")
    from db.session import init_db
    await init_db()
    print("   ✅ All tables created")


# ── Step 3: Create HNSW index for fast vector search ─────────────────────────

async def create_vector_index() -> None:
    """
    HNSW index on document_chunks.embedding using cosine distance.

    HNSW (Hierarchical Navigable Small World) gives approximate nearest
    neighbour search in O(log n) — stays fast even with millions of chunks.

    m=16       : connections per node (higher = better recall, more memory)
    ef_construction=64 : search depth during build (higher = better recall, slower build)

    This index can be created on an empty table and will auto-update as rows
    are inserted — no need to rebuild after uploading documents.
    """
    print("   Creating HNSW index on document_chunks.embedding...")
    import asyncpg
    from core.config import settings

    # Parse user/password from DATABASE_URL
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "")
    user_pass, host_db = url.split("@")
    db_user, db_pass = user_pass.split(":", 1)
    host_port, db_name = host_db.split("/", 1)
    host, port = (host_port.split(":") + ["5432"])[:2]

    conn = await asyncpg.connect(
        host=host, port=int(port), user=db_user, password=db_pass, database=db_name
    )
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)
    await conn.close()
    print("   ✅ HNSW vector index created")


# ── Step 4: Seed admin user ────────────────────────────────────────────────────

async def seed_admin(username: str, email: str, password: str) -> None:
    print("[5/5] Creating admin user...")
    from db.session import AsyncSessionLocal
    from db.repositories.user_repository import UserRepository
    from core.exceptions import ConflictError

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        try:
            user = await repo.create_user(
                username=username,
                email=email,
                plain_password=password,
                is_admin=True,
            )
            await session.commit()
            print(f"   ✅ Admin created: {user.username} (id={user.id})")
        except ConflictError:
            print(f"   ℹ️  Admin user '{username}' already exists — skipping")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(pg_password: str, app_password: str,
               admin_username: str, admin_email: str, admin_password: str) -> None:
    print("=" * 55)
    print("  PostgreSQL setup for Onboarding AI")
    print("=" * 55)

    await setup_database(pg_password, app_password)
    await create_tables()
    await create_vector_index()
    await seed_admin(admin_username, admin_email, admin_password)

    print("\n" + "=" * 55)
    print("  ✅ Setup complete!")
    print("=" * 55)
    print(f"\n  Database : onboarding_ai")
    print(f"  DB user  : onboarding_user")
    print(f"  Admin    : {admin_username} / {admin_password}")
    print(f"\n  Start the app:")
    print(f"  uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
    print(f"\n  Swagger UI: http://localhost:8000/api/docs\n")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Set up PostgreSQL for Onboarding AI")
    parser.add_argument(
        "--pg-password", default="postgres",
        help="Password for the postgres superuser (set during PG installation)"
    )
    parser.add_argument(
        "--app-password", default="onboarding_pass",
        help="Password for the onboarding_user DB account (default: onboarding_pass)"
    )
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--admin-email", default="admin@company.com")
    parser.add_argument("--admin-password", default="Admin123!")
    args = parser.parse_args()

    asyncio.run(main(
        pg_password=args.pg_password,
        app_password=args.app_password,
        admin_username=args.admin_username,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
    ))


if __name__ == "__main__":
    cli()
