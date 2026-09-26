"""
Tests de la pantalla de tracks: lo que un no-admin no tiene que ver.

Spotify es admin-only (la IP de Railway está bloqueada para tracks), así que
para el resto de los usuarios ni el filtro ni las menciones tienen sentido.
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


def _entrar(db_session, client, *, admin: bool):
    u = User(username="jefe" if admin else "alumno", hashed_password="x",
             is_admin=admin, api_token="tok")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    # onboarding_done para que / no redirija al asistente
    db_session.add(UserSettings(user_id=u.id, onboarding_done=True))
    db_session.commit()
    client.cookies.set("mc_session", make_session_token(u.id))
    return client.get("/tracks/pending", headers=HOST).text


def test_non_admin_does_not_see_the_spotify_filter(client, db_session):
    html = _entrar(db_session, client, admin=False)
    assert "source=spotify" not in html
    assert "soundcloud" in html  # los otros filtros siguen


def test_admin_still_sees_it(client, db_session):
    html = _entrar(db_session, client, admin=True)
    assert "source=spotify" in html


def test_non_admin_is_not_told_to_press_a_spotify_button(client, db_session):
    """
    El vacío nombraba ↻ Spotify, un botón que el no-admin no tiene en pantalla.

    Se busca esa cadena y no "Spotify" a secas: la palabra sigue apareciendo en
    el JS del selector de playlists, que para un no-admin es código muerto pero
    inofensivo.
    """
    html = _entrar(db_session, client, admin=False)
    assert "↻ Spotify" not in html
    assert "↻ SoundCloud" in html  # el mensaje sigue ahí, sin Spotify


def test_admin_is_still_told_about_spotify(client, db_session):
    html = _entrar(db_session, client, admin=True)
    assert "↻ Spotify" in html


def test_setup_banner_points_at_the_wizard(client, db_session):
    """
    Mandaba a /settings a cargar credenciales, y desde que se vinculan en el
    agente ese formulario ya no existe.
    """
    html = _entrar(db_session, client, admin=False)
    assert "/primeros-pasos" in html
    assert "Ir a Configuración" not in html
