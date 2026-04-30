"""Initial schema

Revision ID: 000
Revises:
Create Date: 2026-04-29

Creates the base tables as they existed before migration 001.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "000"
down_revision = None
branch_labels = None
depends_on = None

_OLD_STATUS_VALUES = (
    "pending", "approved", "rejected", "download_later",
    "downloaded", "not_found", "vinyl_only",
)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("username", sa.String(), unique=True, nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("soundcloud_oauth_token", sa.Text(), nullable=True),
        sa.Column("muzpa_sess", sa.Text(), nullable=True),
        sa.Column("deezer_arl", sa.Text(), nullable=True),
        sa.Column("download_dir", sa.Text(), nullable=True),
        sa.Column("download_full_eps", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("organize_by_like_date", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("folder_organize_mode", sa.Text(), nullable=False, server_default="none"),
        sa.Column("spotify_token_json", sa.Text(), nullable=True),
        sa.Column("youtube_token_json", sa.Text(), nullable=True),
        sa.Column("spotify_playlist_id", sa.Text(), nullable=True),
        sa.Column("spotify_playlist_name", sa.Text(), nullable=True),
        sa.Column("youtube_playlist_id", sa.Text(), nullable=True),
        sa.Column("youtube_playlist_name", sa.Text(), nullable=True),
    )

    op.create_table(
        "source_tracks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_track_id", sa.String(255), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("raw_title", sa.String(512), nullable=False),
        sa.Column("raw_artist", sa.String(255), nullable=True),
        sa.Column("raw_metadata_json", sa.JSON(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("liked_at", sa.DateTime(), nullable=True),
        sa.Column("collected_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source", "source_track_id", "user_id", name="uq_source_track_user"),
    )
    op.create_index("ix_source_tracks_user_id", "source_tracks", ["user_id"])

    op.create_table(
        "normalized_tracks",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("source_track_id_fk", sa.Integer(), sa.ForeignKey("source_tracks.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("normalized_artist", sa.String(255), nullable=True),
        sa.Column("normalized_title", sa.String(512), nullable=True),
        sa.Column("version_info", sa.String(255), nullable=True),
        sa.Column("search_query", sa.String(512), nullable=True),
        sa.Column("fingerprint_text", sa.String(512), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
    )
    op.create_index("ix_normalized_tracks_fingerprint", "normalized_tracks", ["fingerprint_text"])

    postgresql.ENUM(*_OLD_STATUS_VALUES, name="reviewstatus").create(op.get_bind(), checkfirst=True)

    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "normalized_track_id_fk",
            sa.Integer(),
            sa.ForeignKey("normalized_tracks.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", postgresql.ENUM(*_OLD_STATUS_VALUES, name="reviewstatus", create_type=False), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_review_items_status", "review_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_review_items_status", "review_items")
    op.drop_table("review_items")
    postgresql.ENUM(*_OLD_STATUS_VALUES, name="reviewstatus").drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_normalized_tracks_fingerprint", "normalized_tracks")
    op.drop_table("normalized_tracks")

    op.drop_index("ix_source_tracks_user_id", "source_tracks")
    op.drop_table("source_tracks")

    op.drop_table("user_settings")

    op.drop_index("ix_users_username", "users")
    op.drop_table("users")
