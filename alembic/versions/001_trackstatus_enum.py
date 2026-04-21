"""Replace ReviewStatus enum with TrackStatus

Revision ID: 001
Revises:
Create Date: 2026-04-06

Old values: pending, approved, rejected, download_later, downloaded, not_found, vinyl_only
New values: pending, queued, downloaded, not_found, vinyl_only, discarded

Data migration:
  approved       → queued
  rejected       → discarded
  download_later → pending   (undecided — back to inbox)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None

OLD_ENUM = "reviewstatus"
NEW_ENUM = "trackstatus"
TABLE = "review_items"
COLUMN = "status"

old_values = ("pending", "approved", "rejected", "download_later", "downloaded", "not_found", "vinyl_only")
new_values = ("pending", "queued", "downloaded", "not_found", "vinyl_only", "discarded")


def upgrade() -> None:
    # 1. Create the new enum type
    new_type = postgresql.ENUM(*new_values, name=NEW_ENUM)
    new_type.create(op.get_bind(), checkfirst=True)

    # 2. Add a temporary column with the new type
    op.add_column(TABLE, sa.Column("status_new", sa.Enum(*new_values, name=NEW_ENUM), nullable=True))

    # 3. Migrate data
    op.execute(f"""
        UPDATE {TABLE} SET status_new = CASE
            WHEN {COLUMN}::text = 'approved'       THEN 'queued'::trackstatus
            WHEN {COLUMN}::text = 'rejected'       THEN 'discarded'::trackstatus
            WHEN {COLUMN}::text = 'download_later' THEN 'pending'::trackstatus
            ELSE {COLUMN}::text::trackstatus
        END
    """)

    # 4. Drop old column and rename new one
    op.drop_index("ix_review_items_status", table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
    op.alter_column(TABLE, "status_new", new_column_name=COLUMN, nullable=False)
    op.create_index("ix_review_items_status", TABLE, [COLUMN])

    # 5. Drop old enum type
    old_type = postgresql.ENUM(*old_values, name=OLD_ENUM)
    old_type.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    old_type = postgresql.ENUM(*old_values, name=OLD_ENUM)
    old_type.create(op.get_bind(), checkfirst=True)

    op.add_column(TABLE, sa.Column("status_old", sa.Enum(*old_values, name=OLD_ENUM), nullable=True))

    op.execute(f"""
        UPDATE {TABLE} SET status_old = CASE
            WHEN {COLUMN}::text = 'queued'    THEN 'approved'::reviewstatus
            WHEN {COLUMN}::text = 'discarded' THEN 'rejected'::reviewstatus
            ELSE {COLUMN}::text::reviewstatus
        END
    """)

    op.drop_index("ix_review_items_status", table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
    op.alter_column(TABLE, "status_old", new_column_name=COLUMN, nullable=False)
    op.create_index("ix_review_items_status", TABLE, [COLUMN])

    new_type = postgresql.ENUM(*new_values, name=NEW_ENUM)
    new_type.drop(op.get_bind(), checkfirst=True)
