"""Создание пользователей: логины, проверка дублей, сохранение."""
from typing import Optional

from pydantic import BaseModel

from core.database import get_db_connection
from core.security import hash_password


class UserUpdate(BaseModel):
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    group_name: Optional[str] = None
    login: Optional[str] = None
    password: Optional[str] = None


def generate_login(last_name: str, first_name: str, middle_name: str = None) -> str:
    """Генерация логина в формате фамилия+инициалы на английском"""
    # Транслитерация кириллицы в латиницу
    translit_map = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ы': 'y', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
        'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'Й': 'Y', 'К': 'K', 'Л': 'L', 'М': 'M',
        'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
        'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
        'Ы': 'Y', 'Э': 'E', 'Ю': 'Yu', 'Я': 'Ya'
    }
    
    def transliterate(text):
        return ''.join(translit_map.get(char, char) for char in text.lower())
    
    # Транслитерация фамилии
    last_name_en = transliterate(last_name)
    
    # Получение первой буквы имени
    first_initial = transliterate(first_name[0]) if first_name else ''
    
    # Получение первой буквы отчества (если есть)
    middle_initial = transliterate(middle_name[0]) if middle_name else ''
    
    login = f"{last_name_en}{first_initial}{middle_initial}"
    
    return login


def user_exists(last_name: str, first_name: str, middle_name: str, user_type: str) -> bool:
    """Проверяет, существует ли пользователь с такими ФИО и типом"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM users 
            WHERE last_name = ? AND first_name = ? AND middle_name = ? AND user_type = ?
        ''', (
            last_name,
            first_name, 
            middle_name or '',
            user_type
        ))
        existing_user = cursor.fetchone()
        return existing_user is not None


def save_user_to_db(user_data: dict) -> dict:
    """Сохранение пользователя в базу данных с обработкой дубликатов"""
    # Проверяем, существует ли пользователь
    if user_exists(
        user_data['last_name'],
        user_data['first_name'],
        user_data.get('middle_name'),
        user_data['user_type']
    ):
        return {"exists": True}
    
    login = user_data['login']
    password = user_data['password']
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Проверка уникальности логина
        cursor.execute("SELECT id FROM users WHERE login = ?", (login,))
        existing_login = cursor.fetchone()
        
        # Если логин уже существует, добавляем число
        counter = 1
        original_login = login
        while existing_login:
            login = f"{original_login}{counter}"
            cursor.execute("SELECT id FROM users WHERE login = ?", (login,))
            existing_login = cursor.fetchone()
            counter += 1
        
        # Сохранение пользователя в базу данных
        cursor.execute('''
            INSERT INTO users (user_type, last_name, first_name, middle_name, group_name, login, password)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            user_data['user_type'],
            user_data['last_name'],
            user_data['first_name'],
            user_data.get('middle_name'),
            user_data.get('group_name'),
            login,
            hash_password(password)
        ))
        conn.commit()
        user_id = cursor.lastrowid
        
        return {
            "id": user_id, 
            "login": login, 
            "password": password,
            "exists": False
        }
