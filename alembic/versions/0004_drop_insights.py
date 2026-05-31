"""drop the dead learning_insights table

Revision ID: 0004_drop_insights
Revises: 0003_quizzes_tokens
Create Date: 2026-05-30 12:00:00

The learning_insights table backed an old feature — per-reply, LLM-extracted
"takeaways" — that was removed when learning progress became graded quiz
mastery. Nothing reads or writes it anymore, so the ORM model was deleted;
this migration drops the now-orphaned table to keep the schema in sync.

One-way for consistency with the other migrations; downgrade raises.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0004_drop_insights"
down_revision: Union[str, None] = "0003_quizzes_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF EXISTS so the migration is a no-op on any DB where the table was
    # already absent. Dropping the table also drops its index.
    op.execute("DROP TABLE IF EXISTS learning_insights")


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0004 is not supported. Restore from backup if you "
        "need to revert."
    )
