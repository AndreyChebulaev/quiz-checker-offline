"""Сессии пользователей, хранящиеся в базе данных.

В отличие от словаря в памяти, сессии переживают перезапуск сервера.
В БД хранится не сам токен, а его SHA-256: утечка файла БД
не позволит войти под чужой сессией.
"""
import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from config import DATABASE_PATH as DATABASE_NAME
from config import SESSION_LIFETIME_HOURS

SESSION_LIFETIME = timedelta(hours=SESSION_LIFETIME_HOURS)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DATABASE_NAME)


def init_sessions_table() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                login TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_login ON sessions(login)")


def create_session(username: str) -> str:
    """Создаёт сессию и возвращает токен для cookie."""
    token = secrets.token_hex(32)
    now = datetime.now()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, login, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), username, now.isoformat(), (now + SESSION_LIFETIME).isoformat()),
        )
    return token


def get_user_from_session(session_token: Optional[str]) -> Optional[str]:
    """Логин пользователя по токену или None, если сессии нет или она истекла."""
    if not session_token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT login, expires_at FROM sessions WHERE token_hash = ?",
            (_token_hash(session_token),),
        ).fetchone()
    if not row:
        return None
    login, expires_at = row
    if datetime.fromisoformat(expires_at) < datetime.now():
        remove_session(session_token)
        return None
    return login


def remove_session(session_token: Optional[str]) -> None:
    if not session_token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(session_token),))


def remove_user_sessions(login: str) -> None:
    """Завершает все сессии пользователя (смена пароля, удаление, смена логина)."""
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE login = ?", (login,))


def rename_user_sessions(old_login: str, new_login: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE sessions SET login = ? WHERE login = ?", (new_login, old_login))
