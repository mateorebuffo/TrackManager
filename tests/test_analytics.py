"""Panel de analítica del admin: salud del sistema y alumnos con problemas."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api import analytics
from app.models.app_event import AppEvent
from app.models.download_job import DownloadJob, JobStatus
from app.models.user import User
from app.models.user_report import ReportStatus, UserReport
from app.services import credential_check, health
from app.services.auth import make_session_token

NOW = datetime.now(timezone.utc)


@pytest.fixture
def sin_red(monkeypatch):
    """Ningún test sale a internet: todos los servicios responden OK."""
    monkeypatch.setattr(credential_check, "check", lambda svc, v: (True, "Cuenta conectada"))
    monkeypatch.setattr(health, "_ping_discogs", lambda: (True, "Responde"))
    monkeypatch.setattr(health, "_ping_cosine", lambda: (True, "Responde"))


def _checks(db):
    return db.query(AppEvent).filter(AppEvent.event_type == "health_check").count()


def _tester(db):
    return db.query(User).filter_by(username="tester").one()


def test_el_admin_ve_el_panel_y_se_prueban_los_servicios(client, db_session, sin_red):
    r = client.get("/analitica")
    assert r.status_code == 200
    assert "Salud del sistema" in r.text and "Alumnos con problemas" in r.text
    assert "SoundCloud" in r.text and "Funciona" in r.text
    assert _checks(db_session) == len(health.SERVICES)


def test_no_repite_la_prueba_si_es_reciente(client, db_session, sin_red):
    client.get("/analitica")
    client.get("/analitica")
    assert _checks(db_session) == len(health.SERVICES)


def test_un_alumno_no_entra(client, db_session):
    dj = User(username="dj", hashed_password="x", is_admin=False, api_token="tok-dj")
    db_session.add(dj)
    db_session.commit()
    client.cookies.set("mc_session", make_session_token(dj.id))
    assert client.get("/analitica").status_code == 403
    assert client.post("/analitica/probar").status_code == 403


def test_probar_ahora_respeta_el_limite(client, monkeypatch):
    monkeypatch.setattr(analytics, "_probe_limiter", SimpleNamespace(acquire=lambda uid: False))
    monkeypatch.setattr(health, "run_checks", lambda db, uid: pytest.fail("no tenía que probar"))
    r = client.post("/analitica/probar", follow_redirects=False)
    assert r.status_code == 303 and "espera=1" in r.headers["location"]


@pytest.mark.parametrize("result,esperado", [
    (None, "sin_datos"),
    ({"connected": False, "ok": False, "msg": "Sin conectar."}, "sin_datos"),
    ({"connected": True, "ok": False, "msg": "Error de conexión: timeout"}, "caido"),
    ({"connected": True, "ok": True, "msg": "ok"}, "ok"),
    # Una sesión vencida no dice nada del servicio: hay que reconectar para probarlo.
    ({"connected": True, "ok": False, "msg": "Sesión expirada o inválida. Reconectá Muzpa."}, "sin_datos"),
])
def test_distingue_servicio_caido_de_sesion_vencida(result, esperado):
    assert health._classify("Muzpa", result)[0] == esperado


def test_la_prueba_usa_solo_las_cuentas_del_admin(db_session, client, monkeypatch, sin_red):
    probadas = []
    monkeypatch.setattr(credential_check, "plan_checks",
                        lambda db, uid: (probadas.append(uid) or {}, []))
    dj = User(username="dj", hashed_password="x", is_admin=False)
    db_session.add(dj)
    db_session.commit()
    health.run_checks(db_session, _tester(db_session).id)
    assert probadas == [_tester(db_session).id]


def test_la_sesion_vencida_llega_desde_el_agente(db_session, client, monkeypatch):
    """El agente valida sus cuentas al abrirse; la analítica no las vuelve a probar."""
    from app.models.user_settings import UserSettings
    monkeypatch.setattr(credential_check, "check",
                        lambda svc, v: (False, "Token expirado o inválido. Reconectá SoundCloud."))
    db_session.query(UserSettings).filter_by(user_id=_tester(db_session).id).one().soundcloud_oauth_token = "viejo"
    db_session.commit()
    r = client.get("/api/me/credentials", headers={"Authorization": "Bearer tok-test"})
    assert r.status_code == 200 and r.json()["soundcloud"]["ok"] is False

    probs = {p["user"]: p["problems"] for p in health.user_problems(db_session)}
    assert "Sesión vencida en SoundCloud" in probs["tester"]
    # Con un servicio filtrado (la ventana de login pollea así) no se guarda nada.
    client.get("/api/me/credentials?service=muzpa", headers={"Authorization": "Bearer tok-test"})
    assert db_session.query(AppEvent).filter_by(event_type="credential_check").count() == 1


def test_detecta_los_problemas_de_un_alumno(db_session, client):
    dj = User(username="dj", hashed_password="x", is_admin=False)
    db_session.add(dj)
    db_session.commit()

    db_session.add(AppEvent(event_type="sync_failed", message="x", level="error",
                            user_id=dj.id, source="soundcloud"))
    db_session.add(UserReport(user_id=dj.id, category="other", description="x",
                              status=ReportStatus.open))
    # 12 descargas terminadas, 10 sin encontrar.
    for i in range(12):
        st = JobStatus.completed if i < 2 else JobStatus.not_found
        db_session.add(DownloadJob(user_id=dj.id, review_id=1000 + i, query="q", status=st))
    # Una trabada y una esperando a un agente que nunca se abrió.
    db_session.add(DownloadJob(user_id=dj.id, review_id=2000, query="trabada",
                               status=JobStatus.in_progress, updated_at=NOW - timedelta(hours=2)))
    db_session.add(DownloadJob(user_id=dj.id, review_id=2001, query="espera",
                               status=JobStatus.pending))
    db_session.commit()

    probs = {p["user"]: " | ".join(p["problems"]) for p in health.user_problems(db_session)}
    texto = probs["dj"]
    assert "No puede descargar" in texto
    assert "No puede importar" in texto
    assert "no tiene email cargado" in texto
    assert "Falló la importación de SoundCloud" in texto
    assert "No se encontró el 83 %" in texto
    assert "el agente no se abre" in texto
    assert "1 reporte sin resolver" in texto
    assert "1 descarga trabada" in texto
    assert [j["query"] for j in health.stuck_jobs(db_session)] == ["trabada"]

    # Dado vuelta: un renglón por problema, lo urgente primero.
    groups = health.problem_groups(health.user_problems(db_session))
    by_label = {g["label"]: g for g in groups}
    trabadas = by_label["Descargas trabadas"]
    assert trabadas["urgent"] and trabadas["users"] == [{"user": "dj", "detail": "1 descarga trabada"}]
    assert by_label["Falló la importación de SoundCloud"]["users"][0]["detail"] == "1 vez en 7 días"
    urgent = [g["urgent"] for g in groups]
    assert urgent == sorted(urgent, reverse=True)
    # "No conectó Muzpa ni Deezer" junta a los dos alumnos en un solo renglón.
    assert {u["user"] for u in by_label["No conectó Muzpa ni Deezer: no puede descargar"]["users"]} >= {"dj", "tester"}


def test_buscar_un_alumno(client, db_session, sin_red):
    r = client.get("/analitica?alumno=TESTER")
    assert r.status_code == 200 and "No puede descargar" in r.text
    assert "ningún alumno activo llamado" in client.get("/analitica?alumno=nadie").text


def test_el_agente_avisa_que_esta_vivo(client, db_session):
    assert _tester(db_session).agent_seen_at is None
    r = client.get("/api/download-jobs", headers={"Authorization": "Bearer tok-test"})
    assert r.status_code == 200
    db_session.expire_all()
    assert _tester(db_session).agent_seen_at is not None
    assert health.agents(db_session)["online"] == 1


def test_pendiente_trabada_solo_si_el_agente_anda_y_no_la_toma(db_session, client):
    dj = User(username="dj2", hashed_password="x", agent_seen_at=NOW - timedelta(minutes=1))
    db_session.add(dj)
    db_session.commit()
    db_session.add(DownloadJob(user_id=dj.id, review_id=3000, query="vieja", status=JobStatus.pending,
                               created_at=NOW - timedelta(days=3)))
    db_session.add(DownloadJob(user_id=dj.id, review_id=3001, query="nueva", status=JobStatus.pending,
                               created_at=NOW - timedelta(hours=1)))
    db_session.commit()
    assert [j["query"] for j in health.stuck_jobs(db_session)] == ["vieja"]



def test_cosine_sin_key_aparece_caido(db_session, client, monkeypatch):
    monkeypatch.setattr(credential_check, "check", lambda svc, v: (True, "Cuenta conectada"))
    monkeypatch.setattr(health, "_ping_discogs", lambda: (True, "Responde"))
    monkeypatch.setattr(health.settings, "cosine_api_key", "")  # sin key no sale a la red
    health.run_checks(db_session, _tester(db_session).id)
    cosine = next(s for s in health.service_status(db_session) if s["service"] == "cosine")
    assert cosine["status"] == "caido" and "COSINE_API_KEY" in cosine["detail"]


def _card(db, svc):
    return next(s for s in health.service_status(db) if s["service"] == svc)


def test_el_veredicto_sale_de_las_descargas_reales(db_session, client):
    uid = _tester(db_session).id
    # La prueba de sesión dio bien, pero en el uso real fallan casi todas.
    db_session.add(AppEvent(event_type="health_check", message="x", source="muzpa",
                            context_json={"service": "muzpa", "status": "ok", "detail": "válida."}))
    for i, st in enumerate([JobStatus.completed] + [JobStatus.failed] * 3):
        db_session.add(DownloadJob(user_id=uid, review_id=4000 + i, query="q", status=st))
    db_session.commit()
    card = _card(db_session, "muzpa")
    assert card["status"] == "caido" and "1 completadas, 3 fallaron" in card["use"]


def test_sin_uso_lo_dice_en_vez_de_inventar(db_session, client):
    db_session.add(DownloadJob(user_id=_tester(db_session).id, review_id=5000, query="q",
                               status=JobStatus.completed, updated_at=NOW - timedelta(days=2)))
    db_session.commit()
    use = _card(db_session, "muzpa")["use"]
    assert "Sin descargas en las últimas 24 h" in use and "hace 2 días" in use
    assert _card(db_session, "discogs")["use"] == ""
