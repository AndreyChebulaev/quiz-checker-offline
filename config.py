"""Настройки приложения. Значения можно переопределить переменными окружения."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# База данных SQLite
DATABASE_PATH = os.getenv("QUIZ_DATABASE_PATH", "users.db")

# Каталоги с файлами тестов
UPLOAD_DIR = "uploaded_files"                 # опубликованные тесты
IMPORTED_FILES_DIR = "uploaded_files_imports"  # файлы, импортированные в редактор

TEMPLATES_DIR = "templates"
STATIC_DIR = "static"

# Порог косинусного сходства, начиная с которого ответ считается верным
THRESHOLD = float(os.getenv("QUIZ_AI_THRESHOLD", "0.76"))

# Время жизни сессии пользователя, часов
SESSION_LIFETIME_HOURS = int(os.getenv("QUIZ_SESSION_HOURS", "12"))
