"""
Tipo de columna que cifra y descifra sola.

Con esto ningún call site cambia: `us.muzpa_sess` sigue devolviendo el texto
plano y asignarle un valor lo guarda cifrado. La alternativa —cifrar a mano en
cada lugar que toca una credencial— garantiza que alguna vez alguien se olvide.

**No se puede filtrar por estas columnas.** Fernet usa un IV al azar, así que el
mismo texto da un cifrado distinto cada vez y un `WHERE muzpa_sess = 'x'` nunca
va a encontrar nada. Hoy sólo se las usa por su valor de verdad
(`if not us.muzpa_sess`), que sigue funcionando porque los vacíos no se cifran.
"""
from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator


class EncryptedText(TypeDecorator):
    """Text que se guarda cifrado en la base."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        from app.services.crypto import encrypt
        return encrypt(value)

    def process_result_value(self, value, dialect):
        from app.services.crypto import decrypt
        return decrypt(value)
