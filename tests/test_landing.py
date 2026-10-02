"""La landing es pública: la ve quien todavía no tiene cuenta, y "Empezar" lleva al login."""


def test_landing_sin_sesion(client):
    client.cookies.clear()
    resp = client.get("/para-que-sirve", follow_redirects=False)
    assert resp.status_code == 200
    assert 'href="/login">Empezar' in resp.text


def test_ayuda_con_sesion(client):
    resp = client.get("/ayuda", follow_redirects=False)
    assert resp.status_code == 200
    assert 'id="problemas"' in resp.text


def test_ayuda_sin_sesion_va_al_login(client):
    client.cookies.clear()
    resp = client.get("/ayuda", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
