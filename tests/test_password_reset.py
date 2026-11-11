"""Recuperar la contraseña por email y cambiarla desde Configuración."""
from __future__ import annotations

from unittest.mock import patch

from app.models.user import User
from app.services.auth import authenticate, hash_password, make_reset_token, make_session_token

HOST = {"host": "localhost"}  # TrustedHostMiddleware rechaza "testserver"


def _alumno(db, email="dj@ejemplo.com", password="vieja1234"):
    u = User(username="alumno", hashed_password=hash_password(password), email=email)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_login_tiene_link_a_recuperar(client):
    client.cookies.clear()
    resp = client.get("/login", headers=HOST)
    assert resp.status_code == 200
    assert 'href="/recuperar"' in resp.text


def test_recuperar_es_publica_y_no_revela_si_el_email_existe(client, db_session):
    client.cookies.clear()
    _alumno(db_session)
    assert client.get("/recuperar", headers=HOST, follow_redirects=False).status_code == 200
    with patch("app.api.auth.send_email") as send:
        existe = client.post("/recuperar", data={"email": " DJ@Ejemplo.com "}, headers=HOST)
        no_existe = client.post("/recuperar", data={"email": "nadie@ejemplo.com"}, headers=HOST)
    assert existe.status_code == no_existe.status_code == 200
    assert "Si hay una cuenta con ese email" in existe.text
    assert "Si hay una cuenta con ese email" in no_existe.text
    send.assert_called_once()
    to, _subject, body = send.call_args.args
    assert to == "dj@ejemplo.com"
    assert "/restablecer?token=" in body


def test_link_cambia_la_contrasena_y_sirve_una_sola_vez(client, db_session):
    client.cookies.clear()
    u = _alumno(db_session)
    token = make_reset_token(u)
    assert client.get(f"/restablecer?token={token}", headers=HOST).status_code == 200

    resp = client.post("/restablecer", headers=HOST, follow_redirects=False,
                       data={"token": token, "password": "nueva12345", "password2": "nueva12345"})
    assert resp.status_code == 303
    assert authenticate("alumno", "nueva12345", db_session)
    assert not authenticate("alumno", "vieja1234", db_session)

    # El hash cambió: el mismo link ya no vale.
    again = client.post("/restablecer", headers=HOST,
                        data={"token": token, "password": "otra123456", "password2": "otra123456"})
    assert again.status_code == 400
    assert authenticate("alumno", "nueva12345", db_session)


def test_link_invalido_o_contrasena_corta(client, db_session):
    client.cookies.clear()
    u = _alumno(db_session)
    assert client.get("/restablecer?token=basura", headers=HOST).status_code == 400
    corta = client.post("/restablecer", headers=HOST,
                        data={"token": make_reset_token(u), "password": "corta", "password2": "corta"})
    assert corta.status_code == 400
    assert authenticate("alumno", "vieja1234", db_session)


def test_cambiar_contrasena_desde_configuracion(client, db_session):
    u = _alumno(db_session)
    client.cookies.set("mc_session", make_session_token(u.id))

    mal = client.post("/settings/password", headers=HOST, follow_redirects=False,
                      data={"current": "equivocada", "password": "nueva12345", "password2": "nueva12345"})
    assert "cuenta=pw_wrong" in mal.headers["location"]

    ok = client.post("/settings/password", headers=HOST, follow_redirects=False,
                     data={"current": "vieja1234", "password": "nueva12345", "password2": "nueva12345"})
    assert "cuenta=pw_ok" in ok.headers["location"]
    db_session.expire_all()
    assert authenticate("alumno", "nueva12345", db_session)


def test_guardar_email_desde_configuracion(client, db_session):
    otro = User(username="otro", hashed_password="x", email="usado@ejemplo.com")
    db_session.add(otro)
    u = _alumno(db_session, email=None)
    client.cookies.set("mc_session", make_session_token(u.id))

    dup = client.post("/settings/email", data={"email": "USADO@ejemplo.com"}, headers=HOST, follow_redirects=False)
    assert "cuenta=email_dup" in dup.headers["location"]

    ok = client.post("/settings/email", data={"email": "Mio@Ejemplo.com"}, headers=HOST, follow_redirects=False)
    assert "cuenta=email_ok" in ok.headers["location"]
    db_session.expire_all()
    assert db_session.get(User, u.id).email == "mio@ejemplo.com"
