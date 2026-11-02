"""Редактор тестов и настроек доступа (/editor). Для преподавателей и администраторов."""
import json

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from core.users_repo import get_user_full_info, get_user_permissions
from core.web import get_user_from_session, templates
from services.test_files import (
    NEW_TEST_PLACEHOLDER,
    get_imported_file_path,
    get_saved_file_path,
    make_test_filename,
    normalize_excel_filename,
    parse_answers,
    process_excel_file,
    save_excel_file,
)
from services.test_settings import (
    build_access_context,
    get_test_settings_for_form,
    upsert_test_settings,
)

router = APIRouter(prefix="/editor")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=303)  # Redirect to main app login
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/select", status_code=303)
    
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/editor/create-new", status_code=303)
    
    return templates.TemplateResponse("upload.html", {
        "request": request,
        "user_info": user_info,
        "user_permissions": user_permissions
    })


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        raise HTTPException(status_code=403, detail="У вас нет прав для редактирования тестов")
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Только Excel файлы разрешены")
    
    # Сохраняем файл
    safe_filename = normalize_excel_filename(file.filename)
    file_path = get_imported_file_path(safe_filename)
    with open(file_path, "wb") as f:
        f.write(await file.read())
    
    # Обрабатываем файл
    try:
        original_data = process_excel_file(str(file_path))
        questions_data = []
        
        # Обрабатываем все строки как данные (без заголовков)
        for index, row in enumerate(original_data):
            if row and len(row) >= 2 and pd.notna(row[0]) and str(row[0]).strip():
                question = str(row[0])
                answers = parse_answers(row[1]) if pd.notna(row[1]) else [""]
                questions_data.append({
                    "index": index,
                    "question": question,
                    "answers": answers
                })
        
        test_settings = get_test_settings_for_form(safe_filename)
        context = {
            "request": request,
            "filename": safe_filename,
            "questions": questions_data,
            "user_info": user_info,
            "user_permissions": user_permissions,
            "original_data": json.dumps(original_data, ensure_ascii=False) if original_data else "[]",
            "test_settings": test_settings
        }
        context.update(build_access_context(test_settings))
        return templates.TemplateResponse("edit.html", context)
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки файла: {str(e)}")


@router.get("/download/{filename}")
async def download_file(filename: str, request: Request):
    if request:
        # Проверяем авторизацию
        user_login = get_user_from_session(request)
        if not user_login:
            raise HTTPException(status_code=401, detail="Требуется авторизация")
        
        # Проверяем права доступа
        user_info = get_user_full_info(user_login)
        user_permissions = get_user_permissions(user_info['user_type'])
        
        if not user_permissions['can_edit_tests']:
            raise HTTPException(status_code=403, detail="У вас нет прав для скачивания файлов")
    
    safe_filename = normalize_excel_filename(filename)
    file_path = get_saved_file_path(safe_filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    
    return FileResponse(
        path=str(file_path),
        filename=safe_filename,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@router.get("/create-new")
async def create_new(request: Request):
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=303)  # Redirect to main app login
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/select", status_code=303)
    
    test_settings = {
        "time_limit_minutes": "",
        "available_until": "",
        "allowed_students": "",
        "allowed_groups": "",
        "max_attempts": ""
    }
    context = {
        "request": request,
        "filename": "new_file.xlsx",
        "questions": [{"index": 0, "question": "", "answers": [""]}],
        "user_info": user_info,
        "user_permissions": user_permissions,
        "original_data": "[]",
        "test_settings": test_settings
    }
    context.update(build_access_context(test_settings))
    return templates.TemplateResponse("edit.html", context)


@router.get("/edit/{filename}")
async def edit(filename: str, request: Request):
    user_login = get_user_from_session(request)
    if not user_login:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/", status_code=303)

    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/select", status_code=303)
    
    # Путь к директории с файлами
    safe_filename = normalize_excel_filename(filename)
    file_path = get_saved_file_path(safe_filename)
    
    if not file_path.exists():
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"error": f"Файл {filename} не найден на сервере"},
            status_code=404
        )
    
    # Читаем Excel файл
    try:
        # Читаем Excel файл
        df = pd.read_excel(file_path, header=None)
        
        # Преобразуем данные в нужный формат
        questions_data = []
        
        for index, row in df.iterrows():
            question_text = str(row.iloc[0]) if len(row) > 0 and pd.notna(row.iloc[0]) else ""
            
            # Обрабатываем ответы (второй столбец)
            answers = []
            if len(row) > 1 and pd.notna(row.iloc[1]):
                answers_str = str(row.iloc[1])
                try:
                    # Используем функцию из utils
                    answers = parse_answers(answers_str)
                except:
                    # Если не получается, разбиваем по запятым
                    answers = [a.strip().strip('"').strip("'") for a in answers_str.split(',') if a.strip()]
            
            questions_data.append({
                "question": question_text,
                "answers": answers
            })
        
        # Рендерим шаблон
        from fastapi.templating import Jinja2Templates
        templates = Jinja2Templates(directory="templates")
        
        test_settings = get_test_settings_for_form(safe_filename)
        context = {
            "request": request,
            "filename": safe_filename,
            "questions": questions_data,
            "original_data": "excel_file",
            "user_info": user_info,
            "user_permissions": user_permissions,
            "test_settings": test_settings
        }
        context.update(build_access_context(test_settings))
        return templates.TemplateResponse("edit.html", context)
        
    except Exception as e:
        import traceback
        return JSONResponse(
            content={"error": f"Ошибка чтения файла: {str(e)}\n{traceback.format_exc()}"},
            status_code=500
        )


@router.post("/save_edit")
async def save_edit(
    request: Request,
    filename: str = Form(...),
    questions: list[str] = Form(...),
    answers: list[str] = Form(...),
    time_limit_minutes: str = Form(""),
    max_attempts: str = Form(""),
    available_until: str = Form(""),
    allowed_students: str = Form(""),
    allowed_groups: str = Form(""),
    test_name: str = Form("")
):
    user_login = get_user_from_session(request)
    if not user_login:
        return JSONResponse(
            content={"error": "Не авторизован"},
            status_code=401
        )
    
    # Проверяем права
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        return JSONResponse(
            content={"error": "Нет прав на редактирование"},
            status_code=403
        )
    
    # Формируем DataFrame для Excel
    data = []
    for i, question in enumerate(questions):
        if i < len(answers):
            try:
                # Парсим JSON массив ответов
                answers_list = json.loads(answers[i])
                # Форматируем ответы в строку с кавычками
                answers_str = ','.join([f'"{answer}"' for answer in answers_list])
            except:
                answers_str = answers[i]
        else:
            answers_str = ""
        
        data.append([question, answers_str])
    
    # Новый тест сохраняется под названием, которое ввёл преподаватель.
    # Раньше все новые тесты записывались в new_file.xlsx и затирали друг друга.
    if filename == NEW_TEST_PLACEHOLDER:
        try:
            safe_filename = make_test_filename(test_name)
        except ValueError as e:
            return JSONResponse(content={"error": str(e)}, status_code=400)
        if get_saved_file_path(safe_filename).exists():
            return JSONResponse(
                content={"error": f"Тест «{safe_filename}» уже существует. Выберите другое название."},
                status_code=409
            )
    else:
        safe_filename = normalize_excel_filename(filename)
        # Сохранять можно опубликованный тест или файл, только что импортированный
        # в редактор (он лежит в uploaded_files_imports и публикуется при сохранении)
        is_published = get_saved_file_path(safe_filename).exists()
        is_imported = get_imported_file_path(safe_filename).exists()
        if not (is_published or is_imported):
            return JSONResponse(content={"error": "Редактируемый тест не найден"}, status_code=404)
        if safe_filename.lower().endswith(".xls"):
            # Сохраняем всегда в .xlsx: список тестов показывает только такие файлы
            safe_filename = safe_filename[:-4] + ".xlsx"
    file_path = get_saved_file_path(safe_filename)

    try:
        # Используем нашу утилиту для сохранения без заголовков
        save_excel_file(str(file_path), data)
        upsert_test_settings(safe_filename, time_limit_minutes, max_attempts, available_until, allowed_students, allowed_groups)

        # Возвращаем JSON ответ для асинхронного запроса
        return JSONResponse(
            content={
                "success": True,
                "message": f"Тест «{safe_filename}» сохранён",
                "filename": safe_filename
            }
        )
    except Exception as e:
        return JSONResponse(content={"error": f"Ошибка сохранения: {e}"}, status_code=500)
