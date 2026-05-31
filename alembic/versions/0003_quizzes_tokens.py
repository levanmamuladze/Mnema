"""quizzes + refresh-token revocation

Revision ID: 0003_quizzes_tokens
Revises: 0002_dynamic_roles
Create Date: 2026-05-30 00:00:00

NOTE: the revision id is kept short on purpose — alembic_version.version_num
is varchar(32), so an id longer than 32 chars fails the version UPDATE at the
end of the migration (StringDataRightTruncation).

Adds:
  - quiz_attempts / quiz_questions: graded, doc-grounded quizzes that now
    back the learning-progress (mastery) number.
  - revoked_tokens: server-side denylist of consumed/revoked refresh-token
    jti values (logout invalidation + rotation-reuse detection).

No data migration needed — all three tables are new. learning_progress
keeps its shape; only the *meaning* of progress changes (latest quiz score
instead of accumulated chat deltas), so no column change is required.

One-way for consistency with the other migrations; downgrade raises.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0003_quizzes_tokens"
down_revision: Union[str, None] = "0002_dynamic_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "quiz_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("topic_label", sa.String(length=500), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_quiz_attempts_user_id", "quiz_attempts", ["user_id"])
    op.create_index("ix_quiz_attempts_document_id", "quiz_attempts", ["document_id"])

    op.create_table(
        "quiz_questions",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "attempt_id",
            sa.Integer(),
            sa.ForeignKey("quiz_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("correct_index", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("selected_index", sa.Integer(), nullable=True),
    )
    op.create_index("ix_quiz_questions_attempt_id", "quiz_questions", ["attempt_id"])

    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0003 is not supported. Restore from backup if you "
        "need to revert."
    )
