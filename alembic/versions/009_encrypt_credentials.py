"""cifrar las credenciales que ya estaban guardadas en claro

No cambia el esquema: las columnas siguen siendo TEXT. Lo único que hace es
reescribir los valores existentes en su forma cifrada, para que las filas
anteriores queden protegidas igual que las nuevas.

Escrita a mano, como el resto: autogenerate sobre SQLite inventa drops.

Es idempotente — `encrypt()` reconoce el prefijo enc:v1: y no vuelve a cifrar —
así que correrla dos veces no rompe nada.

El downgrade descifra de vuelta, para poder volver atrás sin perder datos. Ojo:
si SECRET_KEY cambió entre el upgrade y el downgrade, los valores no se pueden
recuperar y quedan vacíos; el usuario tendría que reconectar sus cuentas.

Revision ID: 009
Revises: 008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNAS = [
    "soundcloud_oauth_token",
    "muzpa_sess",
    "deezer_arl",
    "spotify_client_secret",
    "spotify_token_json",
    "youtube_token_json",
]


def _recorrer(transformar) -> None:
    from app.services import crypto

    con = op.get_bind()
    filas = con.execute(
        sa.text(f"SELECT id, {', '.join(_COLUMNAS)} FROM user_settings")
    ).fetchall()

    for fila in filas:
        cambios = {}
        for i, columna in enumerate(_COLUMNAS, start=1):
            actual = fila[i]
            nuevo = transformar(crypto, actual)
            if nuevo != actual:
                cambios[columna] = nuevo
        if cambios:
            sets = ", ".join(f"{c} = :{c}" for c in cambios)
            con.execute(
                sa.text(f"UPDATE user_settings SET {sets} WHERE id = :id"),
                {**cambios, "id": fila[0]},
            )


def upgrade() -> None:
    _recorrer(lambda crypto, valor: crypto.encrypt(valor))


def downgrade() -> None:
    _recorrer(lambda crypto, valor: crypto.decrypt(valor))
