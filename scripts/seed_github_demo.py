"""
One-shot bootstrap of the bundled GitHub demo knowledge base.

Purpose: a small, *verifiable* dataset for presenting Mnema. It mixes
well-known GitHub facts (the audience can confirm answers) with a handful
of planted, company-specific "needle" facts that the base LLM cannot know
(proving answers come from RAG, not the model's memory).

What this script does (it is destructive — it resets demo state):

  1. Strips role memberships from superusers (a superuser needs none).
  2. Truncates documents, chunks, document_roles, conversations, messages.
  3. Deletes every NON-superuser user (clears any previous demo users).
  4. Deletes every role, then recreates the three GitHub demo roles:
     everyone, engineering, devops.
  5. Creates the GitHub demo users (all share password `Mnema123!`).
  6. Ingests `github/` — each file assigned to a role by its folder.

The `admin` superuser and its credentials are preserved.

Run inside the running app container:

    docker exec -it mnema_app python scripts/seed_github_demo.py

Use --dry-run to print the planned uploads without touching the DB.
"""
import argparse
import asyncio
import mimetypes
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete as sql_delete
from sqlalchemy import select, text

from core.config import settings
from db.models import DocumentRole, Role, User, UserRole
from db.repositories.document_repository import DocumentRepository
from db.repositories.document_role_repository import DocumentRoleRepository
from db.repositories.role_repository import RoleRepository
from db.repositories.user_repository import UserRepository
from db.session import AsyncSessionLocal, init_db
from services.ingestion_service import IngestionService


SUPPORTED_EXTENSIONS = {
    ".pdf", ".txt", ".md", ".docx", ".csv", ".tsv", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".java", ".go", ".rs", ".rb", ".php", ".cs", ".cpp", ".c", ".h", ".hpp",
    ".kt", ".swift", ".scala", ".sql", ".sh", ".bash", ".zsh", ".yaml", ".yml",
    ".json", ".jsonl", ".ndjson", ".xml", ".html", ".css", ".toml", ".ini", ".cfg",
    ".log", ".rst", ".tex", ".env",
}


ROLE_DEFINITIONS: list[dict[str, str]] = [
    {
        "name": "everyone",
        "display_name": "Everyone",
        "persona_name": "Hub",
        "domain_label": "general GitHub knowledge",
        "scope_description": (
            "what GitHub is, the difference between Git and GitHub, GitHub's "
            "history and ownership, the glossary of terms, the plan tiers, "
            "and account-security basics"
        ),
        "general_knowledge_scope": (
            "general, publicly-known facts about Git, GitHub, and software "
            "collaboration"
        ),
    },
    {
        "name": "engineering",
        "display_name": "Engineering",
        "persona_name": "Octo",
        "domain_label": "day-to-day GitHub engineering workflow",
        "scope_description": (
            "pull requests, the branching strategy, issues and projects, the "
            "code-review guide (including approval rules), and commit "
            "conventions"
        ),
        "general_knowledge_scope": (
            "general software-engineering and Git workflow best practice"
        ),
    },
    {
        "name": "devops",
        "display_name": "DevOps",
        "persona_name": "Cypher",
        "domain_label": "CI/CD and GitHub infrastructure",
        "scope_description": (
            "GitHub Actions, the CI/CD pipeline, secrets management and "
            "rotation, branch protection, deployments and environments, and "
            "GitHub billing/contract details"
        ),
        "general_knowledge_scope": (
            "general CI/CD, DevOps, and cloud-infrastructure practice"
        ),
    },
]

DEMO_PASSWORD = "Mnema123!"

# (username, email, is_superuser, [(role_slug, is_role_admin), ...])
# Every non-superuser is a member of `everyone` plus (optionally) one
# specialised role. Department-style leads are role-admins so the upload
# feature can be demoed too.
DEMO_USERS: list[dict] = [
    {"username": "alex",  "email": "alex@example.com",  "is_superuser": False,
     "roles": [("everyone", False), ("engineering", True)]},
    {"username": "erin",  "email": "erin@example.com",  "is_superuser": False,
     "roles": [("everyone", False), ("engineering", False)]},
    {"username": "dana",  "email": "dana@example.com",  "is_superuser": False,
     "roles": [("everyone", False), ("devops", True)]},
    {"username": "sam",   "email": "sam@example.com",   "is_superuser": False,
     "roles": [("everyone", False)]},
    # A lead who can see both engineering and devops (but is not a superuser).
    {"username": "morgan", "email": "morgan@example.com", "is_superuser": False,
     "roles": [("everyone", False), ("engineering", True), ("devops", True)]},
]


# Folder prefix (relative to dataset root) → role slugs. First match wins.
FOLDER_ROLE_MAP: list[tuple[str, list[str]]] = [
    ("general/",     ["everyone"]),
    ("engineering/", ["engineering"]),
    ("devops/",      ["devops"]),
]
DEFAULT_ROLES = ["everyone"]  # top-level files like README.md


async def clear_superuser_memberships(session) -> int:
    result = await session.execute(
        sql_delete(UserRole).where(
            UserRole.user_id.in_(
                select(User.id).where(User.is_superuser.is_(True))
            )
        )
    )
    await session.commit()
    return result.rowcount or 0


async def truncate_documents_and_chats(session) -> None:
    await session.execute(
        text(
            "TRUNCATE TABLE document_chunks, document_roles, documents, "
            "messages, conversations RESTART IDENTITY CASCADE"
        )
    )
    await session.commit()


async def delete_non_superusers(session) -> list[str]:
    """Delete every non-superuser account. Documents are already gone
    (truncated first) so the uploaded_by_admin FK can't block this."""
    result = await session.execute(
        select(User).where(User.is_superuser.is_(False))
    )
    users = list(result.scalars().all())
    names = [u.username for u in users]
    for u in users:
        await session.delete(u)
    await session.commit()
    return names


async def delete_all_roles(session) -> int:
    """Delete every role. Safe only after documents are truncated and
    non-superuser users removed (so no document_roles / user_roles
    reference them, and superusers have no memberships)."""
    result = await session.execute(sql_delete(Role))
    await session.commit()
    return result.rowcount or 0


async def create_roles(session) -> dict[str, int]:
    repo = RoleRepository(session)
    name_to_id: dict[str, int] = {}
    for spec in ROLE_DEFINITIONS:
        created = await repo.create_role(**spec)
        name_to_id[spec["name"]] = created.id
        print(f"  + role '{spec['name']}' (id={created.id})")
    await session.commit()
    return name_to_id


async def get_admin_id(session) -> int:
    result = await session.execute(
        select(User.id).where(User.is_superuser.is_(True)).order_by(User.id).limit(1)
    )
    admin_id = result.scalar_one_or_none()
    if admin_id is None:
        raise RuntimeError("No superuser found. Seed the admin first.")
    return admin_id


async def create_demo_users(session, name_to_role_id: dict[str, int]) -> None:
    repo = UserRepository(session)
    for spec in DEMO_USERS:
        role_assignments = [
            (name_to_role_id[slug], is_admin)
            for slug, is_admin in spec["roles"]
            if slug in name_to_role_id
        ]
        user = await repo.create_user(
            username=spec["username"],
            email=spec["email"],
            plain_password=DEMO_PASSWORD,
            is_superuser=spec["is_superuser"],
            role_assignments=role_assignments,
        )
        print(f"  + user '{spec['username']}' (id={user.id}, roles={spec['roles']})")
    await session.commit()


def roles_for_path(rel_path: Path) -> list[str]:
    s = rel_path.as_posix()
    for prefix, roles in FOLDER_ROLE_MAP:
        if s.startswith(prefix):
            return roles
    return DEFAULT_ROLES


def iter_dataset_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield p


async def ingest_one(session, src: Path, rel: Path, role_ids: list[int],
                     admin_id: int, upload_dir: Path) -> None:
    doc_repo = DocumentRepository(session)
    role_repo = DocumentRoleRepository(session)
    ingestion = IngestionService(session)

    flat_name = rel.as_posix().replace("/", "_")
    unique_name = f"{uuid.uuid4().hex}_{flat_name}"
    dest = upload_dir / unique_name
    shutil.copyfile(src, dest)

    mime_type = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    doc = await doc_repo.create_document(
        filename=unique_name,
        original_filename=flat_name,
        file_path=str(dest),
        file_size=dest.stat().st_size,
        mime_type=mime_type,
        uploaded_by_admin=admin_id,
    )
    await role_repo.set_role_ids(doc.id, role_ids)
    await ingestion.index_document(doc)


async def run(dataset_dir: Path, dry_run: bool) -> None:
    if not dataset_dir.exists():
        raise SystemExit(f"Dataset not found: {dataset_dir}")

    files = list(iter_dataset_files(dataset_dir))
    print(f"Discovered {len(files)} supported file(s) under {dataset_dir}")

    if dry_run:
        print("\nDRY RUN — planned uploads:")
        for f in files:
            rel = f.relative_to(dataset_dir)
            print(f"  {rel.as_posix():<48}  roles={roles_for_path(rel)}")
        print("\nPlanned roles:", [r["name"] for r in ROLE_DEFINITIONS])
        print("Planned users:", [u["username"] for u in DEMO_USERS])
        return

    await init_db()
    upload_dir = Path(settings.UPLOAD_DIR) / "github_demo"
    upload_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        admin_id = await get_admin_id(session)
        print(f"Using admin id {admin_id} as uploader.")

        print("\nStripping superuser memberships...")
        print(f"  cleared {await clear_superuser_memberships(session)} row(s)")

        print("Truncating documents + conversations...")
        await truncate_documents_and_chats(session)

        print("Deleting non-superuser users...")
        removed = await delete_non_superusers(session)
        print(f"  removed {len(removed)} user(s): {', '.join(removed) or '(none)'}")

        print("Deleting old roles...")
        print(f"  removed {await delete_all_roles(session)} role(s)")

        print("Creating GitHub demo roles...")
        name_to_id = await create_roles(session)

        print("Creating GitHub demo users...")
        await create_demo_users(session, name_to_id)

    # Ingest each file in its own session/transaction so one failure
    # doesn't roll back the whole batch.
    print("\nIngesting documents...")
    succeeded = 0
    failed: list[tuple[str, str]] = []
    for i, src in enumerate(files, 1):
        rel = src.relative_to(dataset_dir)
        role_names = roles_for_path(rel)
        print(f"  [{i:>2}/{len(files)}] {rel.as_posix():<48} → {role_names}")
        try:
            async with AsyncSessionLocal() as s:
                role_ids = [name_to_id[n] for n in role_names]
                await ingest_one(s, src, rel, role_ids, admin_id, upload_dir)
                await s.commit()
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed.append((rel.as_posix(), str(exc)))
            print(f"      FAILED: {exc}")

    print(f"\nDone. {succeeded}/{len(files)} files ingested.")
    if failed:
        print("Failures:")
        for name, err in failed:
            print(f"  - {name}: {err}")
    print(f"\nDemo users (password '{DEMO_PASSWORD}'): "
          + ", ".join(u["username"] for u in DEMO_USERS))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", default="github",
                   help="Path to the dataset directory (default: github)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the plan without touching the DB.")
    args = p.parse_args()
    asyncio.run(run(Path(args.dataset).resolve(), args.dry_run))


if __name__ == "__main__":
    main()
