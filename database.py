import csv
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from security import hash_password, is_hashed
from session_manager import init_sessions_table

DATABASE_NAME = "users.db"


def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
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

    conn.commit()
    conn.close()

    init_sessions_table()
    migrate_plaintext_passwords()


def migrate_plaintext_passwords(backup_dir: str = "_backup") -> int:
    """Однократно переводит пароли, хранившиеся открытым текстом, в хеши.

    Перед заменой старые логины и пароли сохраняются в _backup/ (папка
    исключена из git), чтобы тестовые учётки не потерялись.
    Возвращает число обновлённых записей.
    """
    with sqlite3.connect(DATABASE_NAME) as conn:
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


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()
