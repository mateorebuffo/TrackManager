"""
Tests del asistente de primeros pasos.

Mismo andamiaje que tests/test_settings_page.py: host permitido por
TrustedHostMiddleware y parche de SessionLocal para que AuthMiddleware vea la
base de prueba.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.models.user import User
from app.models.user_settings import UserSettings
from app.services.auth import make_session_token

HOST = {"host": "localhost"}


class _SesionSinCerrar:
    """AuthMiddleware abre su propia sesión con SessionLocal() y la cierra al final."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _auth_ve_la_db_de_prueba(db_session, monkeypatch):
    import app.auth_middleware as am
    monkeypatch.setattr(am, "SessionLocal", lambda: _SesionSinCerrar(db_session))


def _usuario(db_session, client, *, visto: bool | None = None):
    u = User(username=f"dj{id(db_session)}", hashed_password="x", api_token="tok")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    if visto is not None:
        db_session.add(UserSettings(user_id=u.id, onboarding_done=visto))
        db_session.commit()
    client.cookies.set("mc_session", make_session_token(u.id))
    return u


# ── El redirect de la raíz ───────────────────────────────────────────────────

def test_new_user_lands_on_the_wizard(client, db_session):
    _usuario(db_session, client)  # sin fila de settings: nunca lo vio
    r = client.get("/", headers=HOST, follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/primeros-pasos"


def test_user_who_already_saw_it_goes_to_the_app(client, db_session):
    _usuario(db_session, client, visto=True)
    r = client.get("/", headers=HOST, follow_redirects=False)
    assert r.headers["location"] == "/tracks/pending"


def test_a_settings_row_without_the_flag_still_counts_as_pending(client, db_session):
    """Los usuarios que ya existían tienen fila de settings pero nunca vieron esto."""
    _usuario(db_session, client, visto=False)
    r = client.get("/", headers=HOST, follow_redirects=False)
    assert r.headers["location"] == "/primeros-pasos"


# ── Marcarlo como visto ──────────────────────────────────────────────────────

def test_finishing_stops_the_redirect(client, db_session):
    usuario = _usuario(db_session, client)

    r = client.post("/primeros-pasos/listo", headers=HOST, follow_redirects=False)
    assert r.status_code == 303

    us = db_session.query(UserSettings).filter_by(user_id=usuario.id).first()
    assert us.onboarding_done is True

    r = client.get("/", headers=HOST, follow_redirects=False)
    assert r.headers["location"] == "/tracks/pending"


def test_skipping_is_the_same_as_finishing(client, db_session):
    """
    "Saltar por ahora" postea al mismo endpoint: en los dos casos el usuario ya
    decidió y no hay que volver a interrumpirlo.
    """
    usuario = _usuario(db_session, client)
    client.post("/primeros-pasos/listo", headers=HOST, follow_redirects=False)
    us = db_session.query(UserSettings).filter_by(user_id=usuario.id).first()
    assert us.onboarding_done is True


def test_finishing_does_not_wipe_existing_settings(client, db_session):
    """Marcar el asistente no puede tocar lo que el usuario ya tenía configurado."""
    usuario = _usuario(db_session, client)
    db_session.add(UserSettings(user_id=usuario.id, muzpa_sess="no-me-toques",
                                download_full_eps=True))
    db_session.commit()

    client.post("/primeros-pasos/listo", headers=HOST, follow_redirects=False)

    us = db_session.query(UserSettings).filter_by(user_id=usuario.id).first()
    assert us.muzpa_sess == "no-me-toques"
    assert us.download_full_eps is True
    assert us.onboarding_done is True


# ── La página ────────────────────────────────────────────────────────────────

def test_page_has_the_four_slides_and_the_screenshots(client, db_session):
    _usuario(db_session, client)
    html = client.get("/primeros-pasos", headers=HOST).text
    for n in range(1, 5):
        assert f'data-slide="{n}"' in html
    # Con ?v=: el CDN de Railway guarda los estaticos 4 horas, asi que regenerar
    # una captura no se veria hasta que expire si la URL no cambia.
    import re
    for png in ("agente-configuracion", "agente-cuentas"):
        m = re.search(rf"/static/onboarding/{png}\.png\?v=(\d+)", html)
        assert m, f"{png}.png sin ?v= — el CDN va a seguir sirviendo la vieja"
        assert int(m.group(1)) > 0
    assert "tok" in html  # el token, para copiar y pegar en el agente


def test_page_offers_to_generate_the_token_when_there_is_none(client, db_session):
    """
    El caso real de estreno: /admin/users/create no genera api_token, así que el
    usuario recién creado llega acá sin token y tiene que poder sacarlo desde el
    asistente — si no, el paso 2 no se puede completar.
    """
    u = User(username="recien", hashed_password="x", api_token=None)
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    client.cookies.set("mc_session", make_session_token(u.id))

    html = client.get("/primeros-pasos", headers=HOST).text
    assert "Generar token" in html
    # El boton se pasa por parametro: con event.target, despues del await el
    # event global ya no existe y el boton nunca cambiaba a "Copiar".
    assert "obGenerar(this)" in html


def test_page_stays_reachable_after_finishing(client, db_session):
    """Es el link "Ver los primeros pasos de nuevo" de Ajustes."""
    _usuario(db_session, client, visto=True)
    assert client.get("/primeros-pasos", headers=HOST).status_code == 200


def test_screenshots_exist_on_disk():
    """Si faltan, la página se ve rota y ningún otro test se entera."""
    from pathlib import Path
    estaticos = Path(__file__).resolve().parents[1] / "app" / "static" / "onboarding"
    for nombre in ("agente-configuracion.png", "agente-cuentas.png"):
        archivo = estaticos / nombre
        assert archivo.exists(), f"falta {nombre}: correr scripts/capture_agent_screenshots.py"
        assert archivo.stat().st_size > 5_000, f"{nombre} parece vacía"
