"""add onboarding_done to user_settings

Escrita a mano, no autogenerada: `alembic autogenerate` sobre SQLite inventa
"detected removed table/indexes" cuando sólo se agrega una columna, porque no
reporta los índices igual que PostgreSQL. Para un ADD COLUMN alcanza con esto.

server_default="0" para que las filas que ya existen queden en False en vez de
NULL, que la columna no admite.

Revision ID: 008
Revises: 1a749f705982
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "1a749f705982"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("onboarding_done", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "onboarding_done")
