"""
Authentication helpers: password hashing and session cookie management.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from app.models.user import User

_SECRET_KEY = "music-collector-secret-key-change-in-prod"
_COOKIE_NAME = "mc_session"
_signer = URLSafeSerializer(_SECRET_KEY, salt="session")
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


def create_user(username: str, password: str, is_admin: bool, db: Session) -> User:
    user = User(
        username=username,
        hashed_password=hash_password(password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def has_any_user(db: Session) -> bool:
    return db.query(User).count() > 0
