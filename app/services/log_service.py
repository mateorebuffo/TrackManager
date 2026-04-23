"""
Central helpers for writing app events, track history, and user reports.

All functions catch their own exceptions so a logging failure never
interrupts the core application flow.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.app_event import AppEvent, EventLevel
from app.models.track_history import TrackHistory
from app.models.user_report import ReportStatus, UserReport

logger = logging.getLogger(__name__)

# Keys whose values must never appear in stored context payloads.
_REDACT_SUBSTRINGS = ("token", "secret", "password", "arl", "sess", "key")

_MAX_CONTEXT_BYTES = 2048
_MAX_DESC_CHARS = 2000


def _sanitize(data: dict) -> dict:
    """Recursively redact sensitive-looking keys."""
    result: dict = {}
    for k, v in data.items():
        k_lower = k.lower()
        if any(s in k_lower for s in _REDACT_SUBSTRINGS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _sanitize(v)
        else:
            result[k] = v
    return result


def _prepare_context(data: dict | None) -> dict | None:
    if not data:
        return None
    sanitized = _sanitize(data)
    encoded = json.dumps(sanitized, default=str)
    if len(encoded.encode()) > _MAX_CONTEXT_BYTES:
        return {"_truncated": True, "preview": encoded[:512]}
    return sanitized


def log_event(
    db: Session,
    event_type: str,
    message: str,
    *,
    level: str = "info",
    user_id: int | None = None,
    track_id: int | None = None,
    context: dict[str, Any] | None = None,
    operation_id: str | None = None,
    source: str | None = None,
    commit: bool = False,
) -> AppEvent | None:
    """
    Write an AppEvent row.

    commit=True issues an immediate db.commit() so the event is persisted
    independently of any surrounding transaction (use for sync_started etc.).
    Omit commit=True when the event should be part of the caller's transaction.
    """
    try:
        event = AppEvent(
            user_id=user_id,
            track_id=track_id,
            level=EventLevel(level),
            event_type=event_type,
            message=message[:500],
            context_json=_prepare_context(context),
            operation_id=operation_id,
            source=source,
        )
        db.add(event)
        if commit:
            db.commit()
        return event
    except Exception:
        logger.exception("Failed to write AppEvent (event_type=%r)", event_type)
        return None


def add_track_history(
    db: Session,
    track_id: int,
    action: str,
    *,
    user_id: int | None = None,
    details: dict[str, Any] | None = None,
    commit: bool = False,
) -> TrackHistory | None:
    """Append one entry to a track's history timeline."""
    try:
        entry = TrackHistory(
            track_id=track_id,
            action=action,
            user_id=user_id,
            details_json=_prepare_context(details),
        )
        db.add(entry)
        if commit:
            db.commit()
        return entry
    except Exception:
        logger.exception(
            "Failed to write TrackHistory (track_id=%r action=%r)", track_id, action
        )
        return None


def create_user_report(
    db: Session,
    user_id: int,
    category: str,
    description: str,
    *,
    track_id: int | None = None,
    commit: bool = True,
) -> UserReport | None:
    """Create a user problem report."""
    try:
        report = UserReport(
            user_id=user_id,
            track_id=track_id,
            category=category,
            description=description[:_MAX_DESC_CHARS],
            status=ReportStatus.open,
        )
        db.add(report)
        if commit:
            db.commit()
            db.refresh(report)
        return report
    except Exception:
        logger.exception("Failed to create UserReport")
        return None
