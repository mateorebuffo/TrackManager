"""
Authentication helpers: password hashing and session cookie management.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from itsdangerous import BadSignature, URLSafeSerializer, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.models.user import User

from app.config import settings as _cfg
_COOKIE_NAME = "mc_session"
_signer = URLSafeSerializer(_cfg.secret_key, salt="session")
_ITERATIONS = 260_000


# ── Password helpers (PBKDF2-SHA256, stdlib only) ────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2:sha256:{_ITERATIONS}:{salt}:{key.hex()}"


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _, _, iters, salt, key_hex = hashed.split(":", 4)
        key = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), int(iters))
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


# ── Session cookie helpers ───────────────────────────────────────────────────

def make_session_token(user_id: int) -> str:
    return _signer.dumps({"uid": user_id, "nonce": secrets.token_hex(8)})


def decode_session_token(token: str) -> Optional[int]:
    try:
        data = _signer.loads(token)
        return int(data["uid"])
    except (BadSignature, KeyError, ValueError):
        return None


# ── Password reset link ──────────────────────────────────────────────────────
# Sin tabla: el token firma el final del hash actual. Al cambiar la contraseña el
# hash cambia y el link deja de valer, así que sirve una sola vez.

_reset_signer = URLSafeTimedSerializer(_cfg.secret_key, salt="password-reset")
RESET_MAX_AGE = 60 * 60  # 1 hora


def make_reset_token(user: User) -> str:
    return _reset_signer.dumps({"uid": user.id, "pw": user.hashed_password[-16:]})


def read_reset_token(token: str, db: Session) -> Optional[User]:
    try:
        data = _reset_signer.loads(token, max_age=RESET_MAX_AGE)
        user = get_user_by_id(int(data["uid"]), db)
    except (BadSignature, KeyError, ValueError, TypeError):
        return None
    if not user or not user.is_active or not hmac.compare_digest(user.hashed_password[-16:], data["pw"]):
        return None
    return user


# ── DB helpers ───────────────────────────────────────────────────────────────

def get_user_by_username(username: str, db: Session) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_id(user_id: int, db: Session) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def authenticate(username: str, password: str, db: Session) -> Optional[User]:
    user = get_user_by_username(username, db)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def clean_email(email: str) -> Optional[str]:
    """Minúsculas y sin espacios; vacío → None (la columna es única y NULL no choca)."""
    email = (email or "").strip().lower()
    return email or None


def get_user_by_email(email: str, db: Session) -> Optional[User]:
    email = clean_email(email)
    return db.query(User).filter(User.email == email).first() if email else None


def create_user(username: str, password: str, is_admin: bool, db: Session, email: str = "") -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_admin=is_admin,
        email=clean_email(email),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def has_any_user(db: Session) -> bool:
    return db.query(User).count() > 0
