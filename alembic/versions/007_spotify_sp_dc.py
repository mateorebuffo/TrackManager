"""Add spotify_sp_dc cookie field for non-admin users

Revision ID: 007
Revises: 006
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("spotify_sp_dc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_settings", "spotify_sp_dc")
