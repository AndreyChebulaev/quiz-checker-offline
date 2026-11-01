"""Вспомогательные функции, общие для нескольких разделов."""
from fastapi import Request

from core.users_repo import get_user_full_info
from core.web import get_template_context, get_user_from_session, templates
from services.test_files import get_uploaded_files


def render_select_with_error(request: Request, error_message: str):
    login = get_user_from_session(request)
    user_info = get_user_full_info(login) if login else None
    files = get_uploaded_files(user_info)
    context = get_template_context(request)
    context.update({
        "request": request,
        "files": files,
        "error": error_message
    })
    return templates.TemplateResponse("select.html", context)
