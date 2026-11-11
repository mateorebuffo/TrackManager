"""Envío de mails con Resend. Sin RESEND_API_KEY no manda nada y devuelve False."""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, text: str) -> bool:
    if not settings.resend_api_key:
        logger.info("RESEND_API_KEY not set, skipping email %r", subject)
        return False
    try:
        import resend
        resend.api_key = settings.resend_api_key
        resend.Emails.send({"from": settings.mail_from, "to": [to], "subject": subject, "text": text})
        return True
    except Exception:
        logger.exception("Failed to send email %r", subject)
        return False
