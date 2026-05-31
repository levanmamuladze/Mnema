"""
One-shot ingestion of the bundled Mnema demo dataset.

What this script does:

  1. Truncates uploaded-document state (documents, document_chunks,
     document_roles) and conversation state (conversations, messages).
     The `admin` superuser and `roles` table are PRESERVED.
  2. Removes any role memberships from superusers (a superuser must
     have ZERO memberships — they have full access via the flag).
  3. Removes legacy non-demo users that were created during earlier
     testing (bob_dev, audit_test, audit_test2, sasha_sales).
  4. Ensures the six demo roles exist: hr, developer, sales, finance,
     operations, support. Missing ones are created with sensible
     personas/scope/general_knowledge_scope.
  5. Creates or updates 22 demo user accounts — one per NorthCape
     employee — assigning each user to the role(s) matching their
     real department. Department heads receive is_role_admin = True.
     All demo users share the password `Mnema123!`.
  6. Walks `mnema_demo_dataset/` and uploads every supported file,
     assigning each file to one or more roles based on its folder.
  7. Files are copied into the standard UPLOAD_DIR and run through the
     full IngestionService (load → chunk → embed → store).

Run inside the running app container:

    docker exec -it mnema_app python scripts/seed_demo_dataset.py

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

from sqlalchemy import delete, select, text

from core.config import settings
from db.models import (
    Conversation,
    Document,
    DocumentChunk,
    DocumentRole,
    Message,
    Role,
    User,
    UserRole,
)
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


# Each role definition mirrors the Role model: name (slug), display_name,
# persona_name, domain_label, scope_description, general_knowledge_scope.
ROLE_DEFINITIONS: list[dict[str, str]] = [
    {
        "name": "hr",
        "display_name": "HR",
        "persona_name": "Morgan",
        "domain_label": "human resources",
        "scope_description": (
            "company HR policies, employee handbook, vacation and remote-work "
            "policies, benefits, expenses, onboarding, code of conduct, and "
            "the employee directory"
        ),
        "general_knowledge_scope": (
            "general guidance on workplace etiquette, leave planning, and "
            "common HR practices, even if not in the policy documents"
        ),
    },
    {
        "name": "developer",
        "display_name": "Engineering",
        "persona_name": "Dev",
        "domain_label": "engineering and technical operations",
        "scope_description": (
            "engineering standards, architecture, deployment runbooks, "
            "git workflow, code review, on-call, security practices, "
            "and infrastructure inventory"
        ),
        "general_knowledge_scope": (
            "general software-engineering knowledge: languages, frameworks, "
            "design patterns, debugging guidance, and modern best practices"
        ),
    },
    {
        "name": "sales",
        "display_name": "Sales & Accounts",
        "persona_name": "Sam",
        "domain_label": "sales and client accounts",
        "scope_description": (
            "client profiles, the CRM pipeline, opportunities, contact logs, "
            "the sales playbook, and account-level history"
        ),
        "general_knowledge_scope": (
            "general B2B sales practice, qualification frameworks, and "
            "negotiation hygiene"
        ),
    },
    {
        "name": "finance",
        "display_name": "Finance",
        "persona_name": "Fenna",
        "domain_label": "finance, contracts, and invoicing",
        "scope_description": (
            "the operating budget, invoices issued to clients, contract "
            "samples and templates, and overall revenue and cost plans"
        ),
        "general_knowledge_scope": (
            "general accounting and contract-management practices for a "
            "small consulting firm"
        ),
    },
    {
        "name": "operations",
        "display_name": "Operations",
        "persona_name": "Olivia",
        "domain_label": "company operations",
        "scope_description": (
            "the company profile and leadership, department charters and "
            "org structure, project briefs and milestones, recurring "
            "meetings and meeting notes, internal links, and the office / "
            "communication setup"
        ),
        "general_knowledge_scope": (
            "general project-management and operations practice for a "
            "modern consulting firm"
        ),
    },
    {
        "name": "support",
        "display_name": "Support",
        "persona_name": "Sora",
        "domain_label": "client support and ticketing",
        "scope_description": (
            "the SLA policy, current support tickets, sample task tickets, "
            "and triage practices"
        ),
        "general_knowledge_scope": (
            "general best practice for customer-support triage, escalation, "
            "and SLA management"
        ),
    },
]


# Demo user accounts mirroring the 22 NorthCape employees.
# Each entry: (username, email, is_superuser, [(role_slug, is_role_admin), ...]).
# Department heads get is_role_admin=True for their primary role; cross-
# functional folks (Aisha, Karoline, Priya) hold memberships in multiple
# roles, which exercises the many-to-many design.
#
# Margot (CEO) is the only employee with is_superuser=True. Per the
# rule that superusers have no role memberships, her list is empty.
DEMO_PASSWORD = "Mnema123!"

DEMO_USERS: list[dict] = [
    # Leadership
    {"username": "margot",   "email": "margot.vermeer@northcape.digital",   "is_superuser": True,  "roles": []},
    {"username": "daniel",   "email": "daniel.ohailpin@northcape.digital",  "is_superuser": False, "roles": [("developer", True),  ("operations", False)]},
    {"username": "karoline", "email": "karoline.wessel@northcape.digital",  "is_superuser": False, "roles": [("operations", True), ("finance",   False), ("hr", False)]},

    # Engineering
    {"username": "bashir",   "email": "bashir.quraishi@northcape.digital",  "is_superuser": False, "roles": [("developer", True)]},
    {"username": "lina",     "email": "lina.korhonen@northcape.digital",    "is_superuser": False, "roles": [("developer", False)]},
    {"username": "tomas",    "email": "tomas.beltran@northcape.digital",    "is_superuser": False, "roles": [("developer", False)]},
    {"username": "priya",    "email": "priya.ramanathan@northcape.digital", "is_superuser": False, "roles": [("developer", False), ("support", False)]},
    {"username": "etienne",  "email": "etienne.lacroix@northcape.digital",  "is_superuser": False, "roles": [("developer", False)]},
    {"username": "sofia",    "email": "sofia.larsson@northcape.digital",    "is_superuser": False, "roles": [("developer", False)]},
    {"username": "ravi",     "email": "ravi.subramanian@northcape.digital", "is_superuser": False, "roles": [("developer", False)]},
    {"username": "marcin",   "email": "marcin.wojcik@northcape.digital",    "is_superuser": False, "roles": [("developer", False)]},
    {"username": "bea",      "email": "bea.estrella@northcape.digital",     "is_superuser": False, "roles": [("developer", False)]},
    {"username": "ondrej",   "email": "ondrej.simek@northcape.digital",     "is_superuser": False, "roles": [("developer", False), ("support", False)]},

    # Design (Mnema has no "design" role; designers join developer + operations)
    {"username": "yuki",     "email": "yuki.tanabe@northcape.digital",      "is_superuser": False, "roles": [("developer", False), ("operations", False)]},
    {"username": "florian",  "email": "florian.wessel@northcape.digital",   "is_superuser": False, "roles": [("developer", False)]},

    # Project Management
    {"username": "halima",   "email": "halima.idris@northcape.digital",     "is_superuser": False, "roles": [("operations", True)]},
    {"username": "niko",     "email": "niko.petrovic@northcape.digital",    "is_superuser": False, "roles": [("operations", False)]},

    # Sales
    {"username": "carla",    "email": "carla.almeida@northcape.digital",    "is_superuser": False, "roles": [("sales", True)]},
    {"username": "theo",     "email": "theo.hartmann@northcape.digital",    "is_superuser": False, "roles": [("sales", False)]},

    # HR & Operations / Finance / IT Support
    {"username": "aisha",    "email": "aisha.diallo@northcape.digital",     "is_superuser": False, "roles": [("hr", True),       ("operations", False)]},
    {"username": "fenna",    "email": "fenna.vandijk@northcape.digital",    "is_superuser": False, "roles": [("finance", True)]},
    {"username": "sora",     "email": "sora.yamamoto@northcape.digital",    "is_superuser": False, "roles": [("support", True)]},
]

# Usernames that should be removed if present. These are leftover test
# users from earlier QA runs — not part of the demo and not in the
# employee roster. The `admin` superuser is intentionally left alone.
LEGACY_USERNAMES = {"bob_dev", "audit_test", "audit_test2", "sasha_sales"}


# Folder prefix → roles (matches checked in order, first hit wins).
# Paths are relative to the dataset root (e.g. "hr/policies/...").
FOLDER_ROLE_MAP: list[tuple[str, list[str]]] = [
    ("hr/",                ["hr"]),
    ("employees/",         ["hr"]),
    ("docs/engineering/",  ["developer"]),
    ("docs/design/",       ["developer"]),
    ("infrastructure/",    ["developer"]),
    ("sql/",               ["developer"]),
    ("docs/sales/",        ["sales"]),
    ("clients/",           ["sales", "operations"]),
    ("crm/",               ["sales"]),
    ("finance/",           ["finance"]),
    ("contracts/",         ["finance", "sales"]),
    ("docs/pm/",           ["operations", "developer"]),
    ("departments/",       ["operations"]),
    ("company/",           ["operations", "hr", "developer", "sales", "finance", "support"]),
    ("communication/",     ["operations"]),
    ("links/",             ["operations"]),
    ("meetings/",          ["operations", "sales"]),
    ("projects/",          ["operations", "developer"]),
    ("support/",           ["support"]),
    ("tasks/",             ["support", "developer"]),
    # Top-level files (e.g. README.md) — default fallback below.
]
DEFAULT_ROLES = ["operations"]


async def truncate_demo_state(session) -> None:
    """Wipe documents/chunks/doc_roles and conversations/messages.

    Users and roles are preserved (they are owned data, not seeded by
    this script). RESTART IDENTITY keeps freshly created rows on small
    integer ids, which keeps log output readable.
    """
    # CASCADE handles dependents (chunks, doc_roles, messages).
    await session.execute(
        text(
            "TRUNCATE TABLE document_chunks, document_roles, documents, "
            "messages, conversations RESTART IDENTITY CASCADE"
        )
    )
    await session.commit()


async def clear_superuser_memberships(session) -> int:
    """Defensive: superusers must have ZERO role memberships.

    The is_superuser flag grants global access; piling a role on top is
    redundant and confuses the persona-resolution logic (which picks a
    persona from the single-membership case). Wipe them.
    """
    from sqlalchemy import delete as sql_delete
    result = await session.execute(
        sql_delete(UserRole).where(
            UserRole.user_id.in_(
                select(User.id).where(User.is_superuser.is_(True))
            )
        )
    )
    await session.commit()
    return result.rowcount or 0


async def delete_legacy_users(session) -> list[str]:
    """Delete leftover test users by username. Returns the names removed.

    Documents uploaded by these users are reassigned to the admin so the
    FK on documents.uploaded_by_admin doesn't block the delete.
    """
    if not LEGACY_USERNAMES:
        return []

    repo = UserRepository(session)
    doc_repo = DocumentRepository(session)
    admin_id = await get_admin_id(session)

    removed: list[str] = []
    for username in sorted(LEGACY_USERNAMES):
        user = await repo.get_by_username(username)
        if user is None or user.is_superuser:
            continue
        await doc_repo.reassign_uploader(user.id, admin_id)
        await session.delete(user)
        removed.append(username)
    if removed:
        await session.commit()
    return removed


async def seed_demo_users(session, name_to_role_id: dict[str, int]) -> dict[str, int]:
    """Create or update one user per DEMO_USERS entry.

    Idempotent: re-running converges to the spec. For each user:
      - If they don't exist, create them with the demo password.
      - If they exist, leave their password alone but reset memberships
        and the is_superuser flag to match the spec.

    Returns {username: user_id}.
    """
    repo = UserRepository(session)
    out: dict[str, int] = {}

    for spec in DEMO_USERS:
        role_assignments = [
            (name_to_role_id[slug], is_admin)
            for slug, is_admin in spec["roles"]
            if slug in name_to_role_id
        ]

        existing = await repo.get_by_username(spec["username"])
        if existing is None:
            user = await repo.create_user(
                username=spec["username"],
                email=spec["email"],
                plain_password=DEMO_PASSWORD,
                is_superuser=spec["is_superuser"],
                role_assignments=role_assignments,
            )
            out[spec["username"]] = user.id
            print(f"  + created user '{spec['username']}' (id={user.id}, "
                  f"superuser={spec['is_superuser']}, roles={spec['roles']})")
        else:
            # Reset memberships and superuser flag to match the spec.
            existing.is_superuser = spec["is_superuser"]
            session.add(existing)
            await session.flush()
            # If the user is being demoted to non-superuser AND has no
            # role assignments, set_role_memberships still requires the
            # list (it can be empty for a superuser-only account).
            user = await repo.set_role_memberships(existing.id, role_assignments)
            out[spec["username"]] = user.id
            print(f"  ↻ updated user '{spec['username']}' (id={user.id}, "
                  f"superuser={spec['is_superuser']}, roles={spec['roles']})")

    await session.commit()
    return out


async def get_admin_id(session) -> int:
    result = await session.execute(
        select(User.id).where(User.is_superuser.is_(True)).order_by(User.id).limit(1)
    )
    admin_id = result.scalar_one_or_none()
    if admin_id is None:
        raise RuntimeError(
            "No superuser found. Run the docker entrypoint to seed admin first."
        )
    return admin_id


async def ensure_roles(session) -> dict[str, int]:
    """Create any missing roles from ROLE_DEFINITIONS; return {name: id}."""
    repo = RoleRepository(session)
    name_to_id: dict[str, int] = {}
    for spec in ROLE_DEFINITIONS:
        existing = await repo.get_by_name(spec["name"])
        if existing is None:
            created = await repo.create_role(**spec)
            name_to_id[spec["name"]] = created.id
            print(f"  + created role '{spec['name']}' (id={created.id})")
        else:
            name_to_id[spec["name"]] = existing.id
    await session.commit()
    return name_to_id


def roles_for_path(rel_path: Path) -> list[str]:
    """Map a dataset-relative path to one or more role slugs."""
    s = rel_path.as_posix() + ("/" if rel_path.is_dir() else "")
    for prefix, roles in FOLDER_ROLE_MAP:
        if s.startswith(prefix):
            return roles
    return DEFAULT_ROLES


def iter_dataset_files(root: Path) -> Iterable[Path]:
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        yield p


async def ingest_one(
    session,
    src: Path,
    rel: Path,
    role_ids: list[int],
    admin_id: int,
    upload_dir: Path,
) -> None:
    doc_repo = DocumentRepository(session)
    role_repo = DocumentRoleRepository(session)
    ingestion = IngestionService(session)

    # Keep the original folder breadcrumb in the saved filename so the
    # admin UI shows "hr_policies_vacation_policy.md" rather than 22
    # files all called "vacation_policy.md" when displayed flat.
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
            print(f"  {rel.as_posix():<70}  roles={roles_for_path(rel)}")
        return

    await init_db()

    upload_dir = Path(settings.UPLOAD_DIR) / "demo"
    upload_dir.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as session:
        admin_id = await get_admin_id(session)
        print(f"Using admin id {admin_id} as uploader.")

        print("Ensuring demo roles exist...")
        name_to_id = await ensure_roles(session)
        for name, rid in name_to_id.items():
            print(f"  - {name:<12} id={rid}")

        print("\nStripping role memberships from superusers...")
        cleared = await clear_superuser_memberships(session)
        print(f"  cleared {cleared} stray membership row(s)")

        print("\nRemoving legacy test users...")
        removed = await delete_legacy_users(session)
        if removed:
            print(f"  removed: {', '.join(removed)}")
        else:
            print("  (none found)")

        print("\nSeeding demo users (one per NorthCape employee)...")
        await seed_demo_users(session, name_to_id)

        print("\nTruncating existing demo state (documents, chunks, conversations)...")
        await truncate_demo_state(session)

        succeeded = 0
        failed: list[tuple[str, str]] = []
        for i, src in enumerate(files, 1):
            rel = src.relative_to(dataset_dir)
            role_names = roles_for_path(rel)
            role_ids = [name_to_id[n] for n in role_names]
            print(
                f"  [{i:>3}/{len(files)}] {rel.as_posix():<70} → {role_names}"
            )
            try:
                async with AsyncSessionLocal() as inner:
                    await ingest_one(inner, src, rel, role_ids, admin_id, upload_dir)
                    await inner.commit()
                succeeded += 1
            except Exception as exc:
                failed.append((rel.as_posix(), str(exc)))
                print(f"      FAILED: {exc}")

        print(f"\nDone. {succeeded}/{len(files)} files ingested.")
        if failed:
            print("\nFailures:")
            for name, err in failed:
                print(f"  - {name}: {err}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dataset",
        default="mnema_demo_dataset",
        help="Path to the dataset directory (default: mnema_demo_dataset)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned uploads without touching the DB.",
    )
    args = p.parse_args()

    asyncio.run(run(Path(args.dataset).resolve(), args.dry_run))


if __name__ == "__main__":
    main()
