"""add batch_id to download_jobs

Revision ID: 1a749f705982
Revises: 007
Create Date: 2026-08-12 15:33:30.014308

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '1a749f705982'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('download_jobs', sa.Column('batch_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_download_jobs_batch_id'), 'download_jobs', ['batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_download_jobs_batch_id'), table_name='download_jobs')
    op.drop_column('download_jobs', 'batch_id')
