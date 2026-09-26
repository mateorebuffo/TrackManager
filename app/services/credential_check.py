"""
Validación de credenciales de descarga — fuente única de verdad.

La consumen el form web (/settings/verify/*), el agente (POST /api/me/credentials)
y el chequeo de estado (GET /api/me/credentials).

Cada check devuelve (ok, msg). `msg` se muestra tal cual al usuario.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_UA = {"User-Agent": "TrackManager/1.0"}

# Mismo texto para las cuatro cuentas — el panel del agente las lista juntas y
# cada servicio diciendo lo suyo se leía como si fueran estados distintos.
CONNECTED = "Cuenta conectada"

# Mismo host que usa app/collectors/soundcloud.py
_SC_BASE = "https://api-v2.soundcloud.com"


def check_muzpa(sess: str) -> tuple[bool, str]:
    sess = sess.strip()
    if not sess:
        return False, "No hay SESS configurado."
    try:
        # El endpoint de la cuenta ("My Account" en la web de Muzpa) en vez de una
        # búsqueda: es la pregunta "¿quién soy?", así que responde si la sesión
        # sirve y además de qué cuenta es. Una request, no dos.
        resp = httpx.get(
            "https://srv.muzpa.com/a/ms/account",
            cookies={"SESS": sess},
            headers=_UA,
            timeout=_TIMEOUT,
        )
    except Exception as e:
        logger.exception("Muzpa verify error")
        return False, f"Error de conexión: {e}"

    if resp.status_code in (401, 403):
        return False, "Sesión expirada o inválida. Reconectá Muzpa."
    if resp.status_code != 200:
        return False, f"Respuesta inesperada: {resp.status_code}."

    # Una sesión muerta puede devolver 200 con el HTML del login en vez del JSON.
    try:
        data = resp.json()
    except Exception:
        return False, "Sesión expirada o inválida. Reconectá Muzpa."
    if not isinstance(data, dict) or not data.get("id"):
        return False, "Sesión expirada o inválida. Reconectá Muzpa."
    if data.get("disabled"):
        return False, "La cuenta de Muzpa está deshabilitada."

    who = data.get("email") or data.get("login") or ""
    return True, f"{CONNECTED}{' (' + who + ')' if who else ''}"


def check_deezer(arl: str) -> tuple[bool, str]:
    arl = arl.strip()
    if not arl:
        return False, "No hay ARL configurado."
    try:
        resp = httpx.get(
            "https://www.deezer.com/ajax/gw-light.php",
            params={"method": "deezer.getUserData", "input": "3",
                    "api_version": "1.0", "api_token": "null"},
            cookies={"arl": arl},
            headers=_UA,
            timeout=_TIMEOUT,
        )
        data = resp.json()
    except Exception as e:
        logger.exception("Deezer verify error")
        return False, f"Error de conexión: {e}"

    user = data.get("results", {}).get("USER", {}) if isinstance(data, dict) else {}
    user_id = user.get("USER_ID", 0)
    if user_id and int(user_id) > 0:
        email = user.get("EMAIL", "")
        return True, f"{CONNECTED}{' (' + email + ')' if email else ''}"
    return False, "ARL expirado o inválido. Reconectá Deezer."


def check_soundcloud(token: str) -> tuple[bool, str]:
    # El placeholder del form sugiere pegar "OAuth 2-xxx"; sin esto el header
    # terminaba siendo "Authorization: OAuth OAuth 2-xxx".
    token = token.strip().removeprefix("OAuth ").strip()
    if not token:
        return False, "No hay token configurado."

    params = {"client_id": settings.soundcloud_client_id} if settings.soundcloud_client_id else {}
    try:
        resp = httpx.get(
            f"{_SC_BASE}/me",
            headers={"Authorization": f"OAuth {token}", **_UA},
            params=params,
            timeout=_TIMEOUT,
        )
    except Exception as e:
        logger.exception("SoundCloud verify error")
        return False, f"Error de conexión: {e}"

    if resp.status_code in (401, 403):
        return False, "Token expirado o inválido. Reconectá SoundCloud."
    if resp.status_code != 200:
        return False, f"Respuesta inesperada: {resp.status_code}."

    try:
        me = resp.json()
    except Exception:
        return False, "Token expirado o inválido. Reconectá SoundCloud."
    if not isinstance(me, dict) or not me.get("id"):
        return False, "Token expirado o inválido. Reconectá SoundCloud."

    name = me.get("username") or me.get("permalink") or ""
    return True, f"{CONNECTED}{' (' + name + ')' if name else ''}"


def check_youtube(access_token: str) -> tuple[bool, str]:
    """
    Qué canal quedó conectado. Recibe un access token ya válido (youtube_auth se
    encarga de refrescarlo), así que un 401 acá es raro pero no imposible.
    """
    try:
        resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}", **_UA},
            timeout=_TIMEOUT,
        )
    except Exception as e:
        logger.exception("YouTube verify error")
        return False, f"Error de conexión: {e}"

    if resp.status_code in (401, 403):
        return False, "Token expirado o inválido. Reconectá YouTube."
    if resp.status_code != 200:
        return False, f"Respuesta inesperada: {resp.status_code}."

    try:
        items = resp.json().get("items") or []
    except Exception:
        return False, "Respuesta inesperada de YouTube."

    # Una cuenta de Google sin canal autentica bien pero no tiene items: sigue
    # conectada, solo que no hay nombre que mostrar.
    title = (items[0].get("snippet", {}).get("title") or "") if items else ""
    return True, f"{CONNECTED}{' (' + title + ')' if title else ''}"


# servicio -> (columna en UserSettings, función de validación)
SERVICES = {
    "muzpa":      ("muzpa_sess",             check_muzpa),
    "deezer":     ("deezer_arl",             check_deezer),
    "soundcloud": ("soundcloud_oauth_token", check_soundcloud),
}


def check(service: str, value: str) -> tuple[bool, str]:
    _column, fn = SERVICES[service]
    return fn(value)


def normalize(service: str, value: str) -> str:
    """Limpiar el valor antes de guardarlo (mismo criterio que el check)."""
    value = value.strip()
    if service == "soundcloud":
        value = value.removeprefix("OAuth ").strip()
    return value
