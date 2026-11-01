"""Сохранение результатов тестов и подготовка их к отображению."""
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Request

from config import DATABASE_PATH
from services.formatting import (
    format_datetime_db,
    format_datetime_ru,
    format_duration,
    normalize_date_filter,
    parse_datetime_db,
    parse_json_list,
)
from services.results_search import apply_ai_result_search, normalize_search_text


def save_test_result(
    login: Optional[str],
    filename: Optional[str],
    attempt_number: int,
    total_questions: int,
    total_correct: int,
    percentage: float,
    elapsed_seconds: Optional[int],
    threshold: float,
    forced_finish: bool,
    started_at: Optional[datetime],
    finished_at: datetime,
    user_answers_list: Optional[List[str]] = None,
    questions_list: Optional[List[str]] = None
) -> None:
    if not login or not filename or attempt_number <= 0:
        return

    safe_user_answers = [str(answer) if answer is not None else "" for answer in (user_answers_list or [])]
    safe_questions = [str(question) if question is not None else "" for question in (questions_list or [])]

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO test_results (
            login, filename, attempt_number, total_questions, total_correct, percentage,
            elapsed_seconds, threshold, forced_finish, started_at, finished_at,
            user_answers_json, questions_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(login, filename, attempt_number) DO UPDATE SET
            total_questions = excluded.total_questions,
            total_correct = excluded.total_correct,
            percentage = excluded.percentage,
            elapsed_seconds = excluded.elapsed_seconds,
            threshold = excluded.threshold,
            forced_finish = excluded.forced_finish,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            user_answers_json = excluded.user_answers_json,
            questions_json = excluded.questions_json
        ''',
        (
            login,
            filename,
            attempt_number,
            int(total_questions),
            int(total_correct),
            float(percentage),
            int(elapsed_seconds) if elapsed_seconds is not None else None,
            float(threshold),
            1 if forced_finish else 0,
            format_datetime_db(started_at),
            format_datetime_db(finished_at),
            json.dumps(safe_user_answers, ensure_ascii=False),
            json.dumps(safe_questions, ensure_ascii=False)
        )
    )
    conn.commit()
    conn.close()


def get_results_filters_from_request(request: Request) -> Dict[str, str]:
    search_query = (request.query_params.get("q") or "").strip()
    status_filter = (request.query_params.get("status") or "all").strip().lower()
    test_filter = (request.query_params.get("test") or "").strip()
    date_from = (request.query_params.get("date_from") or "").strip()
    date_to = (request.query_params.get("date_to") or "").strip()

    if status_filter not in ["all", "normal", "forced"]:
        status_filter = "all"

    return {
        "q": search_query,
        "status": status_filter,
        "test": test_filter,
        "date_from": date_from,
        "date_to": date_to
    }


def format_result_status_label(forced_finish: bool) -> str:
    return "Принудительное" if forced_finish else "Обычное"


def format_results_filter_status_label(status_filter: str) -> str:
    if status_filter == "normal":
        return "Обычное"
    if status_filter == "forced":
        return "Принудительное"
    return "Все"


def get_test_result_tests_for_view(user_info: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not user_info:
        return []

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    is_manager = user_info.get("user_type") in ["teacher", "admin"]

    try:
        if is_manager:
            cursor.execute(
                "SELECT DISTINCT filename FROM test_results WHERE filename IS NOT NULL AND filename != '' ORDER BY filename COLLATE NOCASE ASC"
            )
        else:
            cursor.execute(
                "SELECT DISTINCT filename FROM test_results WHERE login = ? AND filename IS NOT NULL AND filename != '' ORDER BY filename COLLATE NOCASE ASC",
                (user_info.get("login"),)
            )
        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    options = []
    for row in rows:
        filename = str(row[0] or "").strip()
        if not filename:
            continue
        options.append({
            "filename": filename,
            "test_name": os.path.splitext(filename)[0]
        })
    return options


def get_test_results_for_view(
    user_info: Optional[Dict[str, Any]],
    limit: Optional[int] = 500,
    search_query: str = "",
    status_filter: str = "all",
    test_filter: str = "",
    date_from: str = "",
    date_to: str = ""
) -> List[Dict[str, Any]]:
    if not user_info:
        return []

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    is_manager = user_info.get("user_type") in ["teacher", "admin"]
    safe_search_query = normalize_search_text(search_query)
    safe_test_filter = (test_filter or "").strip()
    safe_status_filter = status_filter if status_filter in ["all", "normal", "forced"] else "all"
    safe_date_from = normalize_date_filter(date_from)
    safe_date_to = normalize_date_filter(date_to)

    try:
        base_query = '''
            SELECT
                r.id,
                r.login,
                u.last_name,
                u.first_name,
                u.middle_name,
                u.group_name,
                r.filename,
                r.attempt_number,
                r.total_correct,
                r.total_questions,
                r.percentage,
                r.elapsed_seconds,
                r.forced_finish,
                r.finished_at
            FROM test_results r
            LEFT JOIN users u ON u.login = r.login
        '''
        where_parts = []
        params: List[Any] = []

        if not is_manager:
            where_parts.append("r.login = ?")
            params.append(user_info.get("login"))

        if safe_status_filter == "normal":
            where_parts.append("r.forced_finish = 0")
        elif safe_status_filter == "forced":
            where_parts.append("r.forced_finish = 1")

        if safe_test_filter:
            where_parts.append("r.filename = ?")
            params.append(safe_test_filter)

        if safe_date_from:
            where_parts.append("datetime(r.finished_at) >= datetime(?)")
            params.append(f"{safe_date_from} 00:00:00")

        if safe_date_to:
            where_parts.append("datetime(r.finished_at) <= datetime(?)")
            params.append(f"{safe_date_to} 23:59:59")

        where_sql = ""
        if where_parts:
            where_sql = " WHERE " + " AND ".join(where_parts)

        order_part = " ORDER BY datetime(r.finished_at) DESC, r.id DESC"
        query_limit = limit if not safe_search_query else None
        if query_limit is not None:
            order_part += " LIMIT ?"
            params.append(query_limit)
        cursor.execute(base_query + where_sql + order_part, tuple(params))

        rows = cursor.fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    results = []
    for row in rows:
        (
            result_id,
            login,
            last_name,
            first_name,
            middle_name,
            group_name,
            filename,
            attempt_number,
            total_correct,
            total_questions,
            percentage,
            elapsed_seconds,
            forced_finish,
            finished_at
        ) = row

        full_name = f"{(last_name or '').strip()} {(first_name or '').strip()}".strip()
        if middle_name:
            full_name = f"{full_name} {middle_name}".strip()
        if not full_name:
            full_name = login or "Неизвестный пользователь"

        finished_at_dt = parse_datetime_db(finished_at)
        try:
            percentage_value = float(percentage)
        except (TypeError, ValueError):
            percentage_value = 0.0

        results.append({
            "id": int(result_id or 0),
            "login": login or "",
            "full_name": full_name,
            "group_name": group_name or "",
            "filename": filename or "",
            "test_name": os.path.splitext(filename or "")[0],
            "attempt_number": int(attempt_number or 0),
            "total_correct": int(total_correct or 0),
            "total_questions": int(total_questions or 0),
            "percentage_value": percentage_value,
            "percentage_display": f"{percentage_value:.1f}",
            "elapsed_time_display": format_duration(elapsed_seconds),
            "forced_finish": bool(forced_finish),
            "finished_at_display": format_datetime_ru(finished_at_dt) if finished_at_dt else (finished_at or "—")
        })

    if safe_search_query:
        results = apply_ai_result_search(results, safe_search_query)
        if limit is not None:
            results = results[:limit]

    return results


def get_test_result_detail_for_view(user_info: Optional[Dict[str, Any]], result_id: int) -> Optional[Dict[str, Any]]:
    if not user_info or result_id <= 0:
        return None

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            '''
            SELECT
                r.id,
                r.login,
                u.last_name,
                u.first_name,
                u.middle_name,
                u.group_name,
                r.filename,
                r.attempt_number,
                r.total_correct,
                r.total_questions,
                r.percentage,
                r.elapsed_seconds,
                r.forced_finish,
                r.started_at,
                r.finished_at,
                r.user_answers_json,
                r.questions_json
            FROM test_results r
            LEFT JOIN users u ON u.login = r.login
            WHERE r.id = ?
            ''',
            (result_id,)
        )
        row = cursor.fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()

    if not row:
        return None

    (
        db_result_id,
        login,
        last_name,
        first_name,
        middle_name,
        group_name,
        filename,
        attempt_number,
        total_correct,
        total_questions,
        percentage,
        elapsed_seconds,
        forced_finish,
        started_at,
        finished_at,
        user_answers_json,
        questions_json
    ) = row

    is_manager = user_info.get("user_type") in ["teacher", "admin"]
    current_login = user_info.get("login")
    if not is_manager and login != current_login:
        return None

    full_name = f"{(last_name or '').strip()} {(first_name or '').strip()}".strip()
    if middle_name:
        full_name = f"{full_name} {middle_name}".strip()
    if not full_name:
        full_name = login or "Неизвестный пользователь"

    questions_list = parse_json_list(questions_json)
    user_answers_list = parse_json_list(user_answers_json)

    try:
        total_questions_value = int(total_questions or 0)
    except (TypeError, ValueError):
        total_questions_value = 0

    items_count = max(total_questions_value, len(questions_list), len(user_answers_list))
    answer_items = []
    for idx in range(items_count):
        raw_question = questions_list[idx] if idx < len(questions_list) else ""
        raw_answer = user_answers_list[idx] if idx < len(user_answers_list) else ""
        question_text = raw_question.strip() if isinstance(raw_question, str) else str(raw_question or "")
        answer_text = raw_answer if isinstance(raw_answer, str) else str(raw_answer or "")

        answer_items.append({
            "index": idx + 1,
            "question": question_text or f"Вопрос {idx + 1}",
            "user_answer": answer_text.strip(),
            "has_answer": bool(answer_text.strip())
        })

    finished_at_dt = parse_datetime_db(finished_at)
    started_at_dt = parse_datetime_db(started_at)
    try:
        percentage_value = float(percentage)
    except (TypeError, ValueError):
        percentage_value = 0.0

    return {
        "id": int(db_result_id or 0),
        "login": login or "",
        "full_name": full_name,
        "group_name": group_name or "",
        "filename": filename or "",
        "test_name": os.path.splitext(filename or "")[0],
        "attempt_number": int(attempt_number or 0),
        "total_correct": int(total_correct or 0),
        "total_questions": total_questions_value,
        "percentage_display": f"{percentage_value:.1f}",
        "elapsed_time_display": format_duration(elapsed_seconds),
        "forced_finish": bool(forced_finish),
        "started_at_display": format_datetime_ru(started_at_dt) if started_at_dt else "—",
        "finished_at_display": format_datetime_ru(finished_at_dt) if finished_at_dt else (finished_at or "—"),
        "answer_items": answer_items
    }
