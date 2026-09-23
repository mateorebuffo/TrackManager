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

    monkey(login_window, "_save", lambda a, t, s, v: fake_save(a, t, s, v) + (True,))
    monkey(login_window, "_POLL_SECONDS", 0.01)

    out = Path(tempfile.gettempdir()) / "tm_test_watch.json"
    out.unlink(missing_ok=True)
    window = FakeWindow()
    login_window._watch(window, "deezer", out, "http://x", "tok")

    assert validated == ["arl-anonimo", "arl-logueado"], validated
    assert window.destroyed
    assert json.loads(out.read_text())["ok"] is True
    out.unlink(missing_ok=True)


def test_services_cover_every_account():
    assert set(login_window.SERVICES) == {"muzpa", "deezer", "soundcloud"}
    for url, cookie, label in login_window.SERVICES.values():
        assert url.startswith("https://") and cookie and label
    assert set(login_window.OAUTH_SERVICES) == {"youtube"}
    for path, label in login_window.OAUTH_SERVICES.values():
        assert path.startswith("/") and label  # relativa: se le antepone TM_API_URL
    assert set(login_window.all_services()) == {"muzpa", "deezer", "soundcloud", "youtube"}


def test_agent_ui_lists_exactly_the_supported_services():
    """La UI y login_window no se pueden desincronizar."""
    import re
    source = (Path(__file__).resolve().parent / "agent.py").read_text(encoding="utf-8")
    block = source.split("_ACCOUNTS = [")[1].split("]")[0]
    listed = set(re.findall(r'\("(\w+)",', block))
    assert listed == set(login_window.all_services()), listed


def test_oauth_watcher_waits_for_server_confirmation(monkey):
    """YouTube no tiene cookie: cierra cuando el server dice que quedó conectado."""
    import tempfile

    class FakeWindow:
        def __init__(self):
            self.destroyed = False

        def get_cookies(self):
            return []

        def destroy(self):
            self.destroyed = True

    calls = {"n": 0}

    def fake_status(_api, _token, _service):
        calls["n"] += 1
        return {"ok": calls["n"] >= 3, "msg": "Credencial válida."}, ""

    monkey(login_window, "_status", fake_status)
    monkey(login_window, "_POLL_SECONDS", 0.01)

    out = Path(tempfile.gettempdir()) / "tm_test_oauth.json"
    out.unlink(missing_ok=True)
    window = FakeWindow()
    login_window._watch_oauth(window, "youtube", out, "http://x", "tok")

    assert calls["n"] == 3
    assert window.destroyed
    assert json.loads(out.read_text())["ok"] is True
    out.unlink(missing_ok=True)


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


def test_oauth_gives_up_when_server_lacks_the_endpoint(monkey):
    """
    Con el server sin deployear, /api/me/credentials daba 404 y la ventana
    quedaba esperando para siempre — el agente se veía colgado.
    """
    import tempfile

    class FakeWindow:
        def __init__(self):
            self.destroyed = False

        def get_cookies(self):
            return []

        def destroy(self):
            self.destroyed = True

    monkey(login_window, "_status", lambda *_a: ({}, "El servidor no tiene el endpoint"))
    monkey(login_window, "_POLL_SECONDS", 0.01)
    monkey(login_window, "_MAX_ERRORS", 3)

    out = Path(tempfile.gettempdir()) / "tm_test_404.json"
    out.unlink(missing_ok=True)
    window = FakeWindow()
    login_window._watch_oauth(window, "youtube", out, "http://x", "tok")

    assert window.destroyed, "la ventana quedo abierta para siempre"
    result = json.loads(out.read_text())
    assert result["ok"] is False
    assert "endpoint" in result["msg"]
    out.unlink(missing_ok=True)


def test_status_detects_a_server_that_does_not_know_the_service(monkey):
    """
    Un server viejo responde 200 con muzpa/deezer/soundcloud pero sin youtube.
    Sin detectarlo, la ventana esperaba para siempre una confirmación imposible.
    """
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"muzpa": {"ok": True}, "deezer": {"ok": True}, "soundcloud": {"ok": True}}

    monkey(login_window.httpx, "get", lambda *_a, **_kw: FakeResponse())

    info, error = login_window._status("http://x", "tok", "youtube")
    assert info == {}
    assert "no soporta youtube" in error

    info, error = login_window._status("http://x", "tok", "muzpa")
    assert error == "" and info == {"ok": True}


def test_cookie_watcher_retries_when_server_unreachable(monkey):
    """Un error de red no significa que la cookie no sirva: hay que reintentarla."""
    import tempfile
    from http.cookies import SimpleCookie

    jar = SimpleCookie()
    jar["SESS"] = "cookie-buena"

    class FakeWindow:
        def __init__(self):
            self.destroyed = False

        def get_cookies(self):
            return [jar]

        def destroy(self):
            self.destroyed = True

    attempts = {"n": 0}

    def flaky_save(_api, _token, _service, value):
        attempts["n"] += 1
        if attempts["n"] < 3:
            return False, "sin red", False       # no se pudo preguntar
        return True, "Credencial válida.", True  # ahora sí

    monkey(login_window, "_save", flaky_save)
    monkey(login_window, "_POLL_SECONDS", 0.01)

    out = Path(tempfile.gettempdir()) / "tm_test_retry.json"
    out.unlink(missing_ok=True)
    window = FakeWindow()
    login_window._watch(window, "muzpa", out, "http://x", "tok")

    assert attempts["n"] == 3, f"no reintento el mismo valor: {attempts['n']}"
    assert json.loads(out.read_text())["ok"] is True
    out.unlink(missing_ok=True)


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
