"""Настройки тестов: срок, лимит времени и попыток, доступ по студентам и группам."""
import re
import sqlite3
from typing import Any, Dict, List, Optional

import pandas as pd

from config import DATABASE_PATH


def get_test_settings(filename: str) -> Dict[str, Any]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT time_limit_minutes, available_until, allowed_students, allowed_groups, max_attempts FROM test_settings WHERE filename = ?",
        (filename,)
    )
    row = cursor.fetchone()
    conn.close()

    time_limit_minutes = None
    available_until = None
    allowed_students = ""
    allowed_groups = ""
    max_attempts = None
    if row:
        if row[0] is not None:
            try:
                parsed_limit = int(row[0])
                time_limit_minutes = parsed_limit if parsed_limit > 0 else None
            except (TypeError, ValueError):
                time_limit_minutes = None
        if row[1]:
            available_until = str(row[1]).strip()
        if len(row) > 2 and row[2]:
            allowed_students = str(row[2]).strip()
        if len(row) > 3 and row[3]:
            allowed_groups = str(row[3]).strip()
        if len(row) > 4 and row[4] is not None:
            try:
                parsed_attempts = int(row[4])
                max_attempts = parsed_attempts if parsed_attempts > 0 else None
            except (TypeError, ValueError):
                max_attempts = None

    return {
        "time_limit_minutes": time_limit_minutes,
        "available_until": available_until,
        "allowed_students": allowed_students,
        "allowed_groups": allowed_groups,
        "max_attempts": max_attempts
    }


def parse_access_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[,\n;]+", str(value))
    return [p.strip() for p in parts if p.strip()]


def get_user_attempts_count(login: Optional[str], filename: str) -> int:
    if not login or not filename:
        return 0

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT attempts_count FROM test_attempts WHERE login = ? AND filename = ?",
        (login, filename)
    )
    row = cursor.fetchone()
    conn.close()

    if not row or row[0] is None:
        return 0

    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError):
        return 0


def increment_user_attempts_count(login: Optional[str], filename: str) -> int:
    if not login or not filename:
        return 0

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO test_attempts (login, filename, attempts_count, updated_at)
        VALUES (?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(login, filename) DO UPDATE SET
            attempts_count = attempts_count + 1,
            updated_at = CURRENT_TIMESTAMP
        ''',
        (login, filename)
    )
    cursor.execute(
        "SELECT attempts_count FROM test_attempts WHERE login = ? AND filename = ?",
        (login, filename)
    )
    row = cursor.fetchone()
    conn.commit()
    conn.close()

    if not row or row[0] is None:
        return 0

    try:
        return max(0, int(row[0]))
    except (TypeError, ValueError):
        return 0


def get_attempt_limit_status(settings: Dict[str, Any], login: Optional[str], filename: str) -> Dict[str, Any]:
    max_attempts = settings.get("max_attempts")
    attempts_used = get_user_attempts_count(login, filename) if login else 0
    limit_reached = bool(max_attempts and attempts_used >= max_attempts)
    attempts_left = None
    if max_attempts:
        attempts_left = max(0, max_attempts - attempts_used)

    return {
        "max_attempts": max_attempts,
        "attempts_used": attempts_used,
        "attempts_left": attempts_left,
        "limit_reached": limit_reached
    }


def can_user_access_test(settings: Dict[str, Any], user_info: Optional[Dict[str, Any]]) -> (bool, str):
    allowed_students = parse_access_list(settings.get("allowed_students"))
    allowed_groups = parse_access_list(settings.get("allowed_groups"))

    if not allowed_students and not allowed_groups:
        return True, ""

    if user_info and user_info.get("user_type") in ["teacher", "admin"]:
        return True, ""

    if not user_info:
        return False, "Доступ только для выбранных студентов или групп."

    login = user_info.get("login")
    group_name = user_info.get("group_name")

    if login and login in allowed_students:
        return True, ""
    if group_name and group_name in allowed_groups:
        return True, ""

    return False, "Нет доступа к тесту."


def get_test_settings_for_form(filename: str):
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT time_limit_minutes, available_until, allowed_students, allowed_groups, max_attempts FROM test_settings WHERE filename = ?",
        (filename,)
    )
    row = cursor.fetchone()
    conn.close()

    time_limit_minutes = ""
    available_until = ""
    allowed_students = ""
    allowed_groups = ""
    max_attempts = ""
    if row:
        if row[0] is not None:
            try:
                parsed = int(row[0])
                if parsed > 0:
                    time_limit_minutes = str(parsed)
            except (TypeError, ValueError):
                time_limit_minutes = ""
        if row[1]:
            available_until = str(row[1]).strip()
        if len(row) > 2 and row[2]:
            allowed_students = str(row[2]).strip()
        if len(row) > 3 and row[3]:
            allowed_groups = str(row[3]).strip()
        if len(row) > 4 and row[4] is not None:
            try:
                parsed_attempts = int(row[4])
                if parsed_attempts > 0:
                    max_attempts = str(parsed_attempts)
            except (TypeError, ValueError):
                max_attempts = ""

    return {
        "time_limit_minutes": time_limit_minutes,
        "available_until": available_until,
        "allowed_students": allowed_students,
        "allowed_groups": allowed_groups,
        "max_attempts": max_attempts
    }


def normalize_access_value(value: str) -> str:
    parts = parse_access_list(value)
    return ", ".join(parts)


def get_access_students() -> list[dict[str, str]]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT last_name, first_name, middle_name, login, group_name
        FROM users
        WHERE user_type = 'student'
        ORDER BY last_name, first_name, login
    """)
    rows = cursor.fetchall()
    conn.close()

    students = []
    for last_name, first_name, middle_name, login, group_name in rows:
        if not login:
            continue
        full_name = f"{last_name or ''} {first_name or ''}".strip()
        if middle_name:
            full_name = f"{full_name} {middle_name}".strip()
        label = full_name if full_name else "Без имени"
        if group_name:
            label = f"{label} — {group_name}"
        students.append({
            "value": login,
            "label": label
        })
    return students


def get_access_groups() -> list[str]:
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT group_name
        FROM users
        WHERE group_name IS NOT NULL AND TRIM(group_name) <> ''
          AND user_type != 'admin'
        ORDER BY group_name
    """)
    groups = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()
    return groups


def build_access_context(test_settings: dict) -> dict:
    return {
        "access_students": get_access_students(),
        "access_groups": get_access_groups(),
        "allowed_students_list": parse_access_list(test_settings.get("allowed_students", "")),
        "allowed_groups_list": parse_access_list(test_settings.get("allowed_groups", "")),
    }


def upsert_test_settings(
    filename: str,
    time_limit_minutes: str,
    max_attempts: str,
    available_until: str,
    allowed_students: str,
    allowed_groups: str
):
    normalized_limit = None
    limit_value = (time_limit_minutes or "").strip()
    if limit_value:
        parsed_limit = int(limit_value)
        if parsed_limit <= 0:
            raise ValueError("Лимит времени должен быть больше 0.")
        normalized_limit = parsed_limit

    normalized_max_attempts = None
    max_attempts_value = (max_attempts or "").strip()
    if max_attempts_value:
        parsed_attempts = int(max_attempts_value)
        if parsed_attempts <= 0:
            raise ValueError("Лимит попыток должен быть больше 0.")
        normalized_max_attempts = parsed_attempts

    normalized_date = (available_until or "").strip() or None
    if normalized_date:
        try:
            pd.to_datetime(normalized_date, format="%Y-%m-%d", errors="raise")
        except Exception:
            raise ValueError("Некорректная дата дедлайна. Используйте формат YYYY-MM-DD.")

    normalized_students = normalize_access_value(allowed_students)
    normalized_groups = normalize_access_value(allowed_groups)
    stored_students = normalized_students if normalized_students else None
    stored_groups = normalized_groups if normalized_groups else None

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO test_settings (filename, time_limit_minutes, max_attempts, available_until, allowed_students, allowed_groups, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(filename) DO UPDATE SET
            time_limit_minutes=excluded.time_limit_minutes,
            max_attempts=excluded.max_attempts,
            available_until=excluded.available_until,
            allowed_students=excluded.allowed_students,
            allowed_groups=excluded.allowed_groups,
            updated_at=CURRENT_TIMESTAMP
        ''',
        (filename, normalized_limit, normalized_max_attempts, normalized_date, stored_students, stored_groups)
    )
    conn.commit()
    conn.close()
