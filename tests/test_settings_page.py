"""
Tests de /settings tras mover las credenciales al agente.

Lo que más importa acá es que ocultar un campo NO borre su valor. Es un riesgo
real de este cambio: el POST itera una lista de campos y escribe cada uno con lo
que venga del form, así que un campo que no se renderiza pero sigue en la lista
se guarda vacío en cada "Guardar cambios". Es exactamente lo que venía pasando
con download_dir y folder_organize_mode.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.api import settings_page
from app.config import settings as app_settings
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services.auth import make_session_token

# El TestClient del conftest usa el host "testserver", que TrustedHostMiddleware
# rechaza (app/main.py:74). Todas las requests de acá van con un host permitido.
HOST = {"host": "localhost"}

_CREDENCIALES = ("soundcloud_oauth_token", "muzpa_sess", "deezer_arl")


@pytest.fixture
def legacy_off(monkeypatch):
    monkeypatch.setattr(app_settings, "show_legacy_settings", False)


@pytest.fixture
def legacy_on(monkeypatch):
    monkeypatch.setattr(app_settings, "show_legacy_settings", True)


class _SesionSinCerrar:
    """
    AuthMiddleware abre su propia sesión con SessionLocal() y la cierra al final,
    salteándose el dependency override del conftest. Para que vea el usuario de
    prueba le pasamos la sesión del test, protegida del close().
    """

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


@pytest.fixture
def usuario(db_session, client):
    u = User(username="dj", hashed_password="x", is_admin=False, api_token="tok123")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    us = UserSettings(
        user_id=u.id,
        soundcloud_oauth_token="sc-viejo",
        muzpa_sess="muzpa-viejo",
        deezer_arl="arl-viejo",
        download_full_eps=True,
    )
    db_session.add(us)
    db_session.commit()
    client.cookies.set("mc_session", make_session_token(u.id))
    return u


# ── Qué campos siguen activos ────────────────────────────────────────────────

def test_credentials_are_hidden_by_default(legacy_off):
    activos = {f[0] for f in settings_page._active_fields()}
    for campo in _CREDENCIALES:
        assert campo not in activos
    # las de Spotify siguen: no se configuran desde el agente
    assert "spotify_client_id" in activos
    assert "download_full_eps" in activos


def test_the_flag_brings_them_back(legacy_on):
    activos = {f[0] for f in settings_page._active_fields()}
    for campo in _CREDENCIALES:
        assert campo in activos


def test_dead_settings_are_gone_from_the_form(legacy_on):
    """
    download_dir y folder_organize_mode no los renderiza ninguna plantilla y no
    los lee nadie (el agente usa su agent.json), pero al estar en la lista cada
    guardado los pisaba con vacío.
    """
    todos = {f[0] for f in settings_page._FIELDS}
    assert "download_dir" not in todos
    assert "folder_organize_mode" not in todos


# ── La pantalla ──────────────────────────────────────────────────────────────

def test_page_hides_the_credential_inputs(client, usuario, legacy_off):
    html = client.get("/settings", headers=HOST).text
    for campo in _CREDENCIALES:
        assert f'name="{campo}"' not in html
    assert "verifyCredential('muzpa')" not in html
    # y explica dónde se configuran ahora
    assert "Cuentas" in html and "agente" in html


def test_page_shows_them_with_the_flag_on(client, usuario, legacy_on):
    html = client.get("/settings", headers=HOST).text
    for campo in _CREDENCIALES:
        assert f'name="{campo}"' in html
    assert "verifyCredential('muzpa')" in html


def test_spotify_stays_for_admins(client, db_session, legacy_off):
    admin = User(username="jefe", hashed_password="x", is_admin=True, api_token="t2")
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    client.cookies.set("mc_session", make_session_token(admin.id))

    html = client.get("/settings", headers=HOST).text
    assert 'name="spotify_client_id"' in html


# ── Lo importante: guardar no puede borrar lo oculto ─────────────────────────

def test_saving_does_not_wipe_hidden_credentials(client, db_session, usuario, legacy_off):
    resp = client.post(
        "/settings",
        data={"download_full_eps": "on"},
        headers=HOST,
        follow_redirects=False,
    )
    assert resp.status_code == 303

    us = db_session.query(UserSettings).filter_by(user_id=usuario.id).first()
    assert us.soundcloud_oauth_token == "sc-viejo"
    assert us.muzpa_sess == "muzpa-viejo"
    assert us.deezer_arl == "arl-viejo"


def test_saving_still_updates_what_is_visible(client, db_session, usuario, legacy_off):
    client.post("/settings", data={}, headers=HOST, follow_redirects=False)
    us = db_session.query(UserSettings).filter_by(user_id=usuario.id).first()
    # checkbox ausente = desmarcado, eso sí tiene que aplicarse
    assert us.download_full_eps is False
