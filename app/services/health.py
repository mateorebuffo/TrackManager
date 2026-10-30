"""
Analítica del admin: salud del sistema y alumnos con problemas.

Todo sale de tablas que ya existen. Lo único que sale a la red es `run_checks`,
que prueba las cuentas de cada alumno contra su servicio (los mismos chequeos
que usa el agente) y deja el resultado como eventos `health_check` y
`credential_check`. No hay tarea programada: corre cuando el admin abre la
pantalla y la última prueba tiene más de 12 horas, o cuando toca "Probar ahora".
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.app_event import AppEvent, EventLevel
from app.models.download_job import DownloadJob, JobStatus
from app.models.user import User
from app.models.user_report import REPORT_CATEGORIES, ReportStatus, UserReport
from app.models.user_settings import UserSettings
from app.services import credential_check, log_service

logger = logging.getLogger(__name__)

SERVICES = {
    "muzpa": "Muzpa",
    "deezer": "Deezer",
    "soundcloud": "SoundCloud",
    "youtube": "YouTube",
    "discogs": "Discogs",
}
STALE_AFTER = timedelta(hours=12)       # prueba automática al abrir la pantalla
AGENT_ONLINE = timedelta(minutes=2)     # el agente pregunta cada pocos segundos
AGENT_FORGOTTEN = timedelta(days=3)     # trabajos esperando y agente sin abrir
STUCK_IN_PROGRESS = timedelta(minutes=30)
STUCK_PENDING = timedelta(hours=24)
# ponytail: umbrales fijos; pasarlos a config si la escuela pide ajustarlos
NOT_FOUND_RATIO = 0.7
NOT_FOUND_MIN_JOBS = 10


ERROR_LABELS = {
    "sync_failed": "Importación fallida",
    "download_failed": "Descarga fallida (Muzpa no respondió)",
    "health_check": "Servicio caído en la prueba",
}


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite y Postgres devuelven las fechas sin zona; se guardan en UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def ago(dt: datetime | None) -> str:
    """'hace 5 min' — evita mostrar horas UTC que confunden."""
    if dt is None:
        return "nunca"
    s = int((_now() - _aware(dt)).total_seconds())
    if s < 60:
        return "recién"
    if s < 3600:
        return f"hace {s // 60} min"
    if s < 86400:
        return f"hace {s // 3600} h"
    d = s // 86400
    return f"hace {d} día{'s' if d > 1 else ''}"


# ── Pruebas contra los servicios ────────────────────────────────────────────

def _ping_discogs() -> tuple[bool, str]:
    try:
        r = httpx.get("https://api.discogs.com/", timeout=10,
                      headers={"User-Agent": "TrackManager/1.0"})
    except httpx.HTTPError as e:
        return False, f"Error de conexión: {e}"
    if r.status_code != 200:
        return False, f"Respuesta inesperada: {r.status_code}."
    return True, "Responde"


def last_run(db: Session) -> datetime | None:
    return _aware(db.query(func.max(AppEvent.created_at))
                  .filter(AppEvent.event_type == "health_check").scalar())


def is_stale(db: Session) -> bool:
    last = last_run(db)
    return last is None or _now() - last > STALE_AFTER


def _classify(results: list[dict]) -> tuple[str, str]:
    """
    Estado de un servicio a partir de las pruebas con las cuentas de los alumnos.
    Una sesión vencida es problema del alumno, no del servicio: el servicio respondió.
    """
    tested = [r for r in results if r["connected"]]
    if not tested:
        return "sin_datos", "Ningún alumno tiene esta cuenta conectada para probarla."
    ok = sum(r["ok"] for r in tested)
    down = [r for r in tested if not r["ok"] and credential_check.is_service_error(r["msg"])]
    expired = len(tested) - ok - len(down)
    if down and not ok:
        return "caido", down[0]["msg"]
    detail = f"{ok} de {len(tested)} cuentas funcionan"
    if expired:
        detail += f", {expired} con la sesión vencida"
    if down:
        return "con_fallas", f"{detail}. Falló para {len(down)}: {down[0]['msg']}"
    return "ok", detail + "."


def run_checks(db: Session) -> None:
    """Probar todos los servicios con las cuentas de todos los alumnos activos."""
    users = db.query(User).filter(User.is_active.is_(True)).all()

    # La base se lee en este hilo; sólo las requests van en paralelo.
    by_user: dict[int, dict] = {}
    tasks = []
    for u in users:
        known, pending = credential_check.plan_checks(db, u.id)
        by_user[u.id] = known
        tasks += [(u.id, svc, run) for svc, run, _extra in pending]

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(uid, svc, pool.submit(run)) for uid, svc, run in tasks]
        discogs = pool.submit(_ping_discogs)
        for uid, svc, fut in futures:
            try:
                ok, msg = fut.result()
            except Exception as e:  # un servicio raro no tiene que tirar la pantalla
                logger.exception("Health check %s failed for user %s", svc, uid)
                ok, msg = False, f"Error de conexión: {e}"
            by_user[uid][svc] = {"ok": ok, "connected": True, "msg": msg}
        discogs_ok, discogs_msg = discogs.result()

    for uid, results in by_user.items():
        problems = [s for s, r in results.items()
                    if r["connected"] and not r["ok"] and not credential_check.is_service_error(r["msg"])]
        log_service.log_event(
            db, "credential_check",
            f"Cuentas revisadas: {len(problems)} con la sesión vencida" if problems
            else "Cuentas revisadas: sin problemas",
            user_id=uid, context={"results": results},
        )

    for svc, name in SERVICES.items():
        if svc == "discogs":
            status, detail = ("ok", "Responde.") if discogs_ok else ("caido", discogs_msg)
        else:
            status, detail = _classify([r[svc] for r in by_user.values() if svc in r])
        level = {"caido": "error", "con_fallas": "warning"}.get(status, "info")
        log_service.log_event(
            db, "health_check", f"{name}: {detail}", level=level,
            source=svc, context={"service": svc, "status": status, "detail": detail},
        )
    db.commit()


# ── Lo que muestra la pantalla ──────────────────────────────────────────────

def service_status(db: Session) -> list[dict]:
    """Una tarjeta por servicio: última prueba + señales de las últimas 24 h."""
    since_month = _now() - timedelta(days=30)
    checks = (db.query(AppEvent)
              .filter(AppEvent.event_type == "health_check", AppEvent.created_at >= since_month)
              .order_by(AppEvent.created_at.desc()).all())

    day_ago = _now() - timedelta(hours=24)
    muzpa_failed = (db.query(func.count(DownloadJob.id))
                    .filter(DownloadJob.status == JobStatus.failed,
                            DownloadJob.updated_at >= day_ago).scalar())
    sync_failed = dict(db.query(AppEvent.source, func.count(AppEvent.id))
                       .filter(AppEvent.event_type == "sync_failed",
                               AppEvent.created_at >= day_ago)
                       .group_by(AppEvent.source).all())

    cards = []
    for svc, name in SERVICES.items():
        mine = [e for e in checks if (e.context_json or {}).get("service") == svc]
        last = mine[0] if mine else None
        worked = next((e for e in mine if e.context_json.get("status") in ("ok", "con_fallas")), None)
        status = last.context_json["status"] if last else "sin_datos"
        signals = []
        if svc == "muzpa" and muzpa_failed:
            signals.append(f"{_n(muzpa_failed, 'descarga falló', 'descargas fallaron')} "
                           "porque Muzpa no respondió (24 h)")
        if sync_failed.get(svc):
            signals.append(f"{_n(sync_failed[svc], 'importación fallida', 'importaciones fallidas')} (24 h)")
        if signals and status == "ok":
            status = "con_fallas"  # respondía al probar, pero en el uso real falla
        cards.append({
            "service": svc, "name": name, "status": status,
            "detail": last.context_json.get("detail") if last else "Todavía no se probó.",
            "tested": ago(last.created_at) if last else "nunca",
            "worked": ago(worked.created_at) if worked else "sin registro",
            "signals": signals,
        })
    return cards


def _pending_jobs_by_user(db: Session) -> dict[int, int]:
    return dict(db.query(DownloadJob.user_id, func.count(DownloadJob.id))
                .filter(DownloadJob.status == JobStatus.pending)
                .group_by(DownloadJob.user_id).all())


def agents(db: Session) -> dict:
    users = db.query(User).filter(User.is_active.is_(True)).all()
    now = _now()
    waiting = _pending_jobs_by_user(db)
    online = [u for u in users if u.agent_seen_at and now - _aware(u.agent_seen_at) <= AGENT_ONLINE]
    forgotten = [
        {"user": u.username, "user_id": u.id, "pending": waiting[u.id],
         "seen": ago(u.agent_seen_at)}
        for u in users
        if waiting.get(u.id) and (u.agent_seen_at is None
                                  or now - _aware(u.agent_seen_at) > AGENT_FORGOTTEN)
    ]
    return {"online": len(online), "online_users": [u.username for u in online],
            "total": len(users), "forgotten": forgotten}


def stuck_jobs(db: Session) -> list[dict]:
    now = _now()
    rows = (db.query(DownloadJob, User.username, User.agent_seen_at)
            .join(User, User.id == DownloadJob.user_id)
            .filter(
                ((DownloadJob.status == JobStatus.in_progress)
                 & (DownloadJob.updated_at < now - STUCK_IN_PROGRESS))
                | ((DownloadJob.status == JobStatus.pending)
                   & (DownloadJob.created_at < now - STUCK_PENDING)))
            .order_by(DownloadJob.created_at).limit(200).all())

    def stuck(j: DownloadJob, seen: datetime | None) -> bool:
        if j.status == JobStatus.in_progress:
            return True
        # Pendiente hace más de un día aunque el agente se conectó después: el
        # agente anda pero no la toma. Con el agente cerrado no es una descarga
        # trabada, es "el agente no se abre" (ver agents()). Se compara acá y no
        # en SQL porque sumar fechas no se escribe igual en SQLite y en Postgres.
        return seen is not None and _aware(seen) > _aware(j.created_at) + STUCK_PENDING

    return [{
        "user": username, "query": j.query,
        "status": "En curso" if j.status == JobStatus.in_progress else "El agente no la toma",
        "since": ago(j.updated_at if j.status == JobStatus.in_progress else j.created_at),
        "user_id": j.user_id,
    } for j, username, seen in rows if stuck(j, seen)][:50]


def recent_errors(db: Session) -> list[dict]:
    rows = (db.query(AppEvent.event_type, func.count(AppEvent.id), func.max(AppEvent.created_at))
            .filter(AppEvent.level == EventLevel.error,
                    AppEvent.created_at >= _now() - timedelta(hours=24))
            .group_by(AppEvent.event_type)
            .order_by(func.count(AppEvent.id).desc()).all())
    return [{"type": t, "label": ERROR_LABELS.get(t, t), "count": n, "last": ago(last)}
            for t, n, last in rows]


def user_problems(db: Session) -> list[dict]:
    """Una fila por alumno que tiene al menos un problema, con la lista de cuáles."""
    users = {u.id: u for u in db.query(User).filter(User.is_active.is_(True)).all()}
    problems: dict[int, list[str]] = defaultdict(list)
    now = _now()

    settings = {s.user_id: s for s in db.query(UserSettings).all()}
    for uid in users:
        us = settings.get(uid)
        if not (us and (us.muzpa_sess or us.deezer_arl)):
            problems[uid].append("No puede descargar: no conectó Muzpa ni Deezer")
        if not (us and (us.soundcloud_oauth_token or us.youtube_token_json)):
            problems[uid].append("No puede importar: no conectó SoundCloud ni YouTube")

    # La última revisión de cuentas de cada alumno.
    latest = (db.query(AppEvent.user_id, func.max(AppEvent.id).label("last_id"))
              .filter(AppEvent.event_type == "credential_check", AppEvent.user_id.isnot(None))
              .group_by(AppEvent.user_id).subquery())
    for ev in db.query(AppEvent).join(latest, AppEvent.id == latest.c.last_id).all():
        for svc, r in ((ev.context_json or {}).get("results") or {}).items():
            if r.get("connected") and not r.get("ok") and not credential_check.is_service_error(r.get("msg", "")):
                problems[ev.user_id].append(f"Sesión vencida en {SERVICES.get(svc, svc)}")

    week = now - timedelta(days=7)
    for uid, src, n in (db.query(AppEvent.user_id, AppEvent.source, func.count(AppEvent.id))
                        .filter(AppEvent.event_type == "sync_failed", AppEvent.created_at >= week)
                        .group_by(AppEvent.user_id, AppEvent.source).all()):
        name = SERVICES.get(src, src or "una fuente")
        problems[uid].append(f"Falló la importación de {name} ({n} {'vez' if n == 1 else 'veces'} en 7 días)")

    month = now - timedelta(days=30)
    finished = (JobStatus.completed, JobStatus.not_found, JobStatus.failed,
                JobStatus.vinyl_only, JobStatus.bandcamp_only)
    totals: Counter = Counter()
    misses: Counter = Counter()
    for uid, status, n in (db.query(DownloadJob.user_id, DownloadJob.status, func.count(DownloadJob.id))
                           .filter(DownloadJob.status.in_(finished), DownloadJob.updated_at >= month)
                           .group_by(DownloadJob.user_id, DownloadJob.status).all()):
        totals[uid] += n
        if status in (JobStatus.not_found, JobStatus.failed):
            misses[uid] += n
    for uid, total in totals.items():
        if total >= NOT_FOUND_MIN_JOBS and misses[uid] / total > NOT_FOUND_RATIO:
            problems[uid].append(
                f"No se encontró el {round(100 * misses[uid] / total)} % de sus descargas (30 días)")

    for a in agents(db)["forgotten"]:
        problems[a["user_id"]].append(f"{a['pending']} tracks esperando y el agente no se abre ({a['seen']})")

    for uid, n in (db.query(UserReport.user_id, func.count(UserReport.id))
                   .filter(UserReport.status == ReportStatus.open)
                   .group_by(UserReport.user_id).all()):
        problems[uid].append(f"{n} reporte{'s' if n > 1 else ''} sin resolver")

    for uid, n in Counter(j["user_id"] for j in stuck_jobs(db)).items():
        problems[uid].append(f"{n} descarga{'s' if n > 1 else ''} trabada{'s' if n > 1 else ''}")

    return sorted(
        ({"user": users[uid].username, "problems": p} for uid, p in problems.items() if uid in users),
        key=lambda r: (-len(r["problems"]), r["user"]),
    )


def report_summary(db: Session) -> dict:
    counts: dict[str, Counter] = defaultdict(Counter)
    for cat, status, n in (db.query(UserReport.category, UserReport.status, func.count(UserReport.id))
                           .group_by(UserReport.category, UserReport.status).all()):
        counts[cat][status] += n

    resolved = db.query(UserReport).filter(UserReport.status == ReportStatus.resolved).all()
    days = [(_aware(r.updated_at) - _aware(r.created_at)).total_seconds() / 86400 for r in resolved]

    return {
        "rows": [{
            "label": label,
            "open": counts[cat][ReportStatus.open],
            "reviewed": counts[cat][ReportStatus.reviewed],
            "resolved": counts[cat][ReportStatus.resolved],
        } for cat, label in REPORT_CATEGORIES if counts[cat]],
        "open_total": sum(c[ReportStatus.open] for c in counts.values()),
        "avg_days": round(sum(days) / len(days), 1) if days else None,
    }


def dashboard(db: Session) -> dict:
    return {
        "services": service_status(db),
        "last_run": ago(last_run(db)),
        "agents": agents(db),
        "stuck": stuck_jobs(db),
        "errors": recent_errors(db),
        "problems": user_problems(db),
        "reports": report_summary(db),
    }
