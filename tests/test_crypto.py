"""
Cifrado de credenciales en reposo.

Lo que se verifica no es "Fernet funciona" —eso ya está probado río arriba— sino
las decisiones propias: que los vacíos no se rompan, que sea idempotente, que un
valor viejo en claro siga leyéndose, y sobre todo que lo que llega a la base
NO sea el texto plano.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import crypto


# ── La primitiva ─────────────────────────────────────────────────────────────

def test_ida_y_vuelta():
    assert crypto.decrypt(crypto.encrypt("arl-secreto")) == "arl-secreto"


def test_el_cifrado_no_contiene_el_original():
    cifrado = crypto.encrypt("arl-secreto")
    assert "arl-secreto" not in cifrado
    assert crypto.esta_cifrado(cifrado)


@pytest.mark.parametrize("vacio", ["", None])
def test_los_vacios_pasan_de_largo(vacio):
    """`if not us.muzpa_sess` tiene que seguir funcionando."""
    assert crypto.encrypt(vacio) == vacio
    assert crypto.decrypt(vacio) == vacio


def test_cifrar_dos_veces_no_duplica():
    """La migración tiene que poder correrse de nuevo sin romper nada."""
    una = crypto.encrypt("valor")
    dos = crypto.encrypt(una)
    assert dos == una
    assert crypto.decrypt(dos) == "valor"


def test_un_valor_viejo_en_claro_se_lee_igual():
    """Filas que todavía no pasaron por la migración."""
    assert crypto.decrypt("arl-sin-cifrar") == "arl-sin-cifrar"


def test_dos_cifrados_del_mismo_texto_son_distintos():
    """IV al azar: por eso estas columnas no se pueden filtrar por igualdad."""
    assert crypto.encrypt("igual") != crypto.encrypt("igual")


def test_si_cambia_la_secret_key_no_explota(monkeypatch):
    """
    Rotar SECRET_KEY vuelve ilegibles las credenciales. Tiene que degradar a
    "sin conectar" y no tirar una excepción en la cara del usuario.
    """
    cifrado = crypto.encrypt("arl-secreto")
    from app.config import settings
    monkeypatch.setattr(settings, "secret_key", "otra-clave-totalmente-distinta")
    assert crypto.decrypt(cifrado) == ""


# ── La columna ───────────────────────────────────────────────────────────────

def test_la_base_no_guarda_el_texto_plano(db_session):
    """El test que justifica todo esto."""
    u = User(username="cifrado", hashed_password="x")
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)

    db_session.add(UserSettings(user_id=u.id, muzpa_sess="SESS-SUPER-SECRETO",
                                deezer_arl="ARL-SUPER-SECRETO"))
    db_session.commit()
    db_session.expire_all()

    # Leído por el ORM: texto plano, los call sites no se enteran de nada.
    us = db_session.query(UserSettings).filter_by(user_id=u.id).first()
    assert us.muzpa_sess == "SESS-SUPER-SECRETO"
    assert us.deezer_arl == "ARL-SUPER-SECRETO"

    # Leído en crudo, como lo vería un dump de la base: cifrado.
    crudo = db_session.execute(
        __import__("sqlalchemy").text(
            "SELECT muzpa_sess, deezer_arl FROM user_settings WHERE user_id = :u"),
        {"u": u.id},
    ).first()
    assert "SUPER-SECRETO" not in (crudo[0] or ""), "la cookie de Muzpa quedó en claro"
    assert "SUPER-SECRETO" not in (crudo[1] or ""), "el ARL de Deezer quedó en claro"
    assert crudo[0].startswith("enc:v1:")


def test_el_agente_sigue_recibiendo_el_valor_usable(client, db_session):
    """
    De punta a punta: si el agente recibiera el texto cifrado, las descargas
    dejarían de funcionar sin que ningún otro test se entere.
    """
    user = db_session.query(User).filter_by(username="tester").first()
    us = db_session.query(UserSettings).filter_by(user_id=user.id).first()
    us.muzpa_sess = "sess-para-el-agente"
    db_session.commit()

    r = client.get("/api/me/settings",
                   headers={"Authorization": f"Bearer {user.api_token}", "host": "localhost"})
    assert r.status_code == 200
    assert r.json()["muzpa_sess"] == "sess-para-el-agente"
