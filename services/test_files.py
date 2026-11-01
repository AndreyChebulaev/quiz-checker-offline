"""Файлы тестов (Excel): пути, проверка имён, чтение и запись."""
import ast
import glob
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import HTTPException
from openpyxl import Workbook, load_workbook

from config import IMPORTED_FILES_DIR, UPLOAD_DIR
from services.formatting import format_date_ru, parse_available_until
from services.test_settings import can_user_access_test, get_attempt_limit_status, get_test_settings

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(IMPORTED_FILES_DIR, exist_ok=True)


def validate_uploaded_excel_filename(filename: Optional[str]) -> str:
    raw_filename = (filename or "").strip()
    safe_filename = os.path.basename(raw_filename)

    if not raw_filename:
        raise HTTPException(status_code=400, detail="Не указано имя файла")
    if safe_filename != raw_filename:
        raise HTTPException(status_code=400, detail="Некорректное имя файла")
    if not safe_filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Разрешены только Excel-файлы .xlsx")

    return safe_filename


def get_uploaded_file_path(filename: str) -> str:
    safe_filename = validate_uploaded_excel_filename(filename)
    return os.path.join(UPLOAD_DIR, safe_filename)


def get_uploaded_files(user_info: Optional[Dict[str, Any]] = None):
    """Получает список загруженных файлов"""
    files = []
    login = user_info.get("login") if user_info else None
    for file_path in glob.glob(os.path.join(UPLOAD_DIR, "*.xlsx")):
        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        settings = get_test_settings(filename)
        has_access, _ = can_user_access_test(settings, user_info)
        if not has_access:
            continue
        attempt_limit = get_attempt_limit_status(settings, login, filename)
        available_until_dt = parse_available_until(settings.get("available_until"))
        is_available = True
        is_limit_expired = False
        unavailable_reason = ""
        if available_until_dt and datetime.now() > available_until_dt:
            is_available = False
            is_limit_expired = True
            unavailable_reason = "Дедлайн уже прошел"
        else:
            if attempt_limit["limit_reached"]:
                is_available = False
                is_limit_expired = True
                unavailable_reason = f"Лимит попыток исчерпан ({attempt_limit['attempts_used']} из {attempt_limit['max_attempts']})."
        files.append({
            "name": filename,
            "size": file_size,
            "path": file_path,
            "time_limit_minutes": settings.get("time_limit_minutes"),
            "available_until": settings.get("available_until"),
            "available_until_display": format_date_ru(settings.get("available_until")),
            "max_attempts": attempt_limit["max_attempts"],
            "attempts_used": attempt_limit["attempts_used"],
            "attempts_left": attempt_limit["attempts_left"],
            "is_available": is_available,
            "is_limit_expired": is_limit_expired,
            "unavailable_reason": unavailable_reason
        })
    return files


def parse_quoted_strings(s):
    """Парсит строку с ответами в кавычках, разделенных запятыми"""
    return [m.group(1) for m in re.finditer(r'"([^"]*)"', s)]


def normalize_excel_filename(filename: str) -> str:
    safe_filename = os.path.basename((filename or "").strip())
    if not safe_filename:
        raise HTTPException(status_code=400, detail="Не указано имя файла")
    if not safe_filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Разрешены только Excel файлы")
    return safe_filename


def get_imported_file_path(filename: str) -> Path:
    return Path(IMPORTED_FILES_DIR) / normalize_excel_filename(filename)


def get_saved_file_path(filename: str) -> Path:
    return Path(UPLOAD_DIR) / normalize_excel_filename(filename)


def parse_answers_string(answers_str: str) -> list:
    """Парсит строку с ответами в кавычках"""
    # Убираем лишние пробелы
    answers_str = answers_str.strip()
    
    # Если строка уже похожа на список Python
    if answers_str.startswith('[') and answers_str.endswith(']'):
        try:
            return ast.literal_eval(answers_str)
        except:
            pass
    
    # Разбиваем по кавычкам
    answers = []
    in_quote = False
    current_answer = ""
    
    for char in answers_str:
        if char == '"':
            if in_quote:
                # Закрывающая кавычка
                if current_answer:
                    answers.append(current_answer)
                current_answer = ""
            in_quote = not in_quote
        elif char == ',' and not in_quote:
            # Запятая вне кавычек - разделитель
            if current_answer:
                answers.append(current_answer.strip())
                current_answer = ""
        else:
            if in_quote or char not in [' ', '\t', '\n']:
                current_answer += char
    
    # Добавляем последний ответ
    if current_answer.strip():
        answers.append(current_answer.strip())
    
    return answers


def parse_answers(answers_str):
    """Парсит строку с ответами в список"""
    if pd.isna(answers_str):
        return [""]
    
    try:
        # Пробуем парсить как Python список
        if answers_str.startswith('"') and answers_str.endswith('"'):
            answers_str = f"[{answers_str}]"
        
        parsed = ast.literal_eval(answers_str)
        if isinstance(parsed, list):
            return [str(item).strip('"') for item in parsed]  # Убираем кавычки при парсинге
        else:
            return [str(parsed).strip('"')]
    except:
        # Если парсинг не удался, возвращаем как есть (убирая лишние кавычки)
        clean_str = str(answers_str).strip().strip('"')
        return [clean_str]


def format_answers(answers_list):
    """Форматирует список ответов в строку с кавычками"""
    if not answers_list:
        return ""
    
    # Фильтруем пустые ответы и убираем существующие кавычки
    clean_answers = []
    for answer in answers_list:
        if answer and str(answer).strip():
            clean_answer = str(answer).strip().strip('"')
            if clean_answer:  # Проверяем, не пустая ли строка после очистки
                clean_answers.append(clean_answer)
    
    if not clean_answers:
        return ""
    
    # Форматируем с кавычками
    formatted = ','.join([f'"{answer}"' for answer in clean_answers])
    return formatted


def process_excel_file(file_path):
    """Обрабатывает Excel файл и возвращает данные с сохранением формата"""
    # Читаем весь файл с помощью openpyxl для сохранения форматирования
    wb = load_workbook(file_path)
    ws = wb.active
    
    # Получаем все данные из листа
    data = []
    for row in ws.iter_rows(values_only=True):
        data.append(list(row) if row else [])
    
    # Закрываем workbook
    wb.close()
    
    return data


def save_excel_file(file_path, data):
    """Сохраняет данные в Excel файл"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Вопросы и ответы"
    
    # Добавляем данные
    for row in data:
        ws.append(row)
    
    wb.save(file_path)
    wb.close()


def create_new_excel_file(file_path, data):
    """Создает новый Excel файл с данными БЕЗ заголовков"""
    wb = Workbook()
    ws = wb.active
    ws.title = "Вопросы и ответы"
    
    # Добавляем данные БЕЗ заголовков - сразу вопросы и ответы
    for row in data:
        ws.append([row["question"], row["answers"]])
    
    wb.save(file_path)
    wb.close()
