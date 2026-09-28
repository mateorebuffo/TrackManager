"""
Cifrado de credenciales en reposo.

Protege contra que se filtre un backup o un dump de la base. NO protege contra
alguien que entre al servidor: la clave vive en el mismo entorno.

La clave se deriva de SECRET_KEY en vez de agregar una nueva. SECRET_KEY ya es
crítica (perderla invalida todas las sesiones), así que no se suma un segundo
secreto que perder — pero sí significa que **rotar SECRET_KEY vuelve ilegibles
las credenciales** y cada usuario tiene que reconectar sus cuentas. Eso degrada
elegante: `decrypt` devuelve "" y la cuenta aparece como desconectada.
"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)

# Marca explícita en vez de adivinar por el formato de Fernet: hace que la
# migración sea idempotente y que un valor viejo en claro se reconozca solo.
_PREFIJO = "enc:v1:"


def _fernet() -> Fernet:
    # El separador de dominio evita que esta clave coincida con cualquier otro
    # uso futuro de SECRET_KEY.
    material = f"track-manager:credenciales:{settings.secret_key}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def esta_cifrado(valor: str | None) -> bool:
    return bool(valor) and valor.startswith(_PREFIJO)


def encrypt(valor: str | None) -> str | None:
    """Cifrar. Los vacíos se dejan como están para que `if not us.muzpa_sess` siga andando."""
    if not valor:
        return valor
    if esta_cifrado(valor):
        return valor  # idempotente: no cifrar dos veces
    return _PREFIJO + _fernet().encrypt(valor.encode()).decode()


def decrypt(valor: str | None) -> str | None:
    """
    Descifrar. Un valor sin la marca se devuelve tal cual: así las filas que
    todavía no pasaron por la migración siguen funcionando.
    """
    if not valor or not esta_cifrado(valor):
        return valor
    try:
        return _fernet().decrypt(valor.removeprefix(_PREFIJO).encode()).decode()
    except InvalidToken:
        # Pasa si rotaron SECRET_KEY. Devolver "" hace que la cuenta figure como
        # desconectada y el usuario la reconecte, en vez de romper la pantalla.
        logger.warning("No se pudo descifrar una credencial — ¿cambió SECRET_KEY? "
                       "El usuario va a tener que reconectar esa cuenta.")
        return ""
