"""
Estado resumido de las descargas de un usuario.

Lo consulta cada página (nav "Cola", banner de la cola, aviso al terminar), así
que tiene que ser barato: solo conteos, nunca la lista de jobs. La pantalla en
vivo sigue usando /auto-download/jobs/live, que sí trae la lista.
"""
from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.download_job import DownloadJob, JobStatus
from app.models.normalized_track import NormalizedTrack
from app.models.review_item import ReviewItem, TrackStatus
from app.models.source_track import SourceTrack
from app.models.user import User

# Un job en cualquiera de estos estados ya no va a cambiar.
_FINISHED = (JobStatus.completed, JobStatus.not_found, JobStatus.vinyl_only,
             JobStatus.bandcamp_only, JobStatus.failed, JobStatus.cancelled)


def download_status(db: Session, user: User, batch_id: int | None = None) -> dict:
    by_status = dict(
        db.query(DownloadJob.status, func.count())
        .filter(DownloadJob.user_id == user.id,
                DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]))
        .group_by(DownloadJob.status)
        .all()
    )
    pending = by_status.get(JobStatus.pending, 0)
    in_progress = by_status.get(JobStatus.in_progress, 0)

    active = pending + in_progress
    if batch_id is None:
        # La tanda que importa es la que está corriendo; si no hay nada activo, la última.
        # Un "Auto" de un solo track no tiene tanda: queda None y se cuenta aparte abajo.
        q = db.query(func.max(DownloadJob.batch_id)).filter(DownloadJob.user_id == user.id)
        if active:
            q = q.filter(DownloadJob.status.in_([JobStatus.pending, JobStatus.in_progress]))
        batch_id = q.scalar()
    batch: dict = {}
    if batch_id is not None:
        batch = dict(
            db.query(DownloadJob.status, func.count())
            .filter(DownloadJob.user_id == user.id, DownloadJob.batch_id == batch_id)
            .group_by(DownloadJob.status)
            .all()
        )

    queued = (
        db.query(func.count(ReviewItem.id))
        .join(ReviewItem.normalized_track)
        .join(NormalizedTrack.source_track)
        .filter(SourceTrack.user_id == user.id, ReviewItem.status == TrackStatus.queued)
        .scalar()
    ) or 0

    agent_online = user.agent_online
    batch_total = sum(batch.values())
    batch_done = sum(batch.get(s, 0) for s in _FINISHED)
    if active and batch_id is None:
        batch_total, batch_done = active, 0
    return {
        "pending": pending,
        "in_progress": in_progress,
        # "Descargando" solo si el agente está trabajando o puede tomar lo pendiente;
        # con el agente cerrado lo pendiente queda "esperando", que es otro mensaje.
        "state": ("downloading" if in_progress or (pending and agent_online)
                  else "waiting" if pending else "idle"),
        "agent_online": agent_online,
        "queued": queued,
        "batch_id": batch_id,
        "batch_total": batch_total,
        "batch_done": batch_done,
        "batch": {s.value: batch.get(s, 0) for s in _FINISHED},
    }
