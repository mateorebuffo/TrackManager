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

_POLL_SECONDS = 1.5
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


def _save(api_url: str, token: str, service: str, value: str) -> tuple[bool, str]:
    """POST /api/me/credentials — valida contra el servicio y guarda solo si pasa."""
    try:
        r = httpx.post(
            f"{api_url}/api/me/credentials",
            headers={"Authorization": f"Bearer {token}"},
            json={"service": service, "value": value},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        return bool(data.get("ok")), str(data.get("msg", ""))
    except Exception as e:
        return False, f"Error de conexión: {e}"


def _watch(window, service: str, out_path: Path, api_url: str, token: str) -> None:
    """Sondear la cookie; cerrar cuando el servidor confirme que es válida."""
    # ponytail: polling cada 1.5s en vez de hookear el evento de navegación del
    # backend. Solo se valida cuando el valor cambia, así que no es chatty.
    _url, cookie_name, _label = SERVICES[service]
    tried: set[str] = set()
    while True:
        time.sleep(_POLL_SECONDS)
        try:
            value = _cookie_value(window.get_cookies(), cookie_name)
        except Exception:
            return  # ventana cerrada por el usuario

        if not value or value in tried:
            continue
        tried.add(value)

        ok, msg = _save(api_url, token, service, value)
        if ok:
            out_path.write_text(json.dumps({"ok": True, "msg": msg}), encoding="utf-8")
            try:
                window.destroy()
            except Exception:
                pass
            return


def run(service: str, out_path: str) -> int:
    """Abrir el login de `service`. 0 = credencial validada y guardada."""
    if service not in SERVICES:
        print(f"servicio desconocido: {service}", file=sys.stderr)
        return 2

    token = os.environ.get("TM_TOKEN", "")
    if not token:
        print("falta TM_TOKEN", file=sys.stderr)
        return 2
    api_url = os.environ.get("TM_API_URL", _DEFAULT_API_URL).rstrip("/")

    import webview  # import tardío: arrastra pythonnet, solo se paga en el subproceso

    url, _cookie_name, label = SERVICES[service]
    out = Path(out_path)
    out.unlink(missing_ok=True)  # nunca devolver una captura vieja

    window = webview.create_window(
        f"Conectar {label} — Track Manager",
        url,
        width=980,
        height=760,
    )
    webview.start(
        _watch,
        (window, service, out, api_url, token),
        private_mode=False,                        # sesión persistente: reconectar rara vez pide la clave
        storage_path=str(_storage_dir(service)),
    )
    return 0 if out.exists() else 1


def main(argv: list[str]) -> int:
    """argv es sys.argv[1:], con la forma: --login <servicio> <archivo_salida>"""
    if len(argv) != 3 or argv[0] != "--login":
        print("uso: --login <servicio> <archivo_salida>", file=sys.stderr)
        return 2
    return run(argv[1], argv[2])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
