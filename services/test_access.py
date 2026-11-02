"""Владельцы тестов и доступ преподавателей к чужим тестам.

Правила:
- у каждого теста есть владелец — преподаватель (или администратор), который его создал
  или загрузил;
- преподаватель видит и редактирует только свои тесты и тесты, к которым ему открыли
  доступ на редактирование;
- результаты теста (и проверка спорных ответов) видны владельцу и тем, кому открыт доступ
  к результатам. Доступ к тесту и к результатам выдаются независимо;
- переименовать, удалить тест и настроить доступ может только владелец.
Студентов это не касается: их доступ задаётся списками студентов и групп в настройках теста.
"""
import glob
import json
import os
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Set

from config import DATABASE_PATH, UPLOAD_DIR

MANAGER_TYPES = ("teacher", "admin")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_manager(user_info: Optional[Dict[str, Any]]) -> bool:
    return bool(user_info) and user_info.get("user_type") in MANAGER_TYPES


def init_access_tables() -> None:
    with _connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(test_settings)").fetchall()}
        if "owner_login" not in columns:
            conn.execute("ALTER TABLE test_settings ADD COLUMN owner_login TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS test_shares (
                filename TEXT NOT NULL,
                teacher_login TEXT NOT NULL,
                can_edit INTEGER NOT NULL DEFAULT 0,
                can_view_results INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (filename, teacher_login)
            )
            """
        )


def migrate_legacy_owners() -> int:
    """Тестам, созданным до появления владельцев, назначается первый администратор.

    Он может открыть к ним доступ другим преподавателям. Возвращает число тестов.
    """
    with _connect() as conn:
        admin = conn.execute("SELECT login FROM users WHERE user_type = 'admin' ORDER BY id LIMIT 1").fetchone()
        if not admin:
            return 0
        names = {os.path.basename(p) for p in glob.glob(os.path.join(UPLOAD_DIR, "*.xlsx"))}
        names |= {r[0] for r in conn.execute("SELECT DISTINCT filename FROM test_results").fetchall() if r[0]}
        for name in names:
            conn.execute("INSERT OR IGNORE INTO test_settings (filename) VALUES (?)", (name,))
        cur = conn.execute("UPDATE test_settings SET owner_login = ? WHERE owner_login IS NULL", (admin[0],))
        count = cur.rowcount
    if count:
        print(f"[access] Тестам без владельца ({count}) назначен администратор «{admin[0]}»")
    return count


# ------------------------------------------------------------------ владелец
def get_owner(filename: str) -> Optional[str]:
    with _connect() as conn:
        row = conn.execute("SELECT owner_login FROM test_settings WHERE filename = ?", (filename,)).fetchone()
    return row[0] if row else None


def set_owner(filename: str, login: str) -> None:
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO test_settings (filename) VALUES (?)", (filename,))
        conn.execute("UPDATE test_settings SET owner_login = ? WHERE filename = ?", (login, filename))


# ------------------------------------------------------------------ проверки прав
def get_test_access(user_info: Optional[Dict[str, Any]], filename: str) -> Dict[str, bool]:
    """Права пользователя-преподавателя на тест: owner, edit, results."""
    none = {"owner": False, "edit": False, "results": False}
    if not is_manager(user_info) or not filename:
        return none
    login = user_info["login"]
    with _connect() as conn:
        owner = conn.execute("SELECT owner_login FROM test_settings WHERE filename = ?", (filename,)).fetchone()
        if owner and owner[0] == login:
            return {"owner": True, "edit": True, "results": True}
        share = conn.execute(
            "SELECT can_edit, can_view_results FROM test_shares WHERE filename = ? AND teacher_login = ?",
            (filename, login),
        ).fetchone()
    if not share:
        return none
    return {"owner": False, "edit": bool(share[0]), "results": bool(share[1])}


def can_edit_test(user_info: Optional[Dict[str, Any]], filename: str) -> bool:
    return get_test_access(user_info, filename)["edit"]


def can_view_test_results(user_info: Optional[Dict[str, Any]], filename: str) -> bool:
    return get_test_access(user_info, filename)["results"]


def _tests_where(login: str, flag: str) -> Set[str]:
    with _connect() as conn:
        owned = conn.execute("SELECT filename FROM test_settings WHERE owner_login = ?", (login,)).fetchall()
        shared = conn.execute(
            f"SELECT filename FROM test_shares WHERE teacher_login = ? AND {flag} = 1", (login,)
        ).fetchall()
    return {r[0] for r in owned} | {r[0] for r in shared}


def editable_tests(login: str) -> Set[str]:
    """Тесты, которые преподаватель видит в списке и может редактировать."""
    return _tests_where(login, "can_edit")


def results_tests(login: str) -> Set[str]:
    """Тесты, результаты которых преподаватель может смотреть и проверять."""
    return _tests_where(login, "can_view_results")


# ------------------------------------------------------------------ доступ для других преподавателей
def _full_name(row: sqlite3.Row) -> str:
    return " ".join(filter(None, [row["last_name"], row["first_name"], row["middle_name"]])) or row["login"]


def get_user_display_name(login: Optional[str]) -> str:
    if not login:
        return "—"
    with _connect() as conn:
        row = conn.execute(
            "SELECT login, last_name, first_name, middle_name FROM users WHERE login = ?", (login,)
        ).fetchone()
    return _full_name(row) if row else login


def get_teacher_shares(filename: Optional[str], owner_login: str) -> List[Dict[str, Any]]:
    """Все преподаватели (кроме владельца) с отметками, какой доступ им открыт."""
    with _connect() as conn:
        teachers = conn.execute(
            "SELECT login, last_name, first_name, middle_name, user_type FROM users "
            "WHERE user_type IN ('teacher', 'admin') AND login != ? ORDER BY last_name, first_name",
            (owner_login,),
        ).fetchall()
        shares = {}
        if filename:
            shares = {
                r["teacher_login"]: r
                for r in conn.execute("SELECT * FROM test_shares WHERE filename = ?", (filename,)).fetchall()
            }
    return [
        {
            "login": t["login"],
            "full_name": _full_name(t),
            "role": "администратор" if t["user_type"] == "admin" else "преподаватель",
            "can_edit": bool(shares.get(t["login"]) and shares[t["login"]]["can_edit"]),
            "can_view_results": bool(shares.get(t["login"]) and shares[t["login"]]["can_view_results"]),
        }
        for t in teachers
    ]


def parse_teacher_shares(raw: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    """Поле формы (JSON). None — поле не прислано, доступ не меняем."""
    if raw is None or raw == "":
        return None
    try:
        items = json.loads(raw)
    except ValueError:
        raise ValueError("Некорректный список доступа преподавателей")
    if not isinstance(items, list):
        raise ValueError("Некорректный список доступа преподавателей")
    return [
        {"login": str(i.get("login", "")), "can_edit": bool(i.get("can_edit")),
         "can_view_results": bool(i.get("can_view_results"))}
        for i in items if isinstance(i, dict) and i.get("login")
    ]


def save_teacher_shares(filename: str, owner_login: str, shares: Iterable[Dict[str, Any]]) -> None:
    """Полностью заменяет список доступа. Принимаются только существующие преподаватели."""
    with _connect() as conn:
        allowed = {
            r[0] for r in conn.execute(
                "SELECT login FROM users WHERE user_type IN ('teacher', 'admin') AND login != ?", (owner_login,)
            ).fetchall()
        }
        conn.execute("DELETE FROM test_shares WHERE filename = ?", (filename,))
        conn.executemany(
            "INSERT INTO test_shares (filename, teacher_login, can_edit, can_view_results) VALUES (?, ?, ?, ?)",
            [
                (filename, s["login"], int(s["can_edit"]), int(s["can_view_results"]))
                for s in shares
                if s["login"] in allowed and (s["can_edit"] or s["can_view_results"])
            ],
        )


def rename_test_access(old_filename: str, new_filename: str, conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM test_shares WHERE filename = ?", (new_filename,))
    conn.execute("UPDATE test_shares SET filename = ? WHERE filename = ?", (new_filename, old_filename))
