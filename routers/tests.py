"""Список тестов, загрузка, скачивание и удаление файлов, старт теста."""
import os
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from core.users_repo import get_user_full_info, get_user_permissions
from core.web import get_session_token, get_template_context, get_user_from_session, templates
from routers.common import render_select_with_error
from services.ai_backend import ai_service
from services.formatting import parse_available_until
from services.quiz import load_quiz_bank, quiz_sessions
from services.test_access import (
    can_edit_test,
    get_owner,
    get_test_access,
    is_manager,
    set_admin_shows_all,
    set_owner,
)
from services.test_files import (
    get_uploaded_file_path,
    get_uploaded_files,
    validate_uploaded_excel_filename,
)
from services.test_settings import (
    can_user_access_test,
    get_attempt_limit_status,
    get_test_settings,
    increment_user_attempts_count,
)

router = APIRouter()


@router.get("/select", response_class=HTMLResponse)
def select_file_page(request: Request):
    """Страница выбора файла с сервера"""
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)
    
    user_info = get_user_full_info(login)
    files = get_uploaded_files(user_info)
    
    context = get_template_context(request)
    context.update({
        "request": request,
        "files": files
    })
    
    return templates.TemplateResponse("select.html", context)


@router.post("/upload", response_class=HTMLResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Загрузка нового файла на сервер"""
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        return RedirectResponse(url="/", status_code=303)
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_upload_files']:
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "error": "У вас нет прав для загрузки файлов"
        })
        return templates.TemplateResponse("select.html", context)
    
    try:
        safe_filename = validate_uploaded_excel_filename(file.filename)

        # Сохраняем файл в папку uploaded_files
        file_path = get_uploaded_file_path(safe_filename)
        if os.path.exists(file_path) and not can_edit_test(user_info, safe_filename):
            raise HTTPException(status_code=409, detail=f"Тест «{safe_filename}» уже существует. Переименуйте файл.")
        with open(file_path, "wb") as f:
            f.write(await file.read())
        if not get_owner(safe_filename):
            set_owner(safe_filename, user_login)
        
        # Возвращаем на страницу выбора файлов с сообщением об успехе
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "message": f"Файл {safe_filename} успешно загружен!"
        })
        return templates.TemplateResponse("select.html", context)
        
    except HTTPException as exc:
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "error": str(exc.detail)
        })
        return templates.TemplateResponse("select.html", context)

    except Exception as e:
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "error": f"Ошибка загрузки файла: {str(e)}"
        })
        return templates.TemplateResponse("select.html", context)


async def load_quiz_data(request: Request, file_path: str):
    """Загружает данные викторины из файла и начинает тест"""
    try:
        user_login = get_user_from_session(request)
        user_info = get_user_full_info(user_login) if user_login else None

        filename = os.path.basename(file_path)
        if is_manager(user_info) and not can_edit_test(user_info, filename):
            return render_select_with_error(request, "Тест не найден.")
        settings = get_test_settings(filename)
        available_until_dt = parse_available_until(settings.get("available_until"))
        if available_until_dt and datetime.now() > available_until_dt:
            return render_select_with_error(request, "Нельзя начать тест: дедлайн выполнения уже прошел.")

        has_access, access_reason = can_user_access_test(settings, user_info)
        if not has_access:
            return render_select_with_error(request, access_reason)

        attempt_limit = get_attempt_limit_status(settings, user_info.get("login") if user_info else None, filename)
        if attempt_limit["limit_reached"]:
            return render_select_with_error(
                request,
                f"Нельзя начать тест: лимит попыток исчерпан ({attempt_limit['attempts_used']} из {attempt_limit['max_attempts']})."
            )

        if ai_service is None or not ai_service.is_ready():
            # Модель не загружена (например, нет интернета для HuggingFace) — даём понятную ошибку пользователю
            files = get_uploaded_files(user_info)
            context = get_template_context(request)
            context.update({
                "request": request,
                "files": files,
                "error": "Модель проверки ответов не загружена (нет доступа к HuggingFace). "
                         "Обратитесь к администратору: требуется интернет или локальный кэш модели."
            })
            return templates.TemplateResponse("select.html", context)

        bank = load_quiz_bank(file_path)
        if not bank["questions"]:
            return render_select_with_error(request, "В файле теста нет вопросов.")

        session_token = get_session_token(request)
        if not session_token:
            return RedirectResponse(url="/", status_code=303)

        attempt_number = increment_user_attempts_count(user_info.get("login") if user_info else None, filename)

        quiz_sessions[session_token] = {
            "filename": filename,
            # Ссылка на банк фиксирует набор вопросов на время попытки,
            # даже если преподаватель отредактирует файл посреди теста.
            "bank": bank,
            "questions": bank["questions"],
            "reference_answers": bank["reference_answers"],
            "user_answers": [""] * len(bank["questions"]),
            "started_at": datetime.now(),
            "time_limit_minutes": settings.get("time_limit_minutes"),
            "available_until": settings.get("available_until"),
            "max_attempts": settings.get("max_attempts"),
            "attempt_number": attempt_number,
            "result_saved": False
        }
        
        # Перенаправляем на первый вопрос
        return RedirectResponse(url="/quiz?idx=0", status_code=303)
        
    except Exception as e:
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "error": f"Ошибка загрузки теста: {str(e)}"
        })
        return templates.TemplateResponse("select.html", context)


@router.post("/select", response_class=HTMLResponse)
async def select_existing_file(request: Request, filename: str = Form(...)):
    """Выбор существующего файла с сервера и начало теста"""
    # Проверяем авторизацию
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    user_info = get_user_full_info(user)
    
    file_path = get_uploaded_file_path(filename)
    if not os.path.exists(file_path):
        files = get_uploaded_files(user_info)
        context = get_template_context(request)
        context.update({
            "request": request,
            "files": files,
            "error": f"Файл {filename} не найден на сервере"
        })
        return templates.TemplateResponse("select.html", context)
    
    # Загружаем данные и начинаем тест
    return await load_quiz_data(request, file_path)


@router.get("/download/{filename}")
async def download_uploaded_file(request: Request, filename: str):
    """Скачивание Excel-файла теста"""
    user_login = get_user_from_session(request)
    if not user_login:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])

    if not user_permissions['can_edit_tests']:
        raise HTTPException(status_code=403, detail="У вас нет прав для скачивания файлов")

    safe_filename = validate_uploaded_excel_filename(filename)
    file_path = get_uploaded_file_path(safe_filename)
    # Чужой тест для преподавателя «не существует», чтобы не раскрывать названия
    if not os.path.exists(file_path) or not can_edit_test(user_info, safe_filename):
        raise HTTPException(status_code=404, detail="Файл не найден")

    return FileResponse(
        path=file_path,
        filename=safe_filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@router.post("/delete_file")
async def delete_file(request: Request, filename: str = Form(...)):
    """Удаление файла с сервера"""
    user_login = get_user_from_session(request)
    if not user_login:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_delete_files']:
        raise HTTPException(status_code=403, detail="У вас нет прав для удаления файлов")
    
    try:
        safe_filename = validate_uploaded_excel_filename(filename)
        file_path = get_uploaded_file_path(safe_filename)
        access = get_test_access(user_info, safe_filename)
        if not access["edit"]:
            return JSONResponse({"status": "error", "message": "Файл не найден"})
        if not access["manage"]:
            return JSONResponse({"status": "error", "message": "Удалить тест может только его владелец"}, status_code=403)
        if os.path.exists(file_path):
            os.remove(file_path)
            return JSONResponse({"status": "success", "message": f"Файл {safe_filename} удален"})
        else:
            return JSONResponse({"status": "error", "message": "Файл не найден"})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@router.get("/app")
def redirect_to_editor(request: Request):
    """Перенаправление на редактор тестов"""
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    
    user_info = get_user_full_info(user)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        return RedirectResponse(url="/select", status_code=303)
    
    return RedirectResponse(url="/editor", status_code=307)


@router.get("/scope")
def set_admin_scope(request: Request, show: str = "mine"):
    """Переключатель администратора «Мои тесты / Все тесты» (влияет на списки)."""
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)
    user_info = get_user_full_info(login)
    if user_info and user_info.get("user_type") == "admin":
        set_admin_shows_all(login, show == "all")
    # Возвращаемся на ту же страницу (только адреса этого сайта)
    referer = urlparse(request.headers.get("referer", ""))
    target = referer.path or "/select"
    if not target.startswith("/") or target.startswith("//") or target.startswith("/scope"):
        target = "/select"
    if referer.query:
        target += "?" + referer.query
    return RedirectResponse(url=target, status_code=303)
