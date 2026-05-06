"""Add per-user Spotify client credentials

Revision ID: 006
Revises: 005
Create Date: 2026-05-06
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_settings", sa.Column("spotify_client_id", sa.Text(), nullable=True))
    op.add_column("user_settings", sa.Column("spotify_client_secret", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_settings", "spotify_client_secret")
    op.drop_column("user_settings", "spotify_client_id")
