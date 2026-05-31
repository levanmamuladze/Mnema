"""dynamic roles

Revision ID: 0002_dynamic_roles
Revises: 0001_baseline
Create Date: 2026-05-27 00:00:00

Replaces the hardcoded {hr, developer} role model with user-defined
roles plus a three-tier hierarchy:

  - users.is_superuser (boolean) — full superuser, replaces is_admin.
                                   Bypasses every RBAC check.
  - user_roles (M:N)             — which users belong to which roles.
  - user_roles.is_role_admin     — admin-level inside ONE role only
                                   (e.g. Head of HR for the HR role).
  - document_roles.role_id (FK)  — replaces the old varchar role column.

Data migration:
  1. Seed `roles` with rows for every distinct value previously found
     in users.role and document_roles.role (typically "hr" and
     "developer"). Each gets a default persona derived from the old
     hardcoded prompts so existing chats behave the same after upgrade.
  2. For every existing user, insert a user_roles row joining them to
     the role matching their old users.role value. Users with the old
     is_admin=true become users.is_superuser=true.
  3. For every document_roles row, insert a new row keyed by role_id
     (resolved from the old role string), then drop the old column.

This migration is one-way for safety; downgrade raises.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0002_dynamic_roles"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Persona seeds for the two roles we used to ship with hardcoded.
# Kept here (not imported from rag/prompts.py) so this migration stays
# stable even when the prompt module is refactored later.
_LEGACY_ROLE_SEEDS = {
    "hr": {
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
    "developer": {
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
}


def upgrade() -> None:
    # ── 1. New roles table ────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("persona_name", sa.String(length=50), nullable=False),
        sa.Column("domain_label", sa.String(length=50), nullable=False),
        sa.Column("scope_description", sa.Text(), nullable=False),
        sa.Column("general_knowledge_scope", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    # ── 2. user_roles association table ──────────────────────────────
    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "is_role_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_user_roles_user_id", "user_roles", ["user_id"])
    op.create_index("ix_user_roles_role_id", "user_roles", ["role_id"])

    # ── 3. users.is_superuser ─────────────────────────────────────────
    # Add as nullable first so we can backfill from is_admin, then flip
    # to NOT NULL. server_default makes new rows default to False.
    op.add_column(
        "users",
        sa.Column("is_superuser", sa.Boolean(), nullable=True, server_default=sa.false()),
    )

    # ── 4. document_roles.role_id ────────────────────────────────────
    # Add nullable, backfill below, then make NOT NULL and drop the
    # old varchar role column.
    op.add_column(
        "document_roles",
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("roles.id", ondelete="CASCADE"), nullable=True),
    )

    # ── 5. Data migration ────────────────────────────────────────────
    bind = op.get_bind()

    # 5a. Find every distinct role name currently in use across both
    # users.role and document_roles.role so no existing data is dropped
    # on the floor — even if some non-default role string crept in.
    distinct_user_roles = {
        row[0] for row in bind.execute(
            sa.text("SELECT DISTINCT role FROM users WHERE role IS NOT NULL")
        ).fetchall()
    }
    distinct_doc_roles = {
        row[0] for row in bind.execute(
            sa.text("SELECT DISTINCT role FROM document_roles WHERE role IS NOT NULL")
        ).fetchall()
    }
    all_role_names = distinct_user_roles | distinct_doc_roles

    # 5b. Insert a row in `roles` for each name. Known roles get their
    # full legacy persona; unknown ones get a minimal placeholder the
    # owner is expected to edit.
    role_id_by_name: dict[str, int] = {}
    for name in sorted(all_role_names):
        seed = _LEGACY_ROLE_SEEDS.get(name, {
            "display_name": name.capitalize(),
            "persona_name": name.capitalize(),
            "domain_label": name.capitalize(),
            "scope_description": (
                f"{name} documentation and operations. "
                "(Imported from legacy schema — please edit this description.)"
            ),
            "general_knowledge_scope": (
                f"general knowledge a {name} colleague would have."
            ),
        })
        result = bind.execute(
            sa.text(
                "INSERT INTO roles (name, display_name, persona_name, "
                "domain_label, scope_description, general_knowledge_scope) "
                "VALUES (:name, :display_name, :persona_name, "
                ":domain_label, :scope_description, :general_knowledge_scope) "
                "RETURNING id"
            ),
            {"name": name, **seed},
        )
        role_id_by_name[name] = result.scalar_one()

    # 5c. Backfill user_roles from users.role + users.is_admin.
    # The old is_admin flag meant "global admin" — under the new model
    # those people become superusers (full RBAC bypass), NOT role-admins.
    # Treating them as role-admins of their old role would shrink their
    # scope, which would surprise the existing admin on upgrade.
    users = bind.execute(
        sa.text("SELECT id, role, is_admin FROM users")
    ).fetchall()
    for user_id, role_name, is_admin in users:
        if role_name and role_name in role_id_by_name:
            bind.execute(
                sa.text(
                    "INSERT INTO user_roles (user_id, role_id, is_role_admin) "
                    "VALUES (:user_id, :role_id, false) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"user_id": user_id, "role_id": role_id_by_name[role_name]},
            )
        if is_admin:
            bind.execute(
                sa.text("UPDATE users SET is_superuser = true WHERE id = :id"),
                {"id": user_id},
            )

    # 5d. Backfill document_roles.role_id from the old role string.
    bind.execute(
        sa.text(
            "UPDATE document_roles dr "
            "SET role_id = r.id "
            "FROM roles r "
            "WHERE r.name = dr.role"
        )
    )

    # ── 6. Tighten constraints ───────────────────────────────────────
    op.alter_column("users", "is_superuser", nullable=False, server_default=sa.false())
    op.alter_column("document_roles", "role_id", nullable=False)

    # ── 7. Drop legacy columns ───────────────────────────────────────
    # Old UNIQUE (document_id, role) goes with the column.
    op.drop_constraint("uq_document_role", "document_roles", type_="unique")
    op.drop_column("document_roles", "role")
    op.create_unique_constraint(
        "uq_document_role_id", "document_roles", ["document_id", "role_id"]
    )

    op.drop_column("users", "role")
    op.drop_column("users", "is_admin")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from dynamic-roles is not supported. Restore from "
        "backup if you need to revert."
    )
