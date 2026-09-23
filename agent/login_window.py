"""
Ventana de login embebida — captura la cookie de sesión de un servicio.

Corre como subproceso:  TrackManagerAgent.exe --login <servicio> <archivo_salida>
El token del agente llega por la variable de entorno TM_TOKEN (no por argv, que
es legible por cualquier proceso de la máquina).

No puede correr dentro del agente: webview.start() bloquea el main thread y solo
puede llamarse una vez por proceso, lugar que ya ocupa el mainloop de tkinter.

El usuario se loguea en la página real del servicio dentro de una ventana WebView2
(incluida en Windows 10/11 con Edge). La cookie se lee del navegador embebido —
nunca se toca el navegador del usuario ni se descifra nada.

Quién decide que el login terminó: el servidor. No alcanza con que la cookie
aparezca — Deezer setea un `arl` para visitantes anónimos, así que esperar "a que
exista" cerraba la ventana antes de que el usuario escribiera nada. Cada valor
nuevo se manda a POST /api/me/credentials, que valida contra el servicio y solo
guarda si pasa; recién ahí se cierra la ventana.

YouTube va por otro camino: es OAuth de Google con redirect al server, así que la
ventana abre el flujo del propio Track Manager y el token lo guarda el callback.
La primera vez pide loguearse a Track Manager dentro de la ventana; como el perfil
del navegador embebido persiste, eso pasa una sola vez.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

# servicio -> (url de login, nombre de cookie, etiqueta para el título)
SERVICES = {
    "muzpa":      ("https://muzpa.com/",            "SESS",        "Muzpa"),
    "deezer":     ("https://www.deezer.com/login",  "arl",         "Deezer"),
    "soundcloud": ("https://soundcloud.com/signin", "oauth_token", "SoundCloud"),
}

# OAuth: no hay cookie que leer. La ventana abre el flujo del propio server, que
# guarda el token en su callback; acá solo se espera a que confirme que quedó
# conectado. La ruta es relativa a la API (se le antepone TM_API_URL).
OAUTH_SERVICES = {
    "youtube": ("/sync/youtube/connect", "YouTube"),
}


def all_services() -> dict[str, str]:
    """servicio -> etiqueta, para las dos formas de conexión."""
    return {**{s: v[2] for s, v in SERVICES.items()},
            **{s: v[1] for s, v in OAUTH_SERVICES.items()}}

_POLL_SECONDS = 1.5
_MAX_ERRORS = 5  # ~7s de servidor mudo antes de cerrar con el motivo
_DEFAULT_API_URL = "https://trackmanager.app"


def _storage_dir(service: str) -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) if os.name == "nt" else Path.home() / ".config"
    d = base / "TrackManager" / "browser" / service
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cookie_value(cookies, name: str) -> str:
    """
    Extraer una cookie del resultado de window.get_cookies().

    pywebview devuelve una lista de http.cookies.SimpleCookie, pero el shape exacto
    varía entre backends — de ahí el manejo de ambas formas.
    """
    for jar in cookies or []:
        try:
            morsel = jar.get(name)
        except AttributeError:
            continue
        if morsel is None:
            continue
        value = getattr(morsel, "value", morsel)
        if value:
            return str(value)
    return ""


def _save(api_url: str, token: str, service: str, value: str) -> tuple[bool, str, bool]:
    """
    POST /api/me/credentials — valida contra el servicio y guarda solo si pasa.

    Devuelve (ok, mensaje, servidor_respondio). El tercer valor separa "la cookie
    no sirve" de "no pude preguntar": si el servidor no contesta (o es una versión
    vieja sin el endpoint) no hay que descartar el valor ni seguir esperando para
    siempre.
    """
    try:
        r = httpx.post(
            f"{api_url}/api/me/credentials",
            headers={"Authorization": f"Bearer {token}"},
            json={"service": service, "value": value},
            timeout=30,
        )
    except Exception as e:
        return False, f"No se pudo contactar al servidor: {e}", False

    if r.status_code == 404:
        return False, ("El servidor no tiene el endpoint de credenciales. "
                       "Actualizá Track Manager antes de usar esta función."), False
    if r.status_code in (401, 403):
        return False, "Token del agente inválido. Revisalo en Ajustes (⚙).", True
    if r.status_code >= 500:
        return False, f"Error del servidor ({r.status_code}).", False
    try:
        data = r.json()
    except Exception:
        return False, f"Respuesta inesperada del servidor ({r.status_code}).", False
    return bool(data.get("ok")), str(data.get("msg", "")), True


def _finish(window, out_path: Path, ok: bool, msg: str) -> None:
    out_path.write_text(json.dumps({"ok": ok, "msg": msg}), encoding="utf-8")
    try:
        window.destroy()
    except Exception:
        pass


def _watch(window, service: str, out_path: Path, api_url: str, token: str) -> None:
    """Sondear la cookie; cerrar cuando el servidor confirme que es válida."""
    # ponytail: polling cada 1.5s en vez de hookear el evento de navegación del
    # backend. Solo se valida cuando el valor cambia, así que no es chatty.
    _url, cookie_name, _label = SERVICES[service]
    tried: set[str] = set()
    errors = 0
    while True:
        time.sleep(_POLL_SECONDS)
        try:
            value = _cookie_value(window.get_cookies(), cookie_name)
        except Exception:
            return  # ventana cerrada por el usuario

        if not value or value in tried:
            continue
        tried.add(value)

        ok, msg, reachable = _save(api_url, token, service, value)
        if ok:
            _finish(window, out_path, True, msg)
            return
        if not reachable:
            # No es que la cookie no sirva: no pudimos preguntar. Reintentar el
            # mismo valor y, si el servidor sigue mudo, cerrar con el motivo en
            # vez de dejar la ventana esperando para siempre.
            tried.discard(value)
            errors += 1
            if errors >= _MAX_ERRORS:
                _finish(window, out_path, False, msg)
                return
        else:
            errors = 0


def _status(api_url: str, token: str, service: str) -> tuple[dict, str]:
    """(info del servicio, error). Error no vacío = no se pudo preguntar."""
    try:
        r = httpx.get(f"{api_url}/api/me/credentials",
                      headers={"Authorization": f"Bearer {token}"}, timeout=30)
    except Exception as e:
        return {}, f"No se pudo contactar al servidor: {e}"

    if r.status_code == 404:
        return {}, ("El servidor no tiene el endpoint de credenciales. "
                    "Actualizá Track Manager antes de usar esta función.")
    if r.status_code in (401, 403):
        return {}, "Token del agente inválido. Revisalo en Ajustes (⚙)."
    try:
        payload = r.json()
    except Exception:
        return {}, f"Respuesta inesperada del servidor ({r.status_code})."

    # El endpoint devuelve TODOS los servicios que conoce. Si falta el nuestro, el
    # servidor es más viejo que este agente: sin esto la ventana esperaba para
    # siempre una confirmación que nunca iba a llegar.
    if service not in payload:
        return {}, (f"El servidor no soporta {service} todavía. "
                    "Actualizá Track Manager antes de usar esta función.")
    return (payload.get(service) or {}), ""


def _watch_oauth(window, service: str, out_path: Path, api_url: str, token: str) -> None:
    """Esperar a que el server confirme que el flujo OAuth terminó."""
    errors = 0
    while True:
        time.sleep(_POLL_SECONDS)
        try:
            window.get_cookies()  # solo para detectar que la ventana sigue abierta
        except Exception:
            return  # cerrada por el usuario

        info, error = _status(api_url, token, service)
        if error:
            # Un servidor viejo (sin el endpoint) dejaba la ventana esperando para
            # siempre y, con el subprocess en el hilo de tkinter, colgaba el agente.
            errors += 1
            if errors >= _MAX_ERRORS:
                _finish(window, out_path, False, error)
                return
            continue
        errors = 0
        if info.get("ok"):
            _finish(window, out_path, True, info.get("msg", "Conectado."))
            return


def run(service: str, out_path: str) -> int:
    """Abrir el login de `service`. 0 = credencial validada y guardada."""
    if service not in SERVICES and service not in OAUTH_SERVICES:
        print(f"servicio desconocido: {service}", file=sys.stderr)
        return 2

    token = os.environ.get("TM_TOKEN", "")
    if not token:
        print("falta TM_TOKEN", file=sys.stderr)
        return 2
    api_url = os.environ.get("TM_API_URL", _DEFAULT_API_URL).rstrip("/")

    import webview  # import tardío: arrastra pythonnet, solo se paga en el subproceso

    if service in OAUTH_SERVICES:
        path, label = OAUTH_SERVICES[service]
        url, watcher = api_url + path, _watch_oauth
    else:
        url, _cookie_name, label = SERVICES[service]
        watcher = _watch

    out = Path(out_path)
    out.unlink(missing_ok=True)  # nunca devolver una captura vieja

    window = webview.create_window(
        f"Conectar {label} — Track Manager",
        url,
        width=980,
        height=760,
    )
    webview.start(
        watcher,
        (window, service, out, api_url, token),
        private_mode=False,                        # sesión persistente: reconectar rara vez pide la clave
        storage_path=str(_storage_dir(service)),
    )
    if not out.exists():
        return 1  # el usuario cerró la ventana sin completar
    try:
        return 0 if json.loads(out.read_text(encoding="utf-8")).get("ok") else 1
    except Exception:
        return 1


def main(argv: list[str]) -> int:
    """argv es sys.argv[1:], con la forma: --login <servicio> <archivo_salida>"""
    if len(argv) != 3 or argv[0] != "--login":
        print("uso: --login <servicio> <archivo_salida>", file=sys.stderr)
        return 2
    return run(argv[1], argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
