"""Прохождение теста: банк вопросов с эмбеддингами и состояние попытки каждого студента."""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import Request

from core.web import get_session_token
from services.ai_backend import ai_service
from services.formatting import parse_available_until
from services.test_files import parse_quoted_strings

# Состояние прохождения тестов: у каждой сессии (браузера) своё.
# Ключ — session_token, значение — словарь с вопросами, ответами студента и таймингом.
quiz_sessions: Dict[str, Dict[str, Any]] = {}


# Кэш банков вопросов: эмбеддинги эталонов считаются один раз на файл,
# а не на каждого студента. Пересчитываются, если файл изменился (mtime).
_quiz_bank_cache: Dict[str, Dict[str, Any]] = {}


def load_quiz_bank(file_path: str) -> Dict[str, Any]:
    """Возвращает вопросы, эталонные ответы и их эмбеддинги для файла теста (с кэшированием)."""
    mtime = os.path.getmtime(file_path)
    cached = _quiz_bank_cache.get(file_path)
    if cached and cached["mtime"] == mtime:
        return cached

    df = pd.read_excel(file_path, engine='openpyxl', usecols=[0, 1], header=None, names=['q', 'a'])
    df = df.dropna(subset=['q'])
    bank_questions = df['q'].astype(str).tolist()
    bank_references = [parse_quoted_strings(answers_str) for answers_str in df['a'].fillna("").astype(str)]
    bank_embeddings = [
        ai_service.encode(answers_list, convert_to_tensor=True) if answers_list else None
        for answers_list in bank_references
    ]

    bank = {
        "mtime": mtime,
        "questions": bank_questions,
        "reference_answers": bank_references,
        "embeddings": bank_embeddings,
    }
    _quiz_bank_cache[file_path] = bank
    return bank


def get_active_quiz(request: Request) -> Optional[Dict[str, Any]]:
    """Состояние теста, который проходит текущая сессия, или None."""
    session_token = get_session_token(request)
    if not session_token:
        return None
    return quiz_sessions.get(session_token)


def get_unanswered_indexes(quiz_state: Dict[str, Any]) -> List[int]:
    answers = quiz_state["user_answers"]
    return [i for i, answer in enumerate(answers) if not (answer or "").strip()]


def get_quiz_restriction_status(request: Request) -> Dict[str, Any]:
    session_token = get_session_token(request)
    if not session_token:
        return {"error": None, "remaining_seconds": None, "timed_out": False, "deadline_expired": False}

    quiz_state = quiz_sessions.get(session_token)
    if not quiz_state:
        return {"error": None, "remaining_seconds": None, "timed_out": False, "deadline_expired": False}

    now = datetime.now()
    timed_out = False
    deadline_expired = False
    remaining_seconds = None

    available_until_dt = parse_available_until(quiz_state.get("available_until"))
    if available_until_dt and now > available_until_dt:
        deadline_expired = True

    time_limit_minutes = quiz_state.get("time_limit_minutes")
    started_at = quiz_state.get("started_at")
    if time_limit_minutes and started_at:
        elapsed = (now - started_at).total_seconds()
        remaining_seconds = max(0, int(time_limit_minutes * 60 - elapsed))
        if elapsed >= time_limit_minutes * 60:
            timed_out = True

    if timed_out:
        return {
            "error": "Время прохождения теста истекло.",
            "remaining_seconds": 0,
            "timed_out": True,
            "deadline_expired": deadline_expired
        }

    if deadline_expired:
        return {
            "error": "Дедлайн выполнения теста уже прошел.",
            "remaining_seconds": remaining_seconds,
            "timed_out": timed_out,
            "deadline_expired": True
        }

    return {
        "error": None,
        "remaining_seconds": remaining_seconds,
        "timed_out": False,
        "deadline_expired": False
    }
