"""
Tests de app/services/credential_check.py

El caso que motiva este archivo: antes, verify_muzpa mandaba q=/limit= (params que
la API de Muzpa ignora) y solo miraba el status_code, así que una sesión vencida
que respondiera 200 pasaba como "Credencial válida" y después el agente no bajaba
nada. Los tests de sesión muerta son la red contra esa regresión.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("USE_MOCK_COLLECTOR", "true")

from app.services import credential_check as cc


# ── Muzpa ────────────────────────────────────────────────────────────────────

def test_muzpa_empty_sess():
    ok, msg = cc.check_muzpa("   ")
    assert ok is False
    assert "No hay SESS" in msg


_MUZPA_OK = {"id": 175901, "login": "user_175901", "email": "dj@muzpa.com", "disabled": False}


def test_muzpa_valid_session_reports_the_email():
    with patch.object(httpx, "get", return_value=httpx.Response(200, json=_MUZPA_OK)):
        ok, msg = cc.check_muzpa("abc123")
    assert ok is True
    assert msg == f"{cc.CONNECTED} (dj@muzpa.com)"


def test_muzpa_falls_back_to_login_when_there_is_no_email():
    account = {**_MUZPA_OK, "email": None}
    with patch.object(httpx, "get", return_value=httpx.Response(200, json=account)):
        ok, msg = cc.check_muzpa("abc123")
    assert ok is True
    assert msg == f"{cc.CONNECTED} (user_175901)"


def test_muzpa_disabled_account_is_not_usable():
    account = {**_MUZPA_OK, "disabled": True}
    with patch.object(httpx, "get", return_value=httpx.Response(200, json=account)):
        ok, msg = cc.check_muzpa("abc123")
    assert ok is False
    assert "deshabilitada" in msg


def test_every_service_reports_the_same_connected_text():
    """El panel del agente las lista juntas: el texto de OK tiene que ser uno solo."""
    muzpa = httpx.Response(200, json=_MUZPA_OK)
    deezer = httpx.Response(200, json={"results": {"USER": {"USER_ID": 7, "EMAIL": "a@b.com"}}})
    sc = httpx.Response(200, json={"id": 1, "username": "mateo"})
    yt = httpx.Response(200, json={"items": [{"snippet": {"title": "Mi Canal"}}]})
    with patch.object(httpx, "get", side_effect=[muzpa, deezer, sc, yt]):
        msgs = [cc.check_muzpa("x")[1], cc.check_deezer("x")[1],
                cc.check_soundcloud("x")[1], cc.check_youtube("x")[1]]
    for msg in msgs:
        assert msg.startswith(cc.CONNECTED), msg
    # y los cuatro identifican la cuenta, que es el punto de mostrarlos juntos
    for msg in msgs:
        assert "(" in msg, msg


def test_youtube_reports_the_channel_name():
    resp = httpx.Response(200, json={"items": [{"snippet": {"title": "Mateo DJ"}}]})
    with patch.object(httpx, "get", return_value=resp) as mock_get:
        ok, msg = cc.check_youtube("ya29.token")
    assert ok is True
    assert msg == f"{cc.CONNECTED} (Mateo DJ)"
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer ya29.token"


def test_youtube_account_without_a_channel_is_still_connected():
    with patch.object(httpx, "get", return_value=httpx.Response(200, json={"items": []})):
        ok, msg = cc.check_youtube("ya29.token")
    assert ok is True
    assert msg == cc.CONNECTED


@pytest.mark.parametrize("status", [401, 403])
def test_youtube_rejects_auth_errors(status):
    with patch.object(httpx, "get", return_value=httpx.Response(status)):
        ok, msg = cc.check_youtube("vencido")
    assert ok is False
    assert "expirado" in msg


def test_muzpa_dead_session_returns_html_with_200():
    """El bug original: 200 + HTML de login pasaba como válida."""
    resp = httpx.Response(200, text="<html><body>login</body></html>")
    with patch.object(httpx, "get", return_value=resp):
        ok, _msg = cc.check_muzpa("vencida")
    assert ok is False


def test_muzpa_dead_session_returns_json_without_an_account():
    """200 con JSON pero sin la forma de una cuenta real."""
    resp = httpx.Response(200, json={"error": "unauthorized"})
    with patch.object(httpx, "get", return_value=resp):
        ok, _msg = cc.check_muzpa("vencida")
    assert ok is False


@pytest.mark.parametrize("status", [401, 403])
def test_muzpa_rejects_auth_errors(status):
    with patch.object(httpx, "get", return_value=httpx.Response(status)):
        ok, msg = cc.check_muzpa("vencida")
    assert ok is False
    assert "expirada" in msg


def test_muzpa_asks_the_account_endpoint_with_the_session_cookie():
    """
    Guardia de regresión del bug original: el check tiene que preguntar "¿quién
    soy?" y no una búsqueda, que respondía 200 aun con la sesión muerta.
    """
    with patch.object(httpx, "get", return_value=httpx.Response(200, json=_MUZPA_OK)) as mock_get:
        cc.check_muzpa("abc123")
    url = mock_get.call_args.args[0] if mock_get.call_args.args else mock_get.call_args.kwargs["url"]
    assert url.endswith("/a/ms/account")
    assert mock_get.call_args.kwargs["cookies"] == {"SESS": "abc123"}


def test_muzpa_network_error_is_not_a_valid_credential():
    with patch.object(httpx, "get", side_effect=httpx.ConnectError("boom")):
        ok, msg = cc.check_muzpa("abc123")
    assert ok is False
    assert "conexión" in msg


# ── Deezer ───────────────────────────────────────────────────────────────────

def test_deezer_valid_arl_reports_email():
    resp = httpx.Response(200, json={"results": {"USER": {"USER_ID": 42, "EMAIL": "dj@x.com"}}})
    with patch.object(httpx, "get", return_value=resp):
        ok, msg = cc.check_deezer("arl123")
    assert ok is True
    assert msg == f"{cc.CONNECTED} (dj@x.com)"


def test_deezer_expired_arl_has_user_id_zero():
    resp = httpx.Response(200, json={"results": {"USER": {"USER_ID": 0}}})
    with patch.object(httpx, "get", return_value=resp):
        ok, msg = cc.check_deezer("vencido")
    assert ok is False
    assert "expirado" in msg


def test_deezer_empty_arl():
    ok, _msg = cc.check_deezer("")
    assert ok is False


# ── SoundCloud ───────────────────────────────────────────────────────────────

def test_soundcloud_valid_token():
    resp = httpx.Response(200, json={"id": 99, "username": "mateo"})
    with patch.object(httpx, "get", return_value=resp):
        ok, msg = cc.check_soundcloud("2-abc")
    assert ok is True
    assert "mateo" in msg


def test_soundcloud_strips_the_oauth_prefix():
    """El placeholder del form sugiere 'OAuth 2-xxx'; sin strip el header se duplica."""
    resp = httpx.Response(200, json={"id": 1})
    with patch.object(httpx, "get", return_value=resp) as mock_get:
        cc.check_soundcloud("OAuth 2-abc")
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "OAuth 2-abc"


@pytest.mark.parametrize("status", [401, 403])
def test_soundcloud_rejects_auth_errors(status):
    with patch.object(httpx, "get", return_value=httpx.Response(status)):
        ok, msg = cc.check_soundcloud("vencido")
    assert ok is False
    assert "expirado" in msg


def test_soundcloud_200_without_id_is_invalid():
    with patch.object(httpx, "get", return_value=httpx.Response(200, json={})):
        ok, _msg = cc.check_soundcloud("raro")
    assert ok is False


# ── Registro de servicios ────────────────────────────────────────────────────

def test_services_map_matches_user_settings_columns():
    from app.models.user_settings import UserSettings
    for _service, (column, fn) in cc.SERVICES.items():
        assert hasattr(UserSettings, column), f"columna inexistente: {column}"
        assert callable(fn)


def test_services_map_matches_the_agent_login_window():
    """Los nombres de servicio del agente y del server tienen que coincidir."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
    from login_window import SERVICES as AGENT_SERVICES
    assert set(AGENT_SERVICES) == set(cc.SERVICES)


def test_normalize_strips_oauth_prefix_only_for_soundcloud():
    assert cc.normalize("soundcloud", "  OAuth 2-abc ") == "2-abc"
    assert cc.normalize("muzpa", "  SESSVALUE ") == "SESSVALUE"
    assert cc.normalize("deezer", " arl123 ") == "arl123"
