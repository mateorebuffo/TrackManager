"""Señal de credencial vencida, compartida por los descargadores."""
from __future__ import annotations


class AuthExpired(Exception):
    """
    La credencial de un servicio venció a mitad de la descarga.

    Existe para que un 401 NO se confunda con "no encontré el track": el agente
    deja el job sin completar (queda in_progress y reset-stuck lo devuelve a
    pending) en vez de marcarlo not_found de forma permanente.
    """

    def __init__(self, service: str) -> None:
        super().__init__(f"Credencial de {service} vencida")
        self.service = service
