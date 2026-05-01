"""Simple in-memory per-user rate limiter."""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class UserRateLimiter:
    """Allow at most `calls` per `window` seconds per user_id."""

    def __init__(self, calls: int, window: int) -> None:
        self._calls = calls
        self._window = window
        self._history: dict[int, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_limited(self, user_id: int) -> bool:
        """Return True if the user is over the limit (does NOT consume a slot)."""
        now = time.time()
        with self._lock:
            self._history[user_id] = [
                t for t in self._history[user_id] if now - t < self._window
            ]
            return len(self._history[user_id]) >= self._calls

    def acquire(self, user_id: int) -> bool:
        """Try to consume a slot. Returns False if rate limited."""
        now = time.time()
        with self._lock:
            self._history[user_id] = [
                t for t in self._history[user_id] if now - t < self._window
            ]
            if len(self._history[user_id]) >= self._calls:
                return False
            self._history[user_id].append(now)
            return True
