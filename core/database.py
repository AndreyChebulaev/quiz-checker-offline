"""Подключение к SQLite, создание таблиц и миграции."""
import csv
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from config import DATABASE_PATH
from core.security import generate_password, hash_password, is_hashed
from core.sessions import init_sessions_table


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def ensure_admin_exists(cursor) -> None:
    """Создаёт первого администратора, если в системе нет ни одного.

    Пароль берётся из переменной окружения QUIZ_ADMIN_PASSWORD, иначе
    генерируется случайно и записывается в first_admin_credentials.txt.
    """
    cursor.execute("SELECT COUNT(*) FROM users WHERE user_type = 'admin'")
    if cursor.fetchone()[0] > 0:
        return

    password = os.getenv("QUIZ_ADMIN_PASSWORD") or generate_password(12)
    login = "admin"
    cursor.execute("SELECT COUNT(*) FROM users WHERE login = ?", (login,))
    if cursor.fetchone()[0] > 0:
        login = f"admin_{secrets.token_hex(2)}"
    cursor.execute(
        "INSERT INTO users (user_type, last_name, first_name, middle_name, group_name, login, password) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("admin", "Администратор", "Системы", None, None, login, hash_password(password))
    )
    if not os.getenv("QUIZ_ADMIN_PASSWORD"):
        with open("first_admin_credentials.txt", "w", encoding="utf-8") as f:
            f.write(f"Логин: {login}\nПароль: {password}\n"
                    "Смените пароль после первого входа и удалите этот файл.\n")
    print(f"[security] Создан администратор '{login}'. Данные для входа: first_admin_credentials.txt")


def migrate_plaintext_passwords(backup_dir: str = "_backup") -> int:
    """Однократно переводит пароли, хранившиеся открытым текстом, в хеши.

    Перед заменой старые логины и пароли сохраняются в _backup/ (папка
    исключена из git), чтобы тестовые учётки не потерялись.
    Возвращает число обновлённых записей.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        rows = conn.execute("SELECT id, user_type, login, password FROM users").fetchall()
        plain = [row for row in rows if not is_hashed(row[3])]
        if not plain:
            return 0

        os.makedirs(backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"passwords_before_hashing_{stamp}.csv")
        with open(backup_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(["user_type", "login", "password"])
            for _, user_type, login, password in plain:
                writer.writerow([user_type, login, password])

        for user_id, _, _, password in plain:
            conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user_id))

    print(f"[security] Пароли захешированы: {len(plain)}. Старые пароли сохранены в {backup_path}")
    return len(plain)


# Инициализация базы данных с новой структурой
def create_tables():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_type TEXT NOT NULL,
            last_name TEXT NOT NULL,
            first_name TEXT NOT NULL,
            middle_name TEXT,
            group_name TEXT,
            login TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            time_limit_minutes INTEGER,
            available_until TEXT,
            max_attempts INTEGER,
            allowed_students TEXT,
            allowed_groups TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT NOT NULL,
            filename TEXT NOT NULL,
            attempts_count INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(login, filename)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS test_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT NOT NULL,
            filename TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            total_correct INTEGER NOT NULL,
            percentage REAL NOT NULL,
            elapsed_seconds INTEGER,
            threshold REAL NOT NULL,
            forced_finish INTEGER NOT NULL DEFAULT 0,
            started_at TEXT,
            finished_at TEXT NOT NULL,
            user_answers_json TEXT,
            questions_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(login, filename, attempt_number)
        )
    ''')

    # Миграция для старых БД
    cursor.execute("PRAGMA table_info(test_settings)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if "allowed_students" not in existing_columns:
        cursor.execute("ALTER TABLE test_settings ADD COLUMN allowed_students TEXT")
    if "allowed_groups" not in existing_columns:
        cursor.execute("ALTER TABLE test_settings ADD COLUMN allowed_groups TEXT")
    if "max_attempts" not in existing_columns:
        cursor.execute("ALTER TABLE test_settings ADD COLUMN max_attempts INTEGER")

    cursor.execute("PRAGMA table_info(test_results)")
    result_columns = {row[1] for row in cursor.fetchall()}
    if "user_answers_json" not in result_columns:
        cursor.execute("ALTER TABLE test_results ADD COLUMN user_answers_json TEXT")
    if "questions_json" not in result_columns:
        cursor.execute("ALTER TABLE test_results ADD COLUMN questions_json TEXT")
    
    # Порог и ширина «серой зоны» для конкретного теста (NULL — значения по умолчанию)
    cursor.execute("PRAGMA table_info(test_settings)")
    settings_columns = {row[1] for row in cursor.fetchall()}
    if "threshold" not in settings_columns:
        cursor.execute("ALTER TABLE test_settings ADD COLUMN threshold REAL")
    if "review_margin" not in settings_columns:
        cursor.execute("ALTER TABLE test_settings ADD COLUMN review_margin REAL")
    if "students_closed" not in settings_columns:
        # 1 — тест закрыт для всех студентов (виден только преподавателям с доступом)
        cursor.execute("ALTER TABLE test_settings ADD COLUMN students_closed INTEGER NOT NULL DEFAULT 0")

    # Сколько ответов попытки ещё ждут проверки преподавателем
    cursor.execute("PRAGMA table_info(test_results)")
    if "pending_review" not in {row[1] for row in cursor.fetchall()}:
        cursor.execute("ALTER TABLE test_results ADD COLUMN pending_review INTEGER NOT NULL DEFAULT 0")

    # Оценка каждого ответа: автоматическое решение и решение преподавателя
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS answer_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            result_id INTEGER NOT NULL REFERENCES test_results(id) ON DELETE CASCADE,
            question_index INTEGER NOT NULL,
            question TEXT,
            user_answer TEXT,
            best_reference TEXT,
            score REAL,
            auto_verdict TEXT NOT NULL,
            review_reason TEXT,
            final_verdict TEXT,
            reviewed_by TEXT,
            reviewed_at TEXT,
            UNIQUE(result_id, question_index)
        )
    ''')
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_answer_results_pending ON answer_results(auto_verdict, final_verdict)"
    )

    ensure_admin_exists(cursor)
    
    conn.commit()
    conn.close()


def init_db():
    """Создаёт все таблицы, выполняет миграции и создаёт первого администратора."""
    create_tables()
    init_sessions_table()
    migrate_plaintext_passwords()
