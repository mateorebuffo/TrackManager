"""Descubrir: ranking de productores/sellos y recomendaciones (Cosine Club + Discogs)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.download_job import DownloadJob
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User
from app.services import discovery

AUTH = {"Authorization": "Bearer tok-test", "host": "localhost"}  # token del fixture `client`


def _track(db, user_id, artist, title, status=TrackStatus.downloaded, label=None):
    st = SourceTrack(source="soundcloud", source_track_id=f"{user_id}-{artist}-{title}",
                     raw_title=f"{artist} - {title}", user_id=user_id,
                     raw_metadata_json={"label": label} if label else {})
    db.add(st)
    db.flush()
    nt = NormalizedTrack(source_track_id_fk=st.id, normalized_artist=artist, normalized_title=title)
    db.add(nt)
    db.flush()
    ri = ReviewItem(normalized_track_id_fk=nt.id, status=status)
    db.add(ri)
    db.commit()
    return st, ri


def _tester(db):
    return db.query(User).filter_by(username="tester").one()


@pytest.fixture
def limpio(monkeypatch):
    monkeypatch.setattr(discovery, "_releases", {})
    monkeypatch.setattr(discovery, "_recs", {})
    monkeypatch.setattr(discovery.settings, "cosine_api_key", "k")


def test_ranking_cuenta_colaboraciones_y_sellos_sin_mezclar_usuarios(client, db_session):
    uid = _tester(db_session).id
    otro = User(username="otro", hashed_password="x", api_token="tok-otro")
    db_session.add(otro)
    db_session.commit()
    _track(db_session, uid, "Priku & Traumer", "Operation", label="Cadenza")
    _track(db_session, uid, "traumer", "Spot", label="Cadenza")
    _track(db_session, uid, "Rossi.", "Pendiente", status=TrackStatus.pending, label="Phonica")
    _track(db_session, uid, "Sin Sello", "X")
    _track(db_session, otro.id, "Ajeno", "Y", label="Ajena")

    r = discovery.rankings(db_session, uid)
    # Sin distinguir mayúsculas; se muestra la forma del track más nuevo.
    assert r["producers"] == [("traumer", 2), ("Sin Sello", 1), ("Priku", 1)]
    assert r["labels"] == [("Cadenza", 2)]
    assert r["total"] == 3 and r["unlabeled"] == 1

    html = client.get("/descubrir").text
    assert "traumer" in html and "Cadenza" in html
    assert "Ajeno" not in html and "Phonica" not in html


def test_recomienda_productores_y_sellos_nuevos(client, db_session, monkeypatch, limpio):
    uid = _tester(db_session).id
    seed, _ = _track(db_session, uid, "Traumer", "Spot")  # sin sello: se completa de Discogs
    _track(db_session, uid, "Priku", "Operation", label="Cadenza")

    monkeypatch.setattr(discovery, "_cosine", lambda seeds: [
        {"query": "Traumer - Spot",
         "track": {"external_link": "https://discogs.com/release/1"},
         "similar_tracks": [
             {"id": "a", "name": "Rossi. - Uno", "artist": "Rossi.", "score": 0.95,
              "external_link": "https://discogs.com/release/10", "video_uri": "https://youtube.com/watch?v=a"},
             {"id": "b", "name": "Traumer & Newbie - Dos", "artist": "Traumer & Newbie", "score": 0.8,
              "external_link": "https://discogs.com/release/11"},
             {"id": "c", "name": "Rossi. - Tres", "artist": "Rossi.", "score": 0.5},
         ]},
    ])
    labels = {"1": "Cadenza (2)", "10": "Phonica", "11": "Cadenza"}
    def fake_get(url, **_kw):
        rid = url.rsplit("/", 1)[1]
        return SimpleNamespace(status_code=200, json=lambda: {"labels": [{"name": labels[rid], "id": int(rid)}]})
    monkeypatch.setattr(discovery.httpx, "get", fake_get)

    recs = discovery.recommend(db_session, uid)
    assert [(p["name"], round(p["score"], 2)) for p in recs["producers"]] == [("Rossi.", 1.45), ("Newbie", 0.8)]
    assert recs["producers"][0]["track"]["because"] == "Traumer - Spot"
    assert [l["label"] for l in recs["labels"]] == ["Phonica"]  # Cadenza ya la conoce
    db_session.refresh(seed)
    assert seed.raw_metadata_json["label"] == "Cadenza"

    html = client.get("/descubrir/recomendaciones").text
    assert "Rossi." in html and "Phonica" in html and "porque bajaste" in html


def test_sin_api_key_avisa(client, db_session, monkeypatch, limpio):
    monkeypatch.setattr(discovery.settings, "cosine_api_key", "")
    assert "COSINE_API_KEY" in client.get("/descubrir/recomendaciones").text


def test_el_agente_manda_el_sello_al_completar(client, db_session):
    uid = _tester(db_session).id
    st, ri = _track(db_session, uid, "Mr. Fingers", "Can You Feel It", status=TrackStatus.queued)
    job = DownloadJob(user_id=uid, review_id=ri.id, query="Mr. Fingers - Can You Feel It")
    db_session.add(job)
    db_session.commit()

    r = client.post(f"/api/download-jobs/{job.id}/complete", headers=AUTH,
                    json={"status": "completed", "label": "TRAX Records"})
    assert r.status_code == 200
    db_session.refresh(st)
    assert st.raw_metadata_json["label"] == "TRAX Records"


def test_un_solo_tema_no_acapara_las_recomendaciones():
    """Swoy - Another Life tenía 10 vecinos con 0.9+ y llenaba toda la lista."""
    items = [{"name": f"swoy{i}", "seed": "Swoy - Another Life"} for i in range(8)]
    items += [{"name": "stussy", "seed": "Chris Stussy - Blueprint"}]
    top = discovery._spread(items, 4, lambda it: it["seed"])
    assert [it["name"] for it in top] == ["swoy0", "swoy1", "stussy", "swoy2"]


def test_las_semillas_salen_de_los_productores_que_mas_bajo(db_session, client, monkeypatch, limpio):
    """Antes eran los últimos 50 bajados: recomendaba "porque bajaste Swoy" y no por Sublee."""
    uid = _tester(db_session).id
    for i in range(4):
        _track(db_session, uid, "Sublee", f"Tema {i}")
    _track(db_session, uid, "Swoy", "Another Life")  # el más nuevo, pero con un solo tema
    enviadas = []
    monkeypatch.setattr(discovery, "_cosine", lambda seeds: enviadas.extend(seeds) or [])
    discovery.recommend(db_session, uid)
    assert enviadas[:3] == ["Sublee - Tema 3", "Sublee - Tema 2", "Sublee - Tema 1"]
    assert enviadas[3] == "Swoy - Another Life" and "Sublee - Tema 0" in enviadas


def test_tocar_un_productor_o_sello_lista_sus_tracks(client, db_session):
    uid = _tester(db_session).id
    _track(db_session, uid, "Priku & Traumer", "Operation", label="Cadenza")
    _track(db_session, uid, "Traumer", "Spot", label="Phonica")
    _track(db_session, uid, "Traumer", "Pendiente", status=TrackStatus.pending)

    assert 'href="/descubrir/tracks?productor=Traumer"' in client.get("/descubrir").text

    html = client.get("/descubrir/tracks", params={"productor": "traumer"}).text
    assert "Operation" in html and "Spot" in html and "Pendiente" not in html
    assert 'href="/descubrir"' in html  # la flecha para volver

    html = client.get("/descubrir/tracks", params={"sello": "Cadenza"}).text
    assert "Operation" in html and "Spot" not in html

    sin_filtro = client.get("/descubrir/tracks", follow_redirects=False)
    assert sin_filtro.status_code == 302 and sin_filtro.headers["location"] == "/descubrir"


def test_nuevas_sugerencias_no_repiten_y_al_agotarse_vuelven_a_empezar(db_session, client, monkeypatch, limpio):
    uid = _tester(db_session).id
    _track(db_session, uid, "Traumer", "Spot", label="Cadenza")
    monkeypatch.setattr(discovery, "TOP", 1)
    monkeypatch.setattr(discovery, "_cosine", lambda seeds: [{"query": "Traumer - Spot", "similar_tracks": [
        {"id": "a", "artist": "Rossi.", "score": 0.9, "external_link": "https://discogs.com/release/10"},
        {"id": "b", "artist": "Newbie", "score": 0.8},
    ]}])
    monkeypatch.setattr(discovery.httpx, "get", lambda url, **_kw: SimpleNamespace(
        status_code=200, json=lambda: {"labels": [{"name": "Phonica", "id": 1}]}))

    nombres = lambda r: ([p["name"] for p in r["producers"]], [l["label"] for l in r["labels"]])
    assert nombres(discovery.recommend(db_session, uid)) == (["Rossi."], ["Phonica"])
    assert nombres(discovery.recommend(db_session, uid)) == (["Rossi."], ["Phonica"])  # caché
    assert nombres(discovery.recommend(db_session, uid, fresh=True)) == (["Newbie"], [])
    assert nombres(discovery.recommend(db_session, uid, fresh=True)) == (["Rossi."], ["Phonica"])
