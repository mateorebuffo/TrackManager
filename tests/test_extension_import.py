"""La extensión de Chrome manda el tema que suena en Traxsource a Pendiente."""

AUTH = {"Authorization": "Bearer tok-test", "host": "localhost"}  # token del fixture `client`

PAYLOAD = {
    "source_track_id": "15024336",
    "url": "https://www.traxsource.com/track/15024336/side-quest-original-mix",
    "title": "Side Quest (Original Mix)",
    "artists": ["Rey Aguilar"],
    "duration_seconds": 428,
    "label": "Cadenza",
    "genre": "Minimal / Deep Tech",
    "release_date": "2026-09-11",
}


def test_agrega_a_pendiente_normalizado(client):
    resp = client.post("/api/tracks/import", json=PAYLOAD, headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "added"
    assert body["track"] == "Rey Aguilar - Side Quest"

    html = client.get("/tracks/pending").text
    assert 'data-copy="Rey Aguilar - Side Quest"' in html
    assert ">TX<" in html


def test_el_mismo_tema_dos_veces_no_se_duplica(client):
    primero = client.post("/api/tracks/import", json=PAYLOAD, headers=AUTH).json()
    segundo = client.post("/api/tracks/import", json=PAYLOAD, headers=AUTH).json()
    assert segundo["status"] == "exists"
    assert segundo["review_id"] == primero["review_id"]


def test_datos_incompletos_se_rechazan(client):
    for roto in ({**PAYLOAD, "title": ""}, {**PAYLOAD, "artists": []}):
        resp = client.post("/api/tracks/import", json=roto, headers=AUTH)
        assert resp.status_code == 422
