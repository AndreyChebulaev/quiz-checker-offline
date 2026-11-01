"""Вход, выход и смена пароля."""
import sqlite3
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import DATABASE_PATH
from core.security import hash_password, verify_password
from core.sessions import SESSION_LIFETIME, create_session, remove_session, remove_user_sessions
from core.users_repo import get_user_by_login
from core.web import get_template_context, get_user_from_session, templates
from services.quiz import quiz_sessions

router = APIRouter()


# Защита от перебора паролей: не более MAX_LOGIN_FAILURES неудачных
# попыток на один логин за LOGIN_LOCK_SECONDS секунд.
MAX_LOGIN_FAILURES = 5


LOGIN_LOCK_SECONDS = 5 * 60


_login_failures: Dict[str, List[float]] = {}


def _recent_failures(login: str) -> List[float]:
    now = time.time()
    attempts = [t for t in _login_failures.get(login, []) if now - t < LOGIN_LOCK_SECONDS]
    _login_failures[login] = attempts
    return attempts


@router.get("/", response_class=HTMLResponse)
def login_page(request: Request):
    """Страница входа"""
    # Если пользователь уже авторизован, перенаправляем на выбор файла
    if get_user_from_session(request):
        return RedirectResponse(url="/select", status_code=303)
    
    context = get_template_context(request)
    context["request"] = request
    return templates.TemplateResponse("login.html", context)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),  # Изменили на username
    password: str = Form(...)
):
    """Обработка входа"""
    def login_error(message: str):
        context = get_template_context(request)
        context.update({"request": request, "error": message})
        return templates.TemplateResponse("login.html", context)

    try:
        username = username.strip()
        if len(_recent_failures(username)) >= MAX_LOGIN_FAILURES:
            return login_error("Слишком много неудачных попыток. Попробуйте через 5 минут.")

        user = get_user_by_login(username)
        # Одинаковое сообщение для «нет логина» и «неверный пароль»,
        # чтобы нельзя было подбирать существующие логины
        if not user or not verify_password(password, user["password"]):
            _login_failures.setdefault(username, []).append(time.time())
            return login_error("Неверный логин или пароль")

        _login_failures.pop(username, None)
        session_token = create_session(username)
        response = RedirectResponse(url="/select", status_code=303)
        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,          # недоступна из JavaScript
            samesite="lax",         # не отправляется с чужих сайтов при POST
            max_age=int(SESSION_LIFETIME.total_seconds()),
        )
        return response
        
    except Exception as e:
        context = get_template_context(request)
        context.update({
            "request": request,
            "error": f"Ошибка входа: {str(e)}"
        })
        return templates.TemplateResponse("login.html", context)


MIN_PASSWORD_LENGTH = 8


@router.get("/account/password", response_class=HTMLResponse)
def change_password_page(request: Request, changed: int = 0):
    if not get_user_from_session(request):
        return RedirectResponse(url="/", status_code=303)
    context = get_template_context(request)
    context["request"] = request
    if changed:
        context["message"] = "Пароль изменён. На других устройствах потребуется войти заново."
    return templates.TemplateResponse("change_password.html", context)


@router.post("/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    """Смена собственного пароля любым пользователем."""
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)

    def page(error: Optional[str] = None, message: Optional[str] = None):
        context = get_template_context(request)
        context.update({"request": request, "error": error, "message": message})
        return templates.TemplateResponse("change_password.html", context)

    user = get_user_by_login(login)
    if not user or not verify_password(current_password, user["password"]):
        return page(error="Текущий пароль указан неверно")
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return page(error=f"Новый пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
    if new_password != confirm_password:
        return page(error="Новые пароли не совпадают")
    if new_password == current_password:
        return page(error="Новый пароль совпадает с текущим")

    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("UPDATE users SET password = ? WHERE login = ?", (hash_password(new_password), login))

    # Завершаем все сессии пользователя и сразу выдаём новую для текущего браузера
    remove_user_sessions(login)
    response = RedirectResponse(url="/account/password?changed=1", status_code=303)
    response.set_cookie(
        key="session_token",
        value=create_session(login),
        httponly=True,
        samesite="lax",
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )
    return response


@router.get("/logout")
def logout(request: Request):
    """Выход из системы: сессия удаляется и на сервере, и в браузере"""
    session_token = request.cookies.get("session_token")
    remove_session(session_token)
    quiz_sessions.pop(session_token, None)
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_token")
    return response
