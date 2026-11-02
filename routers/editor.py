"""Редактор тестов и настроек доступа (/editor). Для преподавателей и администраторов."""
import json
import os

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

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
    parse_scoring_settings,
    rename_test_records,
    set_scoring_settings,
    upsert_test_settings,
)

router = APIRouter(prefix="/editor")


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        return RedirectResponse(url="/", status_code=303)  # Redirect to main app login
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        return RedirectResponse(url="/select", status_code=303)
    
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
            "source": "import",
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
        return RedirectResponse(url="/", status_code=303)  # Redirect to main app login
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
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
        "source": "new",
        "test_settings": test_settings
    }
    context.update(build_access_context(test_settings))
    return templates.TemplateResponse("edit.html", context)


@router.get("/edit/{filename}")
async def edit(filename: str, request: Request):
    user_login = get_user_from_session(request)
    if not user_login:
        return RedirectResponse(url="/", status_code=303)

    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        return RedirectResponse(url="/select", status_code=303)
    
    # Путь к директории с файлами
    safe_filename = normalize_excel_filename(filename)
    file_path = get_saved_file_path(safe_filename)
    
    if not file_path.exists():
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
        
        test_settings = get_test_settings_for_form(safe_filename)
        context = {
            "request": request,
            "filename": safe_filename,
            "questions": questions_data,
            "original_data": "excel_file",
            "source": "published",
            "user_info": user_info,
            "user_permissions": user_permissions,
            "test_settings": test_settings
        }
        context.update(build_access_context(test_settings))
        return templates.TemplateResponse("edit.html", context)
        
    except Exception as e:
        return JSONResponse(
            content={"error": f"Ошибка чтения файла: {e}"},
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
    test_name: str = Form(""),
    source: str = Form(""),
    threshold: str = Form(""),
    review_margin: str = Form("")
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
    
    try:
        scoring_settings = parse_scoring_settings(threshold, review_margin)
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)

    # Определяем, откуда открыт тест:
    #   new       — создаётся с нуля (служебное имя new_file.xlsx);
    #   import    — загружен из Excel в редактор, лежит в uploaded_files_imports;
    #   published — уже есть в списке тестов (uploaded_files).
    if filename == NEW_TEST_PLACEHOLDER:
        source = "new"
    else:
        original_filename = normalize_excel_filename(filename)
        if source not in ("import", "published"):
            source = "published" if get_saved_file_path(original_filename).exists() else "import"
        source_exists = (get_saved_file_path(original_filename) if source == "published"
                         else get_imported_file_path(original_filename)).exists()
        if not source_exists:
            return JSONResponse(content={"error": "Редактируемый тест не найден"}, status_code=404)

    # Имя файла берётся из поля «Название теста». Пустое поле у существующего
    # теста означает «оставить прежнее название».
    try:
        if source == "new" or test_name.strip():
            safe_filename = make_test_filename(test_name)
        else:
            safe_filename = make_test_filename(original_filename.rsplit(".", 1)[0])
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)

    # Нельзя затереть другой опубликованный тест. Исключения: сохранение теста
    # под его же именем и повторный импорт файла с тем же именем (обновление теста).
    # casefold: в Windows имена файлов не различают регистр («тест» и «Тест» — один файл)
    same_as_original = source != "new" and (
        safe_filename.casefold() == make_test_filename(original_filename.rsplit(".", 1)[0]).casefold()
    )
    if get_saved_file_path(safe_filename).exists() and not same_as_original:
        return JSONResponse(
            content={"error": f"Тест «{safe_filename}» уже существует. Выберите другое название."},
            status_code=409
        )
    renamed_from = original_filename if source == "published" and safe_filename != original_filename else None
    file_path = get_saved_file_path(safe_filename)

    try:
        # Используем нашу утилиту для сохранения без заголовков
        if renamed_from:
            # Переименование: записываем в старый файл и переименовываем его
            # (так корректно работает и смена только регистра букв в Windows),
            # затем переносим настройки, попытки и историю результатов
            old_path = get_saved_file_path(renamed_from)
            save_excel_file(str(old_path), data)
            os.replace(old_path, file_path)
            rename_test_records(renamed_from, safe_filename)
        else:
            save_excel_file(str(file_path), data)
        upsert_test_settings(safe_filename, time_limit_minutes, max_attempts, available_until, allowed_students, allowed_groups)
        set_scoring_settings(safe_filename, scoring_settings["threshold"], scoring_settings["review_margin"])

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
