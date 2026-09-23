"""
Self-check del agente: captura de cookie y propagación de credencial vencida.

Correr:  python test_agent_auth.py     (desde la carpeta agent/)

Sin framework a propósito — es el chequeo mínimo que falla si se rompe la lógica
que evita que un 401 se grabe como "not_found" permanente en el servidor.
"""
from __future__ import annotations

import json
import sys
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

import login_window
from download import muzpa, orchestrator
from download.auth_error import AuthExpired


def test_cookie_extraction():
    """pywebview devuelve list[SimpleCookie]; así se saca el valor."""
    jar = SimpleCookie()
    jar["SESS"] = "abc123"
    assert login_window._cookie_value([jar], "SESS") == "abc123"
    assert login_window._cookie_value([jar], "arl") == ""
    assert login_window._cookie_value([], "SESS") == ""
    assert login_window._cookie_value(None, "SESS") == ""


def test_argv_parsing():
    assert login_window.main([]) == 2
    assert login_window.main(["--login", "muzpa"]) == 2
    assert login_window.main(["--login", "inventado", "out.json"]) == 2


def test_requires_token_from_env(monkey):
    """Sin TM_TOKEN no se abre la ventana: no habría con qué validar."""
    import os
    monkey(os, "environ", {})
    assert login_window.run("muzpa", "out.json") == 2


def test_only_validated_cookies_are_accepted(monkey):
    """
    Deezer setea un `arl` anónimo al cargar la página. Aceptar la primera cookie
    que aparece cerraba la ventana antes de que el usuario escribiera nada.
    """
    import tempfile
    from http.cookies import SimpleCookie

    anon, real = SimpleCookie(), SimpleCookie()
    anon["arl"] = "arl-anonimo"
    real["arl"] = "arl-logueado"

    class FakeWindow:
        def __init__(self):
            self.reads = 0
            self.destroyed = False

        def get_cookies(self):
            self.reads += 1
            return [anon] if self.reads < 3 else [real]  # login al 3er poll

        def destroy(self):
            self.destroyed = True

    validated = []

    def fake_save(_api, _token, _service, value):
        validated.append(value)
        return (value == "arl-logueado"), "ok"

    monkey(login_window, "_save", fake_save)
    monkey(login_window, "_POLL_SECONDS", 0.01)

    out = Path(tempfile.gettempdir()) / "tm_test_watch.json"
    out.unlink(missing_ok=True)
    window = FakeWindow()
    login_window._watch(window, "deezer", out, "http://x", "tok")

    assert validated == ["arl-anonimo", "arl-logueado"], validated
    assert window.destroyed
    assert json.loads(out.read_text())["ok"] is True
    out.unlink(missing_ok=True)


def test_services_cover_the_three_accounts():
    assert set(login_window.SERVICES) == {"muzpa", "deezer", "soundcloud"}
    for url, cookie, label in login_window.SERVICES.values():
        assert url.startswith("https://") and cookie and label


def _raise_status(status):
    def _fake_get(*_a, **_kw):
        request = httpx.Request("GET", "https://srv.muzpa.com/")
        return httpx.Response(status, request=request)
    return _fake_get


def test_muzpa_401_raises_instead_of_not_found(monkey):
    """Lo que antes devolvía "not_found" y quemaba el track para siempre."""
    for status in (401, 403):
        monkey(httpx, "get", _raise_status(status))
        try:
            muzpa.search("cualquier track", "sess-vencida")
        except AuthExpired as e:
            assert e.service == "muzpa"
        else:
            raise AssertionError(f"status {status} no levantó AuthExpired")


def test_muzpa_200_with_html_raises(monkey):
    """Sesión muerta que responde 200 con el HTML del login."""
    def _fake_get(*_a, **_kw):
        request = httpx.Request("GET", "https://srv.muzpa.com/")
        return httpx.Response(200, text="<html>login</html>", request=request)
    monkey(httpx, "get", _fake_get)
    try:
        muzpa.search("cualquier track", "sess-vencida")
    except AuthExpired:
        pass
    else:
        raise AssertionError("200 con HTML no levantó AuthExpired")


def test_muzpa_500_still_means_not_found(monkey):
    """Un error del servidor NO es una credencial vencida: no frenar el agente."""
    monkey(httpx, "get", _raise_status(500))
    track, status = muzpa.search("cualquier track", "sess-ok")
    assert track is None and status == "not_found"


def test_orchestrator_lets_auth_expired_through(monkey):
    """El except Exception genérico no se la puede tragar."""
    def _boom(*_a, **_kw):
        raise AuthExpired("muzpa")
    monkey(muzpa, "search", _boom)
    try:
        orchestrator.try_download("track", Path("."), {"muzpa_sess": "x"})
    except AuthExpired:
        pass
    else:
        raise AssertionError("try_download se tragó AuthExpired y devolvió un status")


def main() -> int:
    patches: list[tuple] = []

    def monkey(obj, name, value):
        patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn(monkey) if fn.__code__.co_argcount else fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {name}: {e}")
        finally:
            while patches:
                obj, attr, original = patches.pop()
                setattr(obj, attr, original)

    print("todo ok" if not failures else f"{failures} fallaron")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
