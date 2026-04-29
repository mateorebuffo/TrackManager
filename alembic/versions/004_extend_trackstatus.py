"""Extend trackstatus enum with bandcamp_only and set_mix

Revision ID: 004
Revises: 003
Create Date: 2026-04-29
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ALTER TYPE ADD VALUE must not run inside a transaction on PG < 12.
    # Railway uses PG 14+, so this is safe.
    op.execute(sa.text("ALTER TYPE trackstatus ADD VALUE IF NOT EXISTS 'bandcamp_only'"))
    op.execute(sa.text("ALTER TYPE trackstatus ADD VALUE IF NOT EXISTS 'set_mix'"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; a full type rebuild is required.
    # Downgrade is intentionally a no-op — the extra values are harmless if unused.
    pass
