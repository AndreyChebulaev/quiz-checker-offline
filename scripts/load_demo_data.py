"""Демонстрационные данные для защиты: преподаватели, студенты, тест и попытки.

Рекомендуется отдельная демо-база, чтобы не смешивать её с рабочей:

    set QUIZ_DATABASE_PATH=demo.db
    set QUIZ_UPLOAD_DIR=demo_files
    python scripts\\load_demo_data.py
    python -m uvicorn main:app

Скрипт можно запускать повторно: существующие пользователи и попытки не дублируются.
Логины и пароли сохраняются в demo_credentials.txt (файл исключён из git).
Попытки студентов оцениваются настоящей моделью, поэтому нужна папка local_models/.
"""
import csv
import os
import random
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

DEMO_PASSWORDS = {"admin": "demo-admin-2026", "teacher": "demo-teacher-2026", "student": "demo-student-2026"}
os.environ.setdefault("QUIZ_ADMIN_PASSWORD", DEMO_PASSWORDS["admin"])

import main  # noqa: E402,F401  — создаёт таблицы и первого администратора
from config import DATABASE_PATH, UPLOAD_DIR  # noqa: E402
from core.security import hash_password  # noqa: E402
from services.ai_backend import THRESHOLD, ai_service  # noqa: E402
from services.quiz import load_quiz_bank  # noqa: E402
from services.results import save_test_result  # noqa: E402
from services.review import save_answer_results  # noqa: E402
from services.scoring import evaluate_answer  # noqa: E402
from services.test_access import save_teacher_shares, set_owner  # noqa: E402
from services.test_settings import increment_user_attempts_count, upsert_test_settings  # noqa: E402

GROUP = "ДЕМО-1"
TEACHERS = [
    ("demo_teacher", "Демидова", "Елена", "Сергеевна"),
    ("demo_teacher2", "Коллегин", "Андрей", "Петрович"),
]
# логин, ФИО, «профиль» — какие типы ответов студент даёт чаще
STUDENTS = [
    ("demo_st1", "Отличникова", "Мария", "Андреевна", {"para": 7, "short": 2, "neg": 1}),
    ("demo_st2", "Хорошистов", "Илья", "Олегович", {"para": 5, "short": 3, "partial": 1, "wrong": 1}),
    ("demo_st3", "Средняков", "Павел", "Игоревич", {"para": 3, "short": 2, "partial": 2, "wrong": 2, "neg": 1}),
    ("demo_st4", "Путаница", "Анна", "Викторовна", {"para": 2, "short": 1, "neg": 4, "wrong": 3}),
    ("demo_st5", "Незнайкин", "Глеб", "Сергеевич", {"short": 1, "wrong": 3, "partial": 3, "off": 3}),
    ("demo_st6", "Новенькая", "Вера", "Ивановна", None),  # без попытки — пройти тест вживую на защите
]
TEST_FILE = "Интеллектуальные системы (демо).xlsx"
DATASET = ROOT / "experiments" / "data" / "answers_intelligent_systems.csv"
SOURCE_TEST = ROOT / "experiments" / "data" / "test_intelligent_systems.xlsx"


def ensure_user(conn, user_type, login, last, first, middle, group, created):
    if conn.execute("SELECT 1 FROM users WHERE login = ?", (login,)).fetchone():
        return False
    conn.execute(
        "INSERT INTO users (user_type, last_name, first_name, middle_name, group_name, login, password)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_type, last, first, middle, group, login, hash_password(DEMO_PASSWORDS[user_type])),
    )
    created.append((user_type, login))
    return True


def main_demo():
    created = []
    with sqlite3.connect(DATABASE_PATH) as conn:
        for login, last, first, middle in TEACHERS:
            ensure_user(conn, "teacher", login, last, first, middle, None, created)
        for login, last, first, middle, _ in STUDENTS:
            ensure_user(conn, "student", login, last, first, middle, GROUP, created)

    # Тест: вопросы и эталоны — из набора эксперимента
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    test_path = Path(UPLOAD_DIR) / TEST_FILE
    if not test_path.exists():
        shutil.copy(SOURCE_TEST, test_path)
    set_owner(TEST_FILE, "demo_teacher")
    upsert_test_settings(TEST_FILE, "30", "2", "", "", GROUP)
    save_teacher_shares(TEST_FILE, "demo_teacher", [{"login": "demo_teacher2", "can_edit": False, "can_view_results": True}])

    # Попытки студентов: ответы берутся из размеченного набора по «профилю» студента
    with open(DATASET, encoding="utf-8-sig") as f:
        answers = list(csv.DictReader(f, delimiter=";"))
    by_question = {}
    for row in answers:
        by_question.setdefault(int(row["question_id"]), {}).setdefault(row["category"], []).append(row["answer"])

    if ai_service is None or not ai_service.is_ready():
        print("Модель не найдена (local_models/) — пользователи и тест созданы, попытки пропущены.")
    else:
        bank = load_quiz_bank(str(test_path))
        rng = random.Random(2026)
        with sqlite3.connect(DATABASE_PATH) as conn:
            done = {r[0] for r in conn.execute("SELECT DISTINCT login FROM test_results WHERE filename = ?", (TEST_FILE,))}
        for index, (login, *_, profile) in enumerate(STUDENTS):
            if not profile or login in done:
                continue
            categories = [c for c, n in profile.items() for _ in range(n)]
            student_answers = []
            for qid in range(1, len(bank["questions"]) + 1):
                category = rng.choice(categories)
                student_answers.append(rng.choice(by_question[qid][category]))
            evaluations = [
                evaluate_answer(ai_service, a, bank["reference_answers"][i], bank["embeddings"][i], THRESHOLD)
                for i, a in enumerate(student_answers)
            ]
            attempt = increment_user_attempts_count(login, TEST_FILE)
            finished = datetime.now() - timedelta(days=2, hours=index)
            correct = sum(e.verdict == "correct" for e in evaluations)
            result_id = save_test_result(
                login=login, filename=TEST_FILE, attempt_number=attempt,
                total_questions=len(evaluations), total_correct=correct,
                percentage=correct / len(evaluations) * 100, elapsed_seconds=rng.randint(600, 1700),
                threshold=THRESHOLD, forced_finish=False,
                started_at=finished - timedelta(minutes=25), finished_at=finished,
                user_answers_list=student_answers, questions_list=bank["questions"],
                pending_review=sum(e.verdict == "review" for e in evaluations),
            )
            save_answer_results(result_id, bank["questions"], student_answers, evaluations)
            print(f"  {login}: {correct}/{len(evaluations)} верно, "
                  f"{sum(e.verdict == 'review' for e in evaluations)} на проверке")

    lines = [
        "Демо-данные системы проверки открытых вопросов",
        f"База: {DATABASE_PATH}, тесты: {UPLOAD_DIR}/",
        "",
        f"Администратор демо-базы: admin / {DEMO_PASSWORDS['admin']} (если база создана этим скриптом)",
        f"Преподаватель (владелец теста): demo_teacher / {DEMO_PASSWORDS['teacher']}",
        f"Коллега (видит только результаты): demo_teacher2 / {DEMO_PASSWORDS['teacher']}",
        f"Студенты группы {GROUP}: demo_st1 … demo_st6 / {DEMO_PASSWORDS['student']}",
        "  demo_st6 ещё не проходил тест — для показа прохождения вживую.",
        "",
        "Не используйте демо-пароли в рабочей базе.",
    ]
    Path("demo_credentials.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Создано пользователей: {len(created)}. Данные для входа: demo_credentials.txt")


if __name__ == "__main__":
    main_demo()
