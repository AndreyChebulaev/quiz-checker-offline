"""Управление пользователями (/v2). Доступно только администратору."""
from io import BytesIO, StringIO

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from core.database import get_db_connection
from core.security import generate_password, hash_password
from core.sessions import remove_user_sessions, rename_user_sessions
from core.users_repo import get_user_by_login
from core.web import RedirectRequired, get_template_context, get_user_from_session, templates
from services.user_management import UserUpdate, generate_login, save_user_to_db, user_exists


def require_admin(request: Request) -> str:
    """Зависимость: пропускает только администратора."""
    login = get_user_from_session(request)
    if not login:
        raise RedirectRequired("/")
    user = get_user_by_login(login)
    if not user or user["user_type"] != "admin":
        if request.method == "GET":
            raise RedirectRequired("/select")
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return login


router = APIRouter(prefix="/v2", dependencies=[Depends(require_admin)])


ALLOWED_NEW_USER_TYPES = ("student", "teacher")


def render_credentials(request: Request, credentials: list, title: str):
    """Показывает логины и пароли один раз: в БД хранятся только хеши."""
    context = get_template_context(request)
    context.update({"request": request, "credentials": credentials, "title": title})
    return templates.TemplateResponse("v2/credentials.html", context)


@router.get("/")
async def home(request: Request):
    context = get_template_context(request)
    context["request"] = request
    return templates.TemplateResponse("v2/index.html", context)


@router.get("/register")
async def show_registration_form(request: Request):
    context = get_template_context(request)
    context["request"] = request
    return templates.TemplateResponse("v2/register.html", context)


@router.post("/register")
async def register_user(
    request: Request,
    user_type: str = Form(...),
    last_name: str = Form(...),
    first_name: str = Form(...),
    middle_name: str = Form(None),
    group_name: str = Form(None)
):
    if user_type not in ALLOWED_NEW_USER_TYPES:
        context = get_template_context(request)
        context.update({"request": request, "error": "Недопустимый тип пользователя"})
        return templates.TemplateResponse("v2/register.html", context)
    last_name, first_name = last_name.strip(), first_name.strip()
    middle_name = (middle_name or "").strip() or None
    group_name = (group_name or "").strip() or None

    # Генерация логина и пароля
    login = generate_login(last_name, first_name, middle_name)
    password = generate_password()
    
    user_data = {
        'user_type': user_type,
        'last_name': last_name,
        'first_name': first_name,
        'middle_name': middle_name,
        'group_name': group_name,
        'login': login,
        'password': password
    }
    
    # Проверяем, существует ли пользователь
    if user_exists(last_name, first_name, middle_name, user_type):
        context = get_template_context(request)
        context.update({
            "request": request,
            "error": f"Пользователь {last_name} {first_name} {middle_name or ''} уже существует в системе"
        })
        return templates.TemplateResponse("v2/register.html", context)
    
    # Сохраняем пользователя
    result = save_user_to_db(user_data)

    return render_credentials(request, [{
        "full_name": f"{last_name} {first_name} {middle_name or ''}".strip(),
        "group_name": group_name or "",
        "login": result["login"],
        "password": result["password"],
    }], "Пользователь создан")


@router.get("/upload")
async def show_upload_form(request: Request):
    context = get_template_context(request)
    context["request"] = request
    return templates.TemplateResponse("v2/upload.html", context)


@router.post("/upload")
async def upload_users_file(request: Request, file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Файл не выбран")
    
    # Проверяем расширение файла
    if not file.filename.lower().endswith(('.csv', '.xlsx', '.xls')):
        raise HTTPException(status_code=400, detail="Поддерживаются только CSV и Excel файлы")
    
    content = await file.read()
    
    try:
        if file.filename.lower().endswith('.csv'):
            # Обработка CSV файла
            csv_content = content.decode('utf-8')
            df = pd.read_csv(StringIO(csv_content))
        else:
            # Обработка Excel файла
            df = pd.read_excel(BytesIO(content))
        
        # Проверяем необходимые колонки
        required_columns = ['last_name', 'first_name', 'user_type']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise HTTPException(
                status_code=400, 
                detail=f"Отсутствуют обязательные колонки: {', '.join(missing_columns)}"
            )
        
        results = {
            'successful': 0,
            'failed': 0,
            'exists': 0,
            'errors': [],
            'existing_users': [],
            'credentials': []
        }
        
        # Обрабатываем каждую строку
        for index, row in df.iterrows():
            try:
                # Подготовка данных
                user_data = {
                    'user_type': str(row['user_type']).strip().lower(),
                    'last_name': str(row['last_name']).strip(),
                    'first_name': str(row['first_name']).strip(),
                    'middle_name': str(row['middle_name']).strip() if 'middle_name' in df.columns and pd.notna(row.get('middle_name')) else None,
                    'group_name': str(row['group_name']).strip() if 'group_name' in df.columns and pd.notna(row.get('group_name')) else None
                }
                
                # Проверка типа пользователя
                if user_data['user_type'] not in ['teacher', 'student']:
                    raise ValueError("Тип пользователя должен быть 'teacher' или 'student'")
                
                # Проверка обязательных полей
                if not user_data['last_name'] or not user_data['first_name']:
                    raise ValueError("Фамилия и имя обязательны для заполнения")
                
                # Для студентов проверяем группу
                if user_data['user_type'] == 'student' and not user_data['group_name']:
                    raise ValueError("Для студента обязательно указание группы")
                
                # Проверяем, существует ли пользователь
                if user_exists(
                    user_data['last_name'],
                    user_data['first_name'],
                    user_data['middle_name'],
                    user_data['user_type']
                ):
                    results['exists'] += 1
                    results['existing_users'].append(
                        f"{user_data['last_name']} {user_data['first_name']} {user_data['middle_name'] or ''} - уже существует"
                    )
                    continue
                
                # Генерация логина и пароля
                user_data['login'] = generate_login(
                    user_data['last_name'], 
                    user_data['first_name'], 
                    user_data['middle_name']
                )
                user_data['password'] = generate_password()
                
                # Сохранение в базу
                result = save_user_to_db(user_data)
                
                if not result.get('exists'):
                    results['successful'] += 1
                    results['credentials'].append({
                        "full_name": f"{user_data['last_name']} {user_data['first_name']} {user_data['middle_name'] or ''}".strip(),
                        "group_name": user_data['group_name'] or "",
                        "login": result["login"],
                        "password": result["password"],
                    })
                
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Строка {index + 2}: {str(e)}")
        
        context = get_template_context(request)
        context.update({
            "request": request,
            "results": results,
            "total_processed": len(df)
        })
        
        return templates.TemplateResponse("v2/upload_result.html", context)
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка обработки файла: {str(e)}")


@router.get("/users")
async def list_users(
    request: Request, 
    sort_by: str = "newest", 
    user_type: str = "all",
    search: str = "",
    group_filter: str = ""
):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Базовый запрос
        query = "SELECT * FROM users WHERE 1=1"
        params = []
        
        # Фильтрация по типу пользователя
        if user_type != "all":
            query += " AND user_type = ?"
            params.append(user_type)
        
        # Фильтрация по группе
        if group_filter:
            query += " AND group_name = ?"
            params.append(group_filter)
        
        # Поиск
        if search:
            query += " AND (last_name LIKE ? OR first_name LIKE ? OR middle_name LIKE ? OR group_name LIKE ? OR login LIKE ?)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term, search_term, search_term])
        
        # Сортировка
        if sort_by == "alphabet":
            query += " ORDER BY last_name, first_name, middle_name"
        elif sort_by == "newest":
            query += " ORDER BY created_at DESC"
        elif sort_by == "oldest":
            query += " ORDER BY created_at ASC"
        elif sort_by == "group":
            query += " ORDER BY group_name, last_name, first_name"
        
        cursor.execute(query, params)
        users = cursor.fetchall()
        
        # Получаем список всех групп для фильтра
        cursor.execute("SELECT DISTINCT group_name FROM users WHERE group_name IS NOT NULL AND group_name != '' ORDER BY group_name")
        groups = [row['group_name'] for row in cursor.fetchall()]
    
    # Разделение пользователей на преподавателей и студентов
    teachers = [dict(user) for user in users if user['user_type'] == 'teacher']
    students = [dict(user) for user in users if user['user_type'] == 'student']
    
    context = get_template_context(request)
    context.update({
        "request": request,
        "teachers": teachers,
        "students": students,
        "sort_by": sort_by,
        "current_user_type": user_type,
        "search_query": search,
        "group_filter": group_filter,
        "available_groups": groups
    })
    
    return templates.TemplateResponse("v2/users_list.html", context)


@router.get("/users/{user_id}/edit")
async def edit_user_form(request: Request, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
    
    context = get_template_context(request)
    context.update({
        "request": request,
        "user": dict(user)
    })
    
    return templates.TemplateResponse("v2/edit_user.html", context)


@router.post("/users/{user_id}/update")
async def update_user(user_id: int, request: Request):
    form_data = await request.form()
    
    def clean(name):
        value = form_data.get(name)
        return value.strip() if isinstance(value, str) else value

    new_password = clean("password") or None  # пусто — пароль не меняется
    update_data = UserUpdate(
        last_name=clean("last_name") or None,
        first_name=clean("first_name") or None,
        middle_name=clean("middle_name"),
        group_name=clean("group_name"),
        login=clean("login") or None,
        password=hash_password(new_password) if new_password else None
    )

    with get_db_connection() as conn:
        cursor = conn.cursor()
        current = cursor.execute("SELECT login FROM users WHERE id = ?", (user_id,)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        old_login = current["login"]

        if update_data.login and update_data.login != old_login:
            taken = cursor.execute(
                "SELECT id FROM users WHERE login = ? AND id != ?", (update_data.login, user_id)
            ).fetchone()
            if taken:
                user = dict(cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
                context = get_template_context(request)
                context.update({"request": request, "user": user,
                                "error": f"Логин «{update_data.login}» уже занят"})
                return templates.TemplateResponse("v2/edit_user.html", context)

        update_fields = []
        update_values = []
        for field, value in update_data.model_dump().items():
            if value is not None:
                update_fields.append(f"{field} = ?")
                update_values.append(value or None)

        if update_fields:
            update_values.append(user_id)
            cursor.execute(
                f"UPDATE users SET {', '.join(update_fields)} WHERE id = ?",
                update_values
            )
            conn.commit()

    if update_data.password:
        remove_user_sessions(old_login)  # после смены пароля нужно войти заново
    elif update_data.login and update_data.login != old_login:
        rename_user_sessions(old_login, update_data.login)

    return RedirectResponse(url="/v2/users", status_code=303)


@router.post("/users/{user_id}/regenerate_password")
async def regenerate_password(request: Request, user_id: int):
    new_password = generate_password()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        cursor.execute(
            "UPDATE users SET password = ? WHERE id = ?",
            (hash_password(new_password), user_id)
        )
        conn.commit()

    remove_user_sessions(user["login"])
    return render_credentials(request, [{
        "full_name": " ".join(filter(None, [user["last_name"], user["first_name"], user["middle_name"]])),
        "group_name": user["group_name"] or "",
        "login": user["login"],
        "password": new_password,
    }], "Новый пароль сгенерирован")


@router.post("/users/{user_id}/delete")
async def delete_user(request: Request, user_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        user = cursor.execute("SELECT login FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if user["login"] == get_user_from_session(request):
            return JSONResponse({"detail": "Нельзя удалить самого себя"}, status_code=400)
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()

    remove_user_sessions(user["login"])
    return RedirectResponse(url="/v2/users", status_code=303)
