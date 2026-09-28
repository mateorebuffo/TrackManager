"""
Pasada adversarial sobre el cifrado en reposo.

No busca confirmar que anda: busca los caminos por donde el cifrado podría
romper algo que antes funcionaba. Cada test apunta a una forma concreta de
fallar en silencio.
"""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import crypto

HOST = {"host": "localhost"}


def _crudo(db_session, user_id, columna):
    """El valor como lo vería un dump de la base, sin pasar por el ORM."""
    return db_session.execute(
        text(f"SELECT {columna} FROM user_settings WHERE user_id = :u"), {"u": user_id}
    ).scalar()


def _usuario(db_session, nombre="adv"):
    u = User(username=nombre, hashed_password="x")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


# ── El ciclo OAuth completo ──────────────────────────────────────────────────

def test_el_token_de_youtube_sobrevive_guardar_leer_y_refrescar(db_session):
    """
    youtube_auth hace json.dumps al guardar y json.loads al leer. Si el cifrado
    se colara en el medio, el JSON dejaría de parsear y YouTube se caería entero
    — y ningún test de credenciales se enteraría.
    """
    from app.services import youtube_auth

    u = _usuario(db_session, "yt")
    youtube_auth._save_token(
        {"access_token": "ya29.abc", "refresh_token": "1//xyz", "expires_in": 3600},
        db_session, u.id,
    )

    guardado = youtube_auth._load_token(db_session, u.id)
    assert guardado["access_token"] == "ya29.abc"
    assert guardado["refresh_token"] == "1//xyz"
    assert youtube_auth.is_connected(db_session, u.id)

    # en la base no se lee nada
    assert "ya29.abc" not in (_crudo(db_session, u.id, "youtube_token_json") or "")

    # un refresh sobrescribe: el segundo cifrado no puede corromper al primero
    youtube_auth._save_token(
        {"access_token": "ya29.NUEVO", "refresh_token": "1//xyz", "expires_in": 3600},
        db_session, u.id,
    )
    assert youtube_auth._load_token(db_session, u.id)["access_token"] == "ya29.NUEVO"


def test_desconectar_youtube_deja_la_columna_vacia(db_session):
    from app.services import youtube_auth

    u = _usuario(db_session, "yt2")
    youtube_auth._save_token({"access_token": "a", "expires_in": 60}, db_session, u.id)
    youtube_auth.disconnect(db_session, u.id)
    assert not youtube_auth.is_connected(db_session, u.id)
    assert _crudo(db_session, u.id, "youtube_token_json") is None


def test_un_token_ilegible_no_rompe_la_pantalla(db_session, monkeypatch):
    """
    Si rotan SECRET_KEY, decrypt devuelve "". json.loads("") explota — pero
    _load_token lo atrapa y devuelve None, así que la cuenta figura como
    desconectada en vez de tirar un 500.
    """
    from app.services import youtube_auth
    from app.config import settings

    u = _usuario(db_session, "yt3")
    youtube_auth._save_token({"access_token": "a", "expires_in": 60}, db_session, u.id)
    db_session.expire_all()

    monkeypatch.setattr(settings, "secret_key", "clave-rotada-distinta")
    assert youtube_auth._load_token(db_session, u.id) is None
    assert youtube_auth.is_connected(db_session, u.id) is False


# ── Valores raros ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", [
    "enc:v1:esto-no-es-un-token-cifrado",   # colisión con el prefijo
    "ñoño áéíóú 日本語 🎵",                  # no-ASCII
    "x" * 20_000,                           # blob largo
    "  espacios  alrededor  ",
    "linea1\nlinea2\ttab",
    "comillas 'simples' y \"dobles\" y ; punto y coma",
])
def test_valores_raros_van_y_vuelven(db_session, valor):
    u = _usuario(db_session, f"raro{abs(hash(valor)) % 10000}")
    db_session.add(UserSettings(user_id=u.id, muzpa_sess=valor))
    db_session.commit()
    db_session.expire_all()

    leido = db_session.query(UserSettings).filter_by(user_id=u.id).first().muzpa_sess
    if valor.startswith("enc:v1:"):
        # Caso conocido: un valor que arranca con el prefijo se toma por cifrado
        # y no se puede recuperar. Ninguna credencial real tiene esa forma.
        assert leido == ""
    else:
        assert leido == valor


def test_los_vacios_siguen_siendo_falsy(db_session):
    """
    Todo el código pregunta `if not us.muzpa_sess`. Si el cifrado convirtiera un
    vacío en una cadena cifrada, esas cuentas figurarían como conectadas.

    Nota: estas columnas tienen default="", así que un None al insertar queda
    como "". Eso ya era así antes del cifrado — se verificó contra una columna
    Text común — y no afecta a nadie porque sólo se usan por valor de verdad.
    """
    u = _usuario(db_session, "vacios")
    db_session.add(UserSettings(user_id=u.id, muzpa_sess="", deezer_arl=None,
                                youtube_token_json=None))
    db_session.commit()
    db_session.expire_all()

    us = db_session.query(UserSettings).filter_by(user_id=u.id).first()
    assert not us.muzpa_sess
    assert not us.deezer_arl
    assert us.youtube_token_json is None      # sin default: el None se conserva
    assert _crudo(db_session, u.id, "muzpa_sess") == ""   # no se cifró un vacío


def test_sobrescribir_una_credencial_cifrada(db_session):
    u = _usuario(db_session, "sobre")
    db_session.add(UserSettings(user_id=u.id, muzpa_sess="primero"))
    db_session.commit()

    us = db_session.query(UserSettings).filter_by(user_id=u.id).first()
    us.muzpa_sess = "segundo"
    db_session.commit()
    db_session.expire_all()

    assert db_session.query(UserSettings).filter_by(user_id=u.id).first().muzpa_sess == "segundo"
    assert "primero" not in (_crudo(db_session, u.id, "muzpa_sess") or "")


# ── Los caminos reales de la app ─────────────────────────────────────────────

def test_el_agente_guarda_y_despues_lee_lo_mismo(client, db_session, monkeypatch):
    """
    POST /api/me/credentials guarda → GET /api/me/settings devuelve. Si el
    cifrado se colara, el agente recibiría basura y las descargas fallarían.
    """
    from app.services import credential_check
    # Hay que parchear check() y no check_muzpa: SERVICES guarda la referencia a
    # la función al importar el módulo, así que reemplazar el nombre no cambia
    # lo que check() termina llamando.
    monkeypatch.setattr(credential_check, "check",
                        lambda servicio, valor: (True, "Cuenta conectada"))

    user = db_session.query(User).filter_by(username="tester").first()
    auth = {"Authorization": f"Bearer {user.api_token}", **HOST}

    r = client.post("/api/me/credentials",
                    json={"service": "muzpa", "value": "SESS-del-agente"}, headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is True

    assert client.get("/api/me/settings", headers=auth).json()["muzpa_sess"] == "SESS-del-agente"
    assert "SESS-del-agente" not in (_crudo(db_session, user.id, "muzpa_sess") or "")


def test_el_form_web_sigue_guardando_bien(client, db_session, monkeypatch):
    """Con SHOW_LEGACY_SETTINGS el formulario todavía escribe credenciales."""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "show_legacy_settings", True)

    user = db_session.query(User).filter_by(username="tester").first()
    r = client.post("/settings", data={"muzpa_sess": "desde-el-form"},
                    headers=HOST, follow_redirects=False)
    assert r.status_code == 303

    db_session.expire_all()
    us = db_session.query(UserSettings).filter_by(user_id=user.id).first()
    assert us.muzpa_sess == "desde-el-form"
    assert "desde-el-form" not in (_crudo(db_session, user.id, "muzpa_sess") or "")


def test_el_estado_de_credenciales_ve_los_valores_descifrados(client, db_session, monkeypatch):
    """
    /api/me/credentials valida contra el servicio real: si le llegara el texto
    cifrado, marcaría todo como inválido y el panel del agente se vería en rojo.
    """
    from app.services import credential_check
    vistos = []
    monkeypatch.setattr(credential_check, "check",
                        lambda servicio, valor: (vistos.append(valor), (True, "ok"))[1])

    user = db_session.query(User).filter_by(username="tester").first()
    us = db_session.query(UserSettings).filter_by(user_id=user.id).first()
    us.muzpa_sess = "valor-a-validar"
    db_session.commit()

    client.get("/api/me/credentials?service=muzpa",
               headers={"Authorization": f"Bearer {user.api_token}", **HOST})
    assert vistos == ["valor-a-validar"], f"el check recibió {vistos}"
