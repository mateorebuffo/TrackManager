"""
/auto-download/status: el resumen liviano que consulta cada página para el
indicador de "Cola", el aviso de descarga activa y el aviso al terminar.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.download_job import DownloadJob, JobStatus
from app.models.user import User

HOST = {"host": "localhost"}


def _jobs(db, user, *statuses, batch=1):
    for i, st in enumerate(statuses):
        db.add(DownloadJob(user_id=user.id, review_id=1000 + i, query=f"t{i}", status=st, batch_id=batch))
    db.commit()


def test_requires_a_session(client):
    client.cookies.clear()
    r = client.get("/auto-download/status", headers=HOST, follow_redirects=False)
    assert r.status_code in (302, 303, 401)


def test_idle_shape(client):
    d = client.get("/auto-download/status", headers=HOST).json()
    assert d["state"] == "idle"
    for k in ("pending", "in_progress", "queued", "batch_id", "batch_total", "batch_done", "batch", "agent_online"):
        assert k in d


def test_pending_with_agent_closed_is_waiting_not_downloading(client, db_session):
    """Lo pendiente con el agente cerrado no avanza: no se puede mostrar como 'descargando'."""
    user = db_session.query(User).first()
    user.agent_seen_at = None
    _jobs(db_session, user, JobStatus.pending, JobStatus.pending, JobStatus.completed)
    d = client.get("/auto-download/status", headers=HOST).json()
    assert d["state"] == "waiting"
    assert (d["batch_done"], d["batch_total"]) == (1, 3)


def test_pending_with_agent_open_is_downloading(client, db_session):
    user = db_session.query(User).first()
    user.agent_seen_at = datetime.now(timezone.utc)
    _jobs(db_session, user, JobStatus.pending, JobStatus.not_found)
    d = client.get("/auto-download/status", headers=HOST).json()
    assert d["state"] == "downloading"
    assert d["batch"]["not_found"] == 1


def test_single_track_auto_without_batch_counts_as_active(client, db_session):
    """El "Auto" de un track encola sin tanda: no tiene que mostrar el progreso de otra tanda vieja."""
    user = db_session.query(User).first()
    user.agent_seen_at = datetime.now(timezone.utc)
    _jobs(db_session, user, JobStatus.completed, JobStatus.completed, batch=3)   # tanda vieja terminada
    db_session.add(DownloadJob(user_id=user.id, review_id=2000, query="uno", status=JobStatus.pending, batch_id=None))
    db_session.commit()
    d = client.get("/auto-download/status", headers=HOST).json()
    assert d["state"] == "downloading"
    assert d["batch_id"] is None
    assert (d["batch_done"], d["batch_total"]) == (0, 1)


def test_removing_a_track_from_the_queue_cancels_its_download(client, db_session):
    """Sin esto, la cola quedaba vacía pero la app seguía mostrando 'esperando al agente'."""
    from app.models.review_item import ReviewItem, TrackStatus
    client.post("/sync/soundcloud", headers=HOST)          # el collector mock trae tracks
    item = db_session.query(ReviewItem).first()
    item.status = TrackStatus.queued
    user = db_session.query(User).first()
    user.agent_seen_at = None
    db_session.add(DownloadJob(user_id=user.id, review_id=item.id, query="x", status=JobStatus.pending, batch_id=1))
    db_session.commit()
    assert client.get("/auto-download/status", headers=HOST).json()["state"] == "waiting"

    client.post(f"/review/{item.id}/requeue/form", headers=HOST, follow_redirects=False)
    assert client.get("/auto-download/status", headers=HOST).json()["state"] == "idle"


def test_finished_batch_reports_its_summary(client, db_session):
    user = db_session.query(User).first()
    _jobs(db_session, user, JobStatus.completed, JobStatus.completed, JobStatus.vinyl_only, batch=7)
    d = client.get("/auto-download/status?batch_id=7", headers=HOST).json()
    assert d["state"] == "idle"
    assert d["batch"]["completed"] == 2 and d["batch"]["vinyl_only"] == 1
    assert d["batch_done"] == d["batch_total"] == 3
