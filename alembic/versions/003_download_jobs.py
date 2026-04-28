"""Add download_jobs table and api_token to users

Revision ID: 003
Revises: 002
Create Date: 2026-04-26
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "download_jobs",
        sa.Column("id",            sa.Integer(),  primary_key=True, nullable=False),
        sa.Column("created_at",    sa.DateTime(), nullable=False),
        sa.Column("updated_at",    sa.DateTime(), nullable=False),
        sa.Column("user_id",       sa.Integer(),  sa.ForeignKey("users.id",         ondelete="CASCADE"), nullable=False),
        sa.Column("review_id",     sa.Integer(),  sa.ForeignKey("review_items.id",  ondelete="CASCADE"), nullable=False),
        sa.Column("status",        sa.String(32), nullable=False, server_default="pending"),
        sa.Column("query",         sa.Text(),     nullable=False),
        sa.Column("attempt_count", sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("last_error",    sa.Text(),     nullable=True),
        sa.Column("downloaded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_download_jobs_user_id", "download_jobs", ["user_id"])
    op.create_index("ix_download_jobs_status",  "download_jobs", ["status"])
    op.create_index("ix_download_jobs_review_id", "download_jobs", ["review_id"])

    op.add_column("users", sa.Column("api_token", sa.String(64), nullable=True))
    op.create_index("ix_users_api_token", "users", ["api_token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_download_jobs_review_id", "download_jobs")
    op.drop_index("ix_download_jobs_status",    "download_jobs")
    op.drop_index("ix_download_jobs_user_id",   "download_jobs")
    op.drop_table("download_jobs")
    op.drop_index("ix_users_api_token", "users")
    op.drop_column("users", "api_token")
