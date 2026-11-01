"""Редактор тестов и настроек доступа (/editor). Для преподавателей и администраторов."""
import json

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from core.users_repo import get_user_full_info, get_user_permissions
from core.web import get_user_from_session, templates
from services.test_files import (
    create_new_excel_file,
    format_answers,
    get_imported_file_path,
    get_saved_file_path,
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


@router.post("/save")
async def save_file(
    request: Request,
    filename: str = Form(...),
    original_data: str = Form("[]"),
    questions: list[str] = Form(...),
    answers: list[str] = Form(...)
):
    # Проверяем авторизацию
    user_login = get_user_from_session(request)
    if not user_login:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    # Проверяем права доступа
    user_info = get_user_full_info(user_login)
    user_permissions = get_user_permissions(user_info['user_type'])
    
    if not user_permissions['can_edit_tests']:
        raise HTTPException(status_code=403, detail="У вас нет прав для редактирования тестов")
    
    try:
        # Определяем, это новый файл или редактирование существующего
        is_new_file = filename == "new_file.xlsx" or not original_data or original_data == "[]"
        
        if is_new_file:
            # СОЗДАНИЕ НОВОГО ФАЙЛА - БЕЗ ЗАГОЛОВКОВ
            output_filename = "new_questions.xlsx"
            output_path = f"uploaded_files/{output_filename}"
            
            # Подготавливаем данные для нового файла
            new_data = []
            for i in range(len(questions)):
                question_text = questions[i]
                # Парсим ответы из формы и правильно форматируем
                answers_list = [answer.strip() for answer in answers[i].split('","') if answer.strip()]
                # Убираем лишние кавычки
                clean_answers = [answer.strip('"') for answer in answers_list if answer.strip('"')]
                formatted_answers = format_answers(clean_answers)
                
                new_data.append({
                    "question": question_text,
                    "answers": formatted_answers
                })
            
            # Создаем новый файл БЕЗ заголовков
            create_new_excel_file(output_path, new_data)
            
            # Подготавливаем данные для отображения
            display_data = []
            for item in new_data:
                display_data.append({
                    "Question": item["question"],
                    "Answers": item["answers"]
                })
            
        else:
            # РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕГО ФАЙЛА
            # Полностью заменяем содержимое файла с тем же именем
            output_filename = filename
            output_path = f"uploaded_files/{output_filename}"
            
            # Создаем новые данные
            new_data = []
            for i in range(len(questions)):
                question_text = questions[i]
                # Парсим ответы из формы и правильно форматируем
                answers_list = [answer.strip() for answer in answers[i].split('","') if answer.strip()]
                # Убираем лишние кавычки
                clean_answers = [answer.strip('"') for answer in answers_list if answer.strip('"')]
                formatted_answers = format_answers(clean_answers)
                
                new_data.append([question_text, formatted_answers])
            
            # Сохраняем полностью новый файл (не копируем старый)
            save_excel_file(output_path, new_data)
            
            # Подготавливаем данные для отображения
            display_data = []
            for row in new_data:
                display_data.append({
                    "Question": row[0] if len(row) > 0 else "",
                    "Answers": row[1] if len(row) > 1 else ""
                })
        
        return RedirectResponse(url="/select", status_code=303)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения: {str(e)}")


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
    allowed_groups: str = Form("")
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
    
    # Сохраняем в Excel файл в правильной директории
    safe_filename = normalize_excel_filename(filename)
    file_path = get_saved_file_path(safe_filename)

    try:
        # Используем нашу утилиту для сохранения без заголовков
        save_excel_file(str(file_path), data)
        upsert_test_settings(safe_filename, time_limit_minutes, max_attempts, available_until, allowed_students, allowed_groups)

        # Возвращаем JSON ответ для асинхронного запроса
        return JSONResponse(
            content={
                "success": True,
                "message": f"Файл {filename} успешно сохранен",
                "filename": safe_filename
            }
        )
    except Exception as e:
        import traceback
        return JSONResponse(
            content={"error": f"Ошибка сохранения: {str(e)}\n{traceback.format_exc()}"},
            status_code=500
        )
