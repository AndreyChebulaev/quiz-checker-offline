"""Оценки отдельных ответов и их проверка преподавателем."""
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from config import DATABASE_PATH
from services.formatting import format_datetime_ru, parse_datetime_db
from services.test_access import results_tests
from services.scoring import VERDICT_CORRECT, VERDICT_INCORRECT, VERDICT_LABELS, AnswerEvaluation

FINAL_VERDICT_SQL = "COALESCE(a.final_verdict, a.auto_verdict)"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def save_answer_results(result_id: int, questions: Sequence[str], answers: Sequence[str],
                        evaluations: Sequence[AnswerEvaluation]) -> None:
    """Сохраняет оценку каждого ответа попытки (повторный вызов перезаписывает)."""
    with _connect() as conn:
        conn.execute("DELETE FROM answer_results WHERE result_id = ?", (result_id,))
        conn.executemany(
            """
            INSERT INTO answer_results
                (result_id, question_index, question, user_answer, best_reference, score, auto_verdict, review_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (result_id, idx, question, answer, ev.best_reference, ev.score, ev.verdict, ev.reason or None)
                for idx, (question, answer, ev) in enumerate(zip(questions, answers, evaluations))
            ],
        )


def recalculate_result(conn: sqlite3.Connection, result_id: int) -> Dict[str, Any]:
    """Пересчитывает итог попытки по решениям (автоматическим и преподавателя)."""
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN {FINAL_VERDICT_SQL} = 'correct' THEN 1 ELSE 0 END) AS correct,
            SUM(CASE WHEN {FINAL_VERDICT_SQL} = 'review' THEN 1 ELSE 0 END) AS pending,
            COUNT(*) AS total
        FROM answer_results a WHERE a.result_id = ?
        """,
        (result_id,),
    ).fetchone()
    total_questions = conn.execute(
        "SELECT total_questions FROM test_results WHERE id = ?", (result_id,)
    ).fetchone()[0] or row["total"] or 0
    correct, pending = int(row["correct"] or 0), int(row["pending"] or 0)
    percentage = correct / total_questions * 100 if total_questions else 0.0
    conn.execute(
        "UPDATE test_results SET total_correct = ?, percentage = ?, pending_review = ? WHERE id = ?",
        (correct, percentage, pending, result_id),
    )
    return {"total_correct": correct, "total_questions": int(total_questions),
            "percentage_display": f"{percentage:.1f}", "pending_review": pending}


def set_answer_verdict(answer_id: int, verdict: Optional[str], reviewer: str) -> Optional[Dict[str, Any]]:
    """Решение преподавателя по ответу. verdict=None — вернуть автоматическое решение."""
    if verdict not in (VERDICT_CORRECT, VERDICT_INCORRECT, None):
        raise ValueError("Недопустимое решение")
    with _connect() as conn:
        row = conn.execute("SELECT result_id, auto_verdict FROM answer_results WHERE id = ?", (answer_id,)).fetchone()
        if not row:
            return None
        if verdict is None:
            conn.execute(
                "UPDATE answer_results SET final_verdict = NULL, reviewed_by = NULL, reviewed_at = NULL WHERE id = ?",
                (answer_id,),
            )
        else:
            conn.execute(
                "UPDATE answer_results SET final_verdict = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?",
                (verdict, reviewer, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), answer_id),
            )
        totals = recalculate_result(conn, row["result_id"])
        effective = verdict or row["auto_verdict"]
        return {
            "answer_id": answer_id,
            "result_id": row["result_id"],
            "verdict": effective,
            "verdict_label": VERDICT_LABELS[effective],
            "is_overridden": verdict is not None,
            **totals,
        }


def get_answer_results(result_id: int) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM answer_results WHERE result_id = ? ORDER BY question_index", (result_id,)
        ).fetchall()
    items = []
    for r in rows:
        verdict = r["final_verdict"] or r["auto_verdict"]
        reviewed_at = parse_datetime_db(r["reviewed_at"])
        items.append({
            "answer_id": r["id"],
            "index": r["question_index"] + 1,
            "question": r["question"] or f"Вопрос {r['question_index'] + 1}",
            "user_answer": (r["user_answer"] or "").strip(),
            "has_answer": bool((r["user_answer"] or "").strip()),
            "best_reference": r["best_reference"] or "",
            "score": float(r["score"] or 0.0),
            "score_display": f"{float(r['score'] or 0.0):.2f}",
            "auto_verdict": r["auto_verdict"],
            "auto_verdict_label": VERDICT_LABELS.get(r["auto_verdict"], r["auto_verdict"]),
            "verdict": verdict,
            "verdict_label": VERDICT_LABELS.get(verdict, verdict),
            "review_reason": r["review_reason"] or "",
            "is_overridden": r["final_verdict"] is not None,
            "reviewed_by": r["reviewed_by"] or "",
            "reviewed_at_display": format_datetime_ru(reviewed_at) if reviewed_at else "",
        })
    return items


def get_answer_filename(answer_id: int) -> Optional[str]:
    """Тест, к которому относится ответ (для проверки прав)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT r.filename FROM answer_results a JOIN test_results r ON r.id = a.result_id WHERE a.id = ?",
            (answer_id,),
        ).fetchone()
    return row[0] if row else None


def _allowed_filter(login: str) -> tuple:
    """SQL-условие «тест доступен преподавателю для проверки» и его параметры."""
    allowed = sorted(results_tests(login))
    return f"r.filename IN ({','.join('?' * len(allowed)) or 'NULL'})", allowed


def count_pending_reviews(login: str) -> int:
    condition, params = _allowed_filter(login)
    with _connect() as conn:
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM answer_results a JOIN test_results r ON r.id = a.result_id "
                f"WHERE a.auto_verdict = 'review' AND a.final_verdict IS NULL AND {condition}",
                params,
            ).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def get_review_queue(login: str, test_filter: str = "", limit: int = 200) -> Dict[str, Any]:
    """Ответы, ожидающие решения преподавателя, — сначала самые старые."""
    condition, params = _allowed_filter(login)
    params = list(params)
    where = f"a.auto_verdict = 'review' AND a.final_verdict IS NULL AND {condition}"
    if test_filter:
        where += " AND r.filename = ?"
        params.append(test_filter)
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT a.*, r.filename, r.login, r.attempt_number, r.finished_at,
                   u.last_name, u.first_name, u.middle_name, u.group_name
            FROM answer_results a
            JOIN test_results r ON r.id = a.result_id
            LEFT JOIN users u ON u.login = r.login
            WHERE {where}
            ORDER BY datetime(r.finished_at) ASC, a.result_id, a.question_index
            LIMIT ?
            """,
            params,
        ).fetchall()
        tests_condition, tests_params = _allowed_filter(login)
        tests = [row[0] for row in conn.execute(
            "SELECT DISTINCT r.filename FROM answer_results a JOIN test_results r ON r.id = a.result_id "
            f"WHERE a.auto_verdict = 'review' AND a.final_verdict IS NULL AND {tests_condition} ORDER BY r.filename",
            tests_params,
        ).fetchall()]
    items = []
    for r in rows:
        full_name = " ".join(filter(None, [r["last_name"], r["first_name"], r["middle_name"]])) or r["login"]
        finished = parse_datetime_db(r["finished_at"])
        items.append({
            "answer_id": r["id"],
            "result_id": r["result_id"],
            "test_name": (r["filename"] or "").rsplit(".", 1)[0],
            "full_name": full_name,
            "login": r["login"],
            "group_name": r["group_name"] or "",
            "attempt_number": r["attempt_number"],
            "finished_at_display": format_datetime_ru(finished) if finished else "",
            "index": r["question_index"] + 1,
            "question": r["question"] or "",
            "user_answer": r["user_answer"] or "",
            "best_reference": r["best_reference"] or "",
            "score_display": f"{float(r['score'] or 0.0):.2f}",
            "review_reason": r["review_reason"] or "",
            "verdict": "review",
            "verdict_label": VERDICT_LABELS["review"],
            "auto_verdict_label": VERDICT_LABELS["review"],
            "is_overridden": False,
        })
    return {"items": items, "tests": [t.rsplit(".", 1)[0] for t in tests], "test_files": tests}
