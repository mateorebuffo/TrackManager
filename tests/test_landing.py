"""La landing es pública: la ve quien todavía no tiene cuenta, y "Empezar" lleva al login."""


def test_landing_sin_sesion(client):
    client.cookies.clear()
    resp = client.get("/para-que-sirve", follow_redirects=False)
    assert resp.status_code == 200
    assert 'href="/login">Empezar' in resp.text
