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


async def run_migrations() -> None:
    """
    Apply Alembic migrations to bring the database up to date.

    Three startup states are handled idempotently:

    1. Fresh database (no tables): `alembic upgrade head` runs the
       baseline migration, which creates the pgvector extension, every
       table, and the HNSW index.

    2. Pre-Alembic database (tables exist, no alembic_version row):
       The schema was created by an older entrypoint via
       Base.metadata.create_all. We stamp the baseline as applied
       *without* running it — otherwise the migration would try to
       CREATE TABLE on tables that already exist. After stamping, any
       subsequent migrations layered on top of baseline still run.

    3. Alembic-managed database: just upgrade head, which is a no-op
       when already at the latest revision.

    Running this synchronously inside the otherwise-async entrypoint is
    fine — Alembic owns its own DB connections via the URL in
    alembic/env.py, separate from the asyncpg pool the app uses.
    """
    print("Applying database migrations...", flush=True)

    conn = await _plain_connect()
    try:
        alembic_managed = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'alembic_version')"
        )
        users_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'users')"
        )
    finally:
        await conn.close()

    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "alembic.ini"))

    if not alembic_managed and users_exists:
        print("  [info] Pre-Alembic database detected; stamping baseline",
              flush=True)
        command.stamp(cfg, "head")
        print("  [ok] Baseline stamped", flush=True)
        return

    command.upgrade(cfg, "head")
    print("  [ok] Migrations applied", flush=True)


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


# Passwords that ship in templates / docs / first-run examples.  We refuse
# to seed any of these when DEBUG=false so a forgotten config can't bring
# up a publicly reachable admin with a guessable password.
_WEAK_ADMIN_PASSWORDS = {"Admin123!", "ChangeMe123!", "Password1", "admin", ""}


async def seed_admin() -> None:
    print("Seeding superuser...", flush=True)
    from core.config import settings
    from core.exceptions import ConflictError
    from db.repositories.user_repository import UserRepository
    from db.session import AsyncSessionLocal

    username = os.getenv("ADMIN_USERNAME", "admin")
    email    = os.getenv("ADMIN_EMAIL",    "admin@company.com")
    password = os.getenv("ADMIN_PASSWORD", "Admin123!")

    # Fail-closed in production: refuse to seed a known-weak default.
    # In DEBUG mode we warn and continue so local dev/demo still works.
    if password in _WEAK_ADMIN_PASSWORDS:
        msg = (
            "ADMIN_PASSWORD is unset or matches a known-weak default. "
            "Set a strong ADMIN_PASSWORD in the environment before "
            "starting the container."
        )
        if not settings.DEBUG:
            print(f"  [fatal] {msg}", flush=True)
            sys.exit(1)
        print(f"  [warn] {msg}  (DEBUG=true — seeding anyway)", flush=True)

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        try:
            user = await repo.create_user(
                username=username,
                email=email,
                plain_password=password,
                is_superuser=True,
                role_assignments=None,
            )
            await session.commit()
            print(f"  [ok] Superuser created: {user.username}", flush=True)
        except ConflictError:
            print(f"  [skip] Superuser '{username}' already exists", flush=True)


# Starter roles seeded only on fresh installs so a brand-new deployment
# has something to play with out-of-the-box. On upgrades from the pre-
# Alembic schema, the 0002 migration already created roles for whatever
# legacy values were in users.role / document_roles.role, so we skip
# seeding if any rows already exist.
_STARTER_ROLES = [
    {
        "name": "hr",
        "display_name": "HR",
        "persona_name": "Morgan",
        "domain_label": "HR",
        "scope_description": (
            "HR policies, onboarding, benefits, payroll, leave, compliance, "
            "employee relations, and other HR documentation."
        ),
        "general_knowledge_scope": (
            "HR concepts, people management, employment law basics, "
            "onboarding best practices, workplace culture, and team dynamics."
        ),
    },
    {
        "name": "developer",
        "display_name": "Engineering",
        "persona_name": "Dev",
        "domain_label": "Engineering",
        "scope_description": (
            "codebases, repositories, development workflows, architecture, "
            "tooling, deployments, and engineering practices."
        ),
        "general_knowledge_scope": (
            "software engineering, programming languages, system design, "
            "DevOps, databases, testing, security, and APIs."
        ),
    },
]


async def seed_starter_roles() -> None:
    print("Seeding starter roles...", flush=True)
    from db.repositories.role_repository import RoleRepository
    from db.session import AsyncSessionLocal
    from sqlalchemy import select, func as sqlfunc
    from db.models import Role

    async with AsyncSessionLocal() as session:
        existing = await session.execute(select(sqlfunc.count()).select_from(Role))
        if existing.scalar_one() > 0:
            print("  [skip] roles table already populated", flush=True)
            return

        repo = RoleRepository(session)
        for spec in _STARTER_ROLES:
            await repo.create_role(**spec)
        await session.commit()
        print(
            f"  [ok] Created {len(_STARTER_ROLES)} starter role(s): "
            f"{', '.join(r['name'] for r in _STARTER_ROLES)}",
            flush=True,
        )


async def main() -> None:
    print("\n========================================", flush=True)
    print("  Mnema - Docker startup", flush=True)
    print("========================================\n", flush=True)
    await wait_for_postgres()
    await run_migrations()
    await wait_for_ollama()
    await seed_admin()
    await seed_starter_roles()
    print("\n  [ok] Startup complete - launching app\n", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
