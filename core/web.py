"""Общее для веб-слоя: шаблоны, текущий пользователь, контекст шаблонов."""
from typing import Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import REVIEW_MARGIN, TEMPLATES_DIR
from core.sessions import get_user_from_session as get_session_user
from core.users_repo import get_user_full_info, get_user_permissions
from services.ai_backend import THRESHOLD, ai_service, ai_status, build_local_ai_help_text
from services.review import count_pending_reviews

templates = Jinja2Templates(directory=TEMPLATES_DIR)
# Отключаем кэш шаблонов для совместимости с Jinja2 3.1.6+ и Starlette
templates.env.cache_size = 0
templates.env.auto_reload = False
# Значения по умолчанию для подсказок в форме настроек теста
templates.env.globals["default_threshold"] = THRESHOLD
templates.env.globals["default_review_margin"] = REVIEW_MARGIN


def get_session_token(request: Request) -> Optional[str]:
    return request.cookies.get("session_token")


def get_user_from_session(request: Request) -> Optional[str]:
    """Получает пользователя из сессии"""
    return get_session_user(request.cookies.get("session_token"))


class RedirectRequired(Exception):
    """Исключение для зависимостей FastAPI: прервать обработку и перенаправить."""

    def __init__(self, url: str):
        self.url = url


def get_template_context(request: Request):
    """Возвращает контекст для шаблонов с информацией о пользователе"""
    login = get_user_from_session(request)
    if login:
        user_info = get_user_full_info(login)
    if login and user_info:
        user_permissions = get_user_permissions(user_info['user_type'])
        return {
            "user_info": user_info,
            "user_permissions": user_permissions,
            "pending_review_count": count_pending_reviews() if user_permissions.get("can_edit_tests") else 0,
            "ai_ready": ai_service is not None and ai_service.is_ready(),
            "ai_model_source": ai_service.model_source if ai_service is not None else None,
            "ai_help_text": build_local_ai_help_text(ai_service.status() if ai_service is not None else ai_status)
        }
    return {
        "user_info": None,
        "user_permissions": None,
        "ai_ready": ai_service is not None and ai_service.is_ready(),
        "ai_model_source": ai_service.model_source if ai_service is not None else None,
        "ai_help_text": build_local_ai_help_text(ai_service.status() if ai_service is not None else ai_status)
    }
