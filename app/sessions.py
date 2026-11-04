"""Серверные сессии: ключ API живёт в памяти процесса, а не в браузере.

Зачем так: localStorage читается любым скриптом на странице, а нам нужно,
чтобы контент-менеджер вводил ключ один раз и не видел его в исходниках
браузерного хранилища. Поэтому ключ лежит на сервере, а браузер получает
только непрозрачный идентификатор в HttpOnly-cookie.

Цена решения: при перезапуске сервера ключ исчезает, его нужно ввести снова.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

SESSION_COOKIE = "pcard_session"
SESSION_TTL_SECONDS = 12 * 3600
MAX_SESSIONS = 200


@dataclass
class ClientSession:
    provider: str = ""
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)


class SessionStore:
    """Потокобезопасное хранилище сессий в памяти процесса."""

    def __init__(self, ttl: int = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS) -> None:
        self._items: dict[str, ClientSession] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_sessions = max_sessions

    def new_id(self) -> str:
        return secrets.token_urlsafe(24)

    def get(self, session_id: str | None) -> ClientSession | None:
        if not session_id:
            return None
        with self._lock:
            session = self._items.get(session_id)
            if session is None:
                return None
            if time.time() - session.last_used > self._ttl:
                self._items.pop(session_id, None)
                return None
            session.last_used = time.time()
            return session

    def update(
        self,
        session_id: str,
        *,
        provider: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> ClientSession:
        with self._lock:
            self._purge_locked()
            session = self._items.get(session_id)
            if session is None:
                session = ClientSession()
                self._items[session_id] = session
            if provider is not None:
                session.provider = provider
            if api_key:
                session.api_key = api_key
            if model is not None:
                session.model = model
            if base_url is not None:
                session.base_url = base_url
            session.last_used = time.time()
            self._trim_locked()
            return session

    def clear(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._items.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._purge_locked()
            return len(self._items)

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [key for key, item in self._items.items() if now - item.last_used > self._ttl]
        for key in expired:
            self._items.pop(key, None)

    def _trim_locked(self) -> None:
        if len(self._items) <= self._max_sessions:
            return
        ordered = sorted(self._items.items(), key=lambda pair: pair[1].last_used)
        for key, _ in ordered[: len(self._items) - self._max_sessions]:
            self._items.pop(key, None)


store = SessionStore()


def mask_key(api_key: str) -> str:
    """Показываем только хвост: «••••a1b2». Сам ключ наружу не отдаём."""
    if not api_key:
        return ""
    tail = api_key[-4:] if len(api_key) >= 8 else ""
    return f"••••{tail}" if tail else "••••"


def session_view(session: ClientSession | None) -> dict[str, object]:
    if session is None:
        return {"has_key": False, "masked": "", "provider": "", "model": "", "base_url": ""}
    return {
        "has_key": bool(session.api_key),
        "masked": mask_key(session.api_key),
        "provider": session.provider,
        "model": session.model,
        "base_url": session.base_url,
    }
