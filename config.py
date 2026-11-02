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

# ML-модель для оценки ответов (папка в local_models/).
# Выбрана по результатам эксперимента: experiments/README.md, раздел «Выводы».
MODELS_DIR = "local_models"
MODEL_NAME = os.getenv("QUIZ_MODEL", "rubert-tiny2")
FALLBACK_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # если основной модели нет

# Откуда скачивать модели (python download_model.py)
MODEL_HF_IDS = {
    "rubert-tiny2": "cointegrated/rubert-tiny2",
    "paraphrase-multilingual-MiniLM-L12-v2": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
}

# Порог косинусного сходства, начиная с которого ответ считается верным.
# У каждой модели своя шкала сходства, поэтому порог подобран отдельно для каждой
# (максимум F1 при кросс-валидации по вопросам).
MODEL_THRESHOLDS = {
    "rubert-tiny2": 0.71,
    "paraphrase-multilingual-MiniLM-L12-v2": 0.60,
}
DEFAULT_THRESHOLD = 0.71
THRESHOLD_OVERRIDE = os.getenv("QUIZ_AI_THRESHOLD")  # задать порог вручную

# Время жизни сессии пользователя, часов
SESSION_LIFETIME_HOURS = int(os.getenv("QUIZ_SESSION_HOURS", "12"))
