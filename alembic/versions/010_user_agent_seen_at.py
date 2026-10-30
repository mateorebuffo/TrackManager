"""add agent_seen_at to users

Escrita a mano (ver 008): para un ADD COLUMN alcanza con esto. Nullable y sin
default: un usuario cuyo agente nunca se conectó queda en NULL, que es justo lo
que la analítica muestra como "nunca".

Revision ID: 010
Revises: 009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("agent_seen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "agent_seen_at")
