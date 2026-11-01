"""Этап 1: два студента одновременно проходят РАЗНЫЕ тесты — состояния не должны смешиваться."""
import os, sys, sqlite3, shutil
from openpyxl import Workbook
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/paraphrase-multilingual-MiniLM-L12-v2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
# Тестовые пароли: в копии БД задаём известные открытые пароли (импорт main их захеширует)
_pre = __import__("sqlite3").connect("users.db")
_pre.execute("update users set password = 'pw_' || login")
_pre.commit()

def make(name, rows):
    wb = Workbook(); ws = wb.active
    for q, a in rows: ws.append([q, a])
    wb.save(f"uploaded_files/{name}")
make("A.xlsx", [("Столица Франции?", '"Париж"'), ("Сколько будет 2+2?", '"четыре","4"'), ("Цвет неба?", '"голубой","синий"')])
make("B.xlsx", [("Автор Евгения Онегина?", '"Пушкин","Александр Сергеевич Пушкин"'), ("Самая длинная река России?", '"Лена","Обь"')])

import sqlite3 as _sq
_pre = _sq.connect('users.db')
CREDS = {}
for _t, _l, _p in _pre.execute('select user_type, login, password from users order by id'):
    CREDS.setdefault(_t, []).append((_l, _p))
_pre.close()
from fastapi.testclient import TestClient
import main
db = sqlite3.connect("users.db")
students = CREDS["student"][:2]
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)

c1, c2 = TestClient(main.app), TestClient(main.app)
for c, (l, p) in zip((c1, c2), students):
    r = c.post("/login", data={"username": l, "password": p}, follow_redirects=False)
    check(f"вход {l}", r.status_code == 303)

r1 = c1.post("/select", data={"filename": "A.xlsx"}, follow_redirects=False)
r2 = c2.post("/select", data={"filename": "B.xlsx"}, follow_redirects=False)
check("оба начали тест", r1.status_code == r2.status_code == 303, (r1.status_code, r2.status_code))

q1 = c1.get("/quiz?idx=0").text; q2 = c2.get("/quiz?idx=0").text
check("студент 1 видит вопрос из A", "Столица Франции" in q1)
check("студент 2 видит вопрос из B", "Евгения Онегина" in q2 and "Столица Франции" not in q2)

# Перемешанные ответы
c1.post("/answer", data={"idx": 0, "user_answer": "Париж"})
c2.post("/answer", data={"idx": 0, "user_answer": "Пушкин"})
c1.post("/answer", data={"idx": 1, "user_answer": "четыре"})
c2.post("/answer", data={"idx": 1, "user_answer": "Лена"})

s1 = c1.get("/check_completion").json(); s2 = c2.get("/check_completion").json()
check("у студента 1: 3 вопроса, не хватает 3-го", s1["total_questions"] == 3 and s1["unanswered"] == [3], s1)
check("у студента 2: 2 вопроса, всё отвечено", s2["total_questions"] == 2 and s2["completed"], s2)

r = c1.get("/final_results", follow_redirects=False)
check("незаконченный тест не завершается (редирект на пропуск)", r.status_code == 303 and r.headers["location"] == "/quiz?idx=2", r.headers.get("location"))
c1.post("/answer", data={"idx": 2, "user_answer": "голубой"})

f2 = c2.get("/final_results"); f1 = c1.get("/final_results")
check("итоги студента 2 про его тест", "Онегина" in f2.text and "Франции" not in f2.text)
check("итоги студента 1 про его тест", "Франции" in f1.text and "Онегина" not in f1.text)
rows = db.execute("select login, filename, total_questions, total_correct from test_results where filename in ('A.xlsx','B.xlsx') order by id").fetchall()
print("     сохранено в БД:", rows)
check("в БД два результата с правильными файлами", sorted(r[1:3] for r in rows) == [("A.xlsx", 3), ("B.xlsx", 2)])
check("после завершения ответы не принимаются", c1.post("/answer", data={"idx": 0, "user_answer": "x"}).status_code == 409)

c3 = TestClient(main.app); l, p = students[0]
c3.post("/login", data={"username": l, "password": p})
r = c3.get("/quiz?idx=0")
check("без начатого теста /quiz не падает", r.status_code == 200 and "Тест не начат" in r.text, r.status_code)
check("эмбеддинги закэшированы на файл", len(__import__("services.quiz", fromlist=["x"])._quiz_bank_cache) == 2)
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
