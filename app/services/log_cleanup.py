"""
Log retention cleanup.

Retention policy:
  app_events  info    → delete after 30 days
  app_events  warning → delete after 60 days
  app_events  error   → delete after 180 days
  track_history       → kept indefinitely
  user_reports        → kept indefinitely
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.app_event import AppEvent, EventLevel

logger = logging.getLogger(__name__)

_RETENTION: dict[EventLevel, timedelta] = {
    EventLevel.info:    timedelta(days=30),
    EventLevel.warning: timedelta(days=60),
    EventLevel.error:   timedelta(days=180),
}


def run_cleanup(db: Session) -> dict[str, int]:
    """
    Delete expired app_events per retention policy.
    Returns {level: deleted_count} for each level.
    """
    now = datetime.now(timezone.utc)
    deleted: dict[str, int] = {}

    for level, max_age in _RETENTION.items():
        cutoff = now - max_age
        count = (
            db.query(AppEvent)
            .filter(AppEvent.level == level, AppEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        deleted[level.value] = count
        if count:
            logger.info(
                "Cleaned %d %r app_events older than %s",
                count, level.value, cutoff.date(),
            )

    db.commit()
    total = sum(deleted.values())
    logger.info("Log cleanup complete: %d total rows deleted", total)
    return deleted
