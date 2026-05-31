"""drop learning_progress + quiz tables (feature removed)

Revision ID: 0005_drop_quizzes
Revises: 0004_drop_insights
Create Date: 2026-05-31 00:00:00

The learning-progress / quiz feature (graded MCQ quizzes that produced a
per-topic "mastery" number) was removed entirely. The ORM models and all
endpoints/UI are gone; this migration drops the now-orphaned tables to keep
the schema in sync.

Drop order respects the FK graph: quiz_questions references quiz_attempts,
so it goes first. learning_progress is independent. IF EXISTS keeps the
migration a no-op on any DB where the tables were already absent; dropping a
table also drops its indexes and constraints.

One-way for consistency with the other migrations; downgrade raises.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0005_drop_quizzes"
down_revision: Union[str, None] = "0004_drop_insights"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quiz_questions")
    op.execute("DROP TABLE IF EXISTS quiz_attempts")
    op.execute("DROP TABLE IF EXISTS learning_progress")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0005 is not supported. Restore from backup if you "
        "need to revert."
    )
