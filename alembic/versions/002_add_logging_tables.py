"""Add app_events, track_history, user_reports tables

Revision ID: 002
Revises: 001
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=True),
        # VARCHAR enum — works on both SQLite and PostgreSQL without a native type
        sa.Column("level", sa.String(20), nullable=False, server_default="info"),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=True),
        sa.Column("operation_id", sa.String(36), nullable=True),
        sa.Column("source", sa.String(50), nullable=True),
    )
    op.create_index("ix_app_events_id",           "app_events", ["id"],           unique=True)
    op.create_index("ix_app_events_created_at",   "app_events", ["created_at"])
    op.create_index("ix_app_events_user_id",      "app_events", ["user_id"])
    op.create_index("ix_app_events_track_id",     "app_events", ["track_id"])
    op.create_index("ix_app_events_level",        "app_events", ["level"])
    op.create_index("ix_app_events_event_type",   "app_events", ["event_type"])
    op.create_index("ix_app_events_operation_id", "app_events", ["operation_id"])

    op.create_table(
        "track_history",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_track_history_id",         "track_history", ["id"],         unique=True)
    op.create_index("ix_track_history_created_at", "track_history", ["created_at"])
    op.create_index("ix_track_history_track_id",   "track_history", ["track_id"])

    op.create_table(
        "user_reports",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_user_reports_id",      "user_reports", ["id"],      unique=True)
    op.create_index("ix_user_reports_user_id", "user_reports", ["user_id"])
    op.create_index("ix_user_reports_status",  "user_reports", ["status"])


def downgrade() -> None:
    op.drop_table("user_reports")
    op.drop_table("track_history")
    op.drop_table("app_events")
