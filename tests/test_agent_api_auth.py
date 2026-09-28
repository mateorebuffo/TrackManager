"""
Autenticación de los endpoints que consume el agente.

La propiedad que importa: **un token inválido no puede llegar a ningún endpoint
`/api/*`**. El middleware dejaba pasar cualquier header que empezara con
"Bearer " sin validarlo, así que la seguridad dependía de que cada handler se
acordara de chequear a mano. Un endpoint nuevo que se olvidara quedaba abierto —
fallaba abierto en vez de cerrado.

El test recorre las rutas registradas en vez de una lista escrita a mano: así
cubre también los endpoints que se agreguen después.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.main import app

HOST = {"host": "localhost"}
TOKEN_FALSO = {"Authorization": "Bearer no-existe-este-token", **HOST}

# Se autentican con la sesión del navegador, no con el token del agente.
_DE_NAVEGADOR = {"/api/generate-token", "/api/me/token", "/api/download-agent",
                 "/api/download-jobs/cancel-all-pending"}


def _rutas_del_agente():
    """(método, path) de cada endpoint /api/* que espera el token del agente."""
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or path in _DE_NAVEGADOR:
            continue
        for metodo in sorted(getattr(r, "methods", set()) - {"HEAD", "OPTIONS"}):
            yield metodo, path


def test_hay_rutas_para_revisar():
    """Si el filtro dejara la lista vacía, los tests de abajo pasarían por vacuidad."""
    assert len(list(_rutas_del_agente())) >= 5


@pytest.mark.parametrize("metodo,path", sorted(set(_rutas_del_agente())))
def test_un_token_invalido_no_entra(client, metodo, path, db_session):
    url = path.replace("{job_id}", "1")
    resp = client.request(metodo, url, headers=TOKEN_FALSO, json={},
                          follow_redirects=False)
    assert resp.status_code == 401, (
        f"{metodo} {path} respondió {resp.status_code} con un token inventado"
    )


@pytest.mark.parametrize("metodo,path", sorted(set(_rutas_del_agente())))
def test_sin_header_tampoco(client, metodo, path, db_session):
    """
    Sin Authorization el middleware manda al login. Lo que no puede pasar es que
    el endpoint conteste 200.
    """
    sin_cookie = {**HOST}
    client.cookies.clear()
    url = path.replace("{job_id}", "1")
    resp = client.request(metodo, url, headers=sin_cookie, json={},
                          follow_redirects=False)
    assert resp.status_code != 200, f"{metodo} {path} sirvió sin autenticación"


def test_un_token_valido_si_entra(client, db_session):
    """La contracara: el agente real tiene que poder trabajar."""
    from app.models.user import User
    user = db_session.query(User).filter_by(username="tester").first()
    resp = client.get("/api/me/settings",
                      headers={"Authorization": f"Bearer {user.api_token}", **HOST})
    assert resp.status_code == 200
    assert "muzpa_sess" in resp.json()


@pytest.mark.parametrize("metodo,path", sorted(set(_rutas_del_agente())))
def test_un_token_valido_no_se_rechaza_en_ninguna(client, db_session, metodo, path):
    """
    Consolidar la auth en una dependency podría haber roto el agente entero, y
    "todo devuelve 401" pasaría los tests de arriba con gloria. Acá se verifica
    lo contrario en cada ruta: con un token bueno, nunca 401.

    No se afirma 200: un job inexistente da 404 y un body vacío da 422, que son
    respuestas legítimas. Lo que no puede pasar es que rechace la autenticación.
    """
    from app.models.user import User
    user = db_session.query(User).filter_by(username="tester").first()
    url = path.replace("{job_id}", "999999")   # no existe: se espera 404, no 401
    resp = client.request(metodo, url, json={},
                          headers={"Authorization": f"Bearer {user.api_token}", **HOST},
                          follow_redirects=False)
    assert resp.status_code != 401, f"{metodo} {path} rechazó un token válido"
