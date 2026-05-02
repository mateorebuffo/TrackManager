"""Add is_active to users

Revision ID: 005
Revises: 004
Create Date: 2026-05-02
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"))


def downgrade() -> None:
    op.drop_column("users", "is_active")
