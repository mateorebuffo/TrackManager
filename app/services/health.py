"""
Analítica del admin: salud del sistema y alumnos con problemas.

Todo sale de tablas que ya existen. Lo único que sale a la red es `run_checks`,
que prueba las cuentas del admin que abre la pantalla contra cada servicio (los
mismos chequeos que usa el agente) y deja el resultado como eventos
`health_check`. No hay tarea programada: corre cuando el admin abre la pantalla
y la última prueba tiene más de 12 horas, o cuando toca "Probar ahora".

Las cuentas de los alumnos no se prueban desde acá: su agente ya las valida al
abrirse, y ese resultado queda como evento `credential_check` (ver
`log_credential_check`). De ahí sale "Sesión vencida" en cada alumno.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
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
    "cosine": "Cosine Club",
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


def _ping_cosine() -> tuple[bool, str]:
    """Las recomendaciones de /descubrir. Prueba la key de la app, no la de un alumno."""
    if not settings.cosine_api_key:
        return False, "Falta configurar COSINE_API_KEY en Railway."
    try:
        r = httpx.get("https://cosine.club/api/v1/search", params={"q": "test", "limit": 1}, timeout=10,
                      headers={"Authorization": f"Bearer {settings.cosine_api_key}",
                               "User-Agent": "TrackManager/1.0"})
    except httpx.HTTPError as e:
        return False, f"Error de conexión: {e}"
    if r.status_code == 401:
        return False, "La API key no es válida: revisá COSINE_API_KEY."
    if r.status_code == 429:
        return False, "Llegó al límite de 120 consultas por minuto."
    if r.status_code != 200:
        return False, f"Respuesta inesperada: {r.status_code}."
    return True, "Responde"


def last_run(db: Session) -> datetime | None:
    return _aware(db.query(func.max(AppEvent.created_at))
                  .filter(AppEvent.event_type == "health_check").scalar())


def is_stale(db: Session) -> bool:
    last = last_run(db)
    return last is None or _now() - last > STALE_AFTER


def _classify(name: str, r: dict | None) -> tuple[str, str]:
    """
    Estado de un servicio a partir de la prueba con la cuenta del admin.
    Una sesión vencida no dice nada del servicio (respondió): hay que reconectar
    la cuenta para poder probarlo.
    """
    if not r or not r["connected"]:
        return "sin_datos", f"sin conectar. Conectá tu cuenta de {name} en el agente para probarla."
    if r["ok"]:
        return "ok", "válida."
    if credential_check.is_service_error(r["msg"]):
        return "caido", r["msg"]
    return "sin_datos", f"vencida. Reconectá {name} en el agente para probarla."


def log_credential_check(db: Session, user_id: int, results: dict) -> None:
    """Guardar la revisión de cuentas de un usuario (la usa "Alumnos con problemas")."""
    problems = [s for s, r in results.items()
                if r["connected"] and not r["ok"] and not credential_check.is_service_error(r["msg"])]
    log_service.log_event(
        db, "credential_check",
        f"Cuentas revisadas: {len(problems)} con la sesión vencida" if problems
        else "Cuentas revisadas: sin problemas",
        user_id=user_id, context={"results": results},
    )


def run_checks(db: Session, admin_id: int) -> None:
    """Probar todos los servicios con las cuentas del admin (unas pocas requests)."""
    # ponytail: una sola cuenta de prueba; si la del admin falla por algo suyo,
    # el servicio queda "sin datos" hasta que la reconecte.
    known, pending = credential_check.plan_checks(db, admin_id)
    results = dict(known)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(svc, pool.submit(run)) for svc, run, _extra in pending]
        # Servicios sin cuenta de usuario: se prueban con la de la app.
        pings = {svc: pool.submit(fn) for svc, fn in
                 (("discogs", _ping_discogs), ("cosine", _ping_cosine))}
        for svc, fut in futures:
            try:
                ok, msg = fut.result()
            except Exception as e:  # un servicio raro no tiene que tirar la pantalla
                logger.exception("Health check %s failed", svc)
                ok, msg = False, f"Error de conexión: {e}"
            results[svc] = {"ok": ok, "connected": True, "msg": msg}
        pinged = {svc: fut.result() for svc, fut in pings.items()}

    log_credential_check(db, admin_id, results)
    for svc, name in SERVICES.items():
        if svc in pinged:
            ok, msg = pinged[svc]
            status, detail = ("ok", "Responde.") if ok else ("caido", msg)
        else:
            status, detail = _classify(name, results.get(svc))
        level = {"caido": "error", "con_fallas": "warning"}.get(status, "info")
        log_service.log_event(
            db, "health_check", f"{name}: {detail}", level=level,
            source=svc, context={"service": svc, "status": status, "detail": detail},
        )
    db.commit()


# ── Lo que muestra la pantalla ──────────────────────────────────────────────

def _usage(db: Session) -> dict[str, tuple[int, int]]:
    """Uso real de las últimas 24 h, por servicio: (salieron bien, fallaron)."""
    day_ago = _now() - timedelta(hours=24)
    jobs = dict(db.query(DownloadJob.status, func.count(DownloadJob.id))
                .filter(DownloadJob.status.in_((JobStatus.completed, JobStatus.failed)),
                        DownloadJob.updated_at >= day_ago)
                .group_by(DownloadJob.status).all())
    syncs: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for src, typ, n in (db.query(AppEvent.source, AppEvent.event_type, func.count(AppEvent.id))
                        .filter(AppEvent.event_type.in_(("sync_completed", "sync_failed")),
                                AppEvent.created_at >= day_ago)
                        .group_by(AppEvent.source, AppEvent.event_type).all()):
        syncs[src][typ == "sync_failed"] += n
    # ponytail: los trabajos no guardan la tienda; todas las descargas cuentan para Muzpa.
    out = {"muzpa": (jobs.get(JobStatus.completed, 0), jobs.get(JobStatus.failed, 0))}
    out.update({src: (ok, bad) for src, (ok, bad) in syncs.items()})
    return out


def _last_success(db: Session, svc: str) -> datetime | None:
    if svc == "muzpa":
        return db.query(func.max(DownloadJob.updated_at)).filter(
            DownloadJob.status == JobStatus.completed).scalar()
    return db.query(func.max(AppEvent.created_at)).filter(
        AppEvent.event_type == "sync_completed", AppEvent.source == svc).scalar()


def service_status(db: Session) -> list[dict]:
    """
    Una tarjeta por servicio. El veredicto sale del uso real de las últimas 24 h
    (descargas e importaciones de los alumnos); sin uso, queda el de la prueba
    de sesión del admin, que sólo dice que la página responde.
    """
    since_month = _now() - timedelta(days=30)
    checks = (db.query(AppEvent)
              .filter(AppEvent.event_type == "health_check", AppEvent.created_at >= since_month)
              .order_by(AppEvent.created_at.desc()).all())
    usage = _usage(db)

    cards = []
    for svc, name in SERVICES.items():
        mine = [e for e in checks if (e.context_json or {}).get("service") == svc]
        last = mine[0] if mine else None
        worked = next((e for e in mine if e.context_json.get("status") in ("ok", "con_fallas")), None)
        status = last.context_json["status"] if last else "sin_datos"
        detail = last.context_json.get("detail") if last else "Todavía no se probó."
        ok, bad = usage.get(svc, (0, 0))
        what = ("descargas", "completadas") if svc == "muzpa" else ("importaciones", "completadas")
        if ok or bad:
            if bad > ok:
                status = "caido"
            elif bad:
                status = "con_fallas"
            elif status != "caido":
                status = "ok"
            use = f"Últimas 24 h: {ok} {what[1]}, {bad} {'falló' if bad == 1 else 'fallaron'}."
        elif svc in ("muzpa", "soundcloud", "youtube"):
            # Sin uso no hay forma de saber si descargar anda: se dice, no se inventa.
            use = (f"Sin {what[0]} en las últimas 24 h (la última salió bien "
                   f"{ago(_last_success(db, svc))}). Sólo se sabe que la página responde.")
        else:
            use = ""
        cards.append({
            "service": svc, "name": name, "status": status, "detail": detail, "use": use,
            "own_account": svc not in ("discogs", "cosine"),
            "tested": ago(last.created_at) if last else "nunca",
            "worked": ago(worked.created_at) if worked else "sin registro",
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
    """
    Una fila por alumno que tiene al menos un problema. Cada problema guarda su
    grupo (para la vista "problema → alumnos"), si es urgente, el texto completo
    (para la búsqueda por alumno) y el dato propio del alumno dentro del grupo.
    """
    users = {u.id: u for u in db.query(User).filter(User.is_active.is_(True)).all()}
    problems: dict[int, list[dict]] = defaultdict(list)
    now = _now()

    def add(uid: int, group: str, msg: str, detail: str = "", urgent: bool = False) -> None:
        problems[uid].append({"group": group, "msg": msg, "detail": detail, "urgent": urgent})

    settings = {s.user_id: s for s in db.query(UserSettings).all()}
    for uid in users:
        us = settings.get(uid)
        if not (us and (us.muzpa_sess or us.deezer_arl)):
            add(uid, "No conectó Muzpa ni Deezer: no puede descargar",
                "No puede descargar: no conectó Muzpa ni Deezer")
        if not (us and (us.soundcloud_oauth_token or us.youtube_token_json)):
            add(uid, "No conectó SoundCloud ni YouTube: no puede importar",
                "No puede importar: no conectó SoundCloud ni YouTube")
        if not users[uid].email:
            add(uid, "Sin email: no puede recuperar la contraseña",
                "No puede recuperar la contraseña: no tiene email cargado")

    # La última revisión de cuentas de cada alumno.
    latest = (db.query(AppEvent.user_id, func.max(AppEvent.id).label("last_id"))
              .filter(AppEvent.event_type == "credential_check", AppEvent.user_id.isnot(None))
              .group_by(AppEvent.user_id).subquery())
    for ev in db.query(AppEvent).join(latest, AppEvent.id == latest.c.last_id).all():
        for svc, r in ((ev.context_json or {}).get("results") or {}).items():
            if r.get("connected") and not r.get("ok") and not credential_check.is_service_error(r.get("msg", "")):
                msg = f"Sesión vencida en {SERVICES.get(svc, svc)}"
                add(ev.user_id, msg, msg, urgent=True)

    week = now - timedelta(days=7)
    for uid, src, n in (db.query(AppEvent.user_id, AppEvent.source, func.count(AppEvent.id))
                        .filter(AppEvent.event_type == "sync_failed", AppEvent.created_at >= week)
                        .group_by(AppEvent.user_id, AppEvent.source).all()):
        name = SERVICES.get(src, src or "una fuente")
        times = f"{n} {'vez' if n == 1 else 'veces'} en 7 días"
        add(uid, f"Falló la importación de {name}", f"Falló la importación de {name} ({times})", times)

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
            pct = f"{round(100 * misses[uid] / total)} %"
            add(uid, f"No se encuentra lo que pide (más del {round(100 * NOT_FOUND_RATIO)} %, 30 días)",
                f"No se encontró el {pct} de sus descargas (30 días)", f"{pct} sin encontrar")

    for a in agents(db)["forgotten"]:
        add(a["user_id"], "Tracks esperando y el agente cerrado",
            f"{a['pending']} tracks esperando y el agente no se abre ({a['seen']})",
            f"{a['pending']} tracks, se conectó {a['seen']}", urgent=True)

    for uid, n in (db.query(UserReport.user_id, func.count(UserReport.id))
                   .filter(UserReport.status == ReportStatus.open)
                   .group_by(UserReport.user_id).all()):
        msg = f"{n} reporte{'s' if n > 1 else ''} sin resolver"
        add(uid, "Reportes sin resolver", msg, msg, urgent=True)

    for uid, n in Counter(j["user_id"] for j in stuck_jobs(db)).items():
        msg = f"{n} descarga{'s' if n > 1 else ''} trabada{'s' if n > 1 else ''}"
        add(uid, "Descargas trabadas", msg, msg, urgent=True)

    return sorted(
        ({"user": users[uid].username, "problems": [i["msg"] for i in p], "items": p}
         for uid, p in problems.items() if uid in users),
        key=lambda r: (-len(r["problems"]), r["user"]),
    )


def problem_groups(rows: list[dict]) -> list[dict]:
    """
    La misma información dada vuelta: un renglón por problema con sus alumnos.
    Con muchos alumnos los conteos escalan y los nombres no: primero lo urgente
    (lo que bloquea al alumno o espera al admin), después lo informativo.
    """
    groups: dict[str, dict] = {}
    for row in rows:
        for i in row["items"]:
            g = groups.setdefault(i["group"], {"label": i["group"], "urgent": i["urgent"], "users": []})
            g["users"].append({"user": row["user"], "detail": i["detail"]})
    for g in groups.values():
        g["users"].sort(key=lambda u: u["user"].lower())
    return sorted(groups.values(), key=lambda g: (not g["urgent"], -len(g["users"]), g["label"]))


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
    problems = user_problems(db)
    return {
        "services": service_status(db),
        "last_run": ago(last_run(db)),
        "agents": agents(db),
        "stuck": stuck_jobs(db),
        "errors": recent_errors(db),
        "problems": problems,
        "groups": problem_groups(problems),
        "students": sorted((u for (u,) in db.query(User.username).filter(User.is_active.is_(True))),
                           key=str.lower),
        "reports": report_summary(db),
    }
