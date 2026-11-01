"""Редактор: создание теста, настройки доступа, редактирование, скачивание; студент видит/не видит тест."""
import os, sys, shutil, io
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/paraphrase-multilingual-MiniLM-L12-v2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
import sqlite3
pre = sqlite3.connect("users.db"); pre.execute("update users set password = 'pw_' || login"); pre.commit()
rows = pre.execute("select user_type, login, group_name from users order by id").fetchall(); pre.close()
first = {}
for t, l, g in rows: first.setdefault(t, (l, g))
from fastapi.testclient import TestClient
import main
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)
def client(t):
    c = TestClient(main.app); l = first[t][0]
    c.post("/login", data={"username": l, "password": "pw_" + l}); return c
t = client("teacher"); s = client("student")
r = t.get("/app", follow_redirects=False)
check("/app ведёт в редактор", r.status_code == 307 and r.headers["location"].startswith("/editor"), r.headers.get("location"))
check("редактор открывается", t.get("/editor").status_code == 200)
check("студент не попадает в редактор", s.get("/editor/", follow_redirects=False).status_code == 303)
import json as _j
def save(t, filename, qs, ans, **extra):
    data = {"filename": filename, "questions": qs, "answers": [_j.dumps(a, ensure_ascii=False) for a in ans]}
    data.update(extra)
    return t.post("/editor/save_edit", data=data)
r = save(t, "new_file.xlsx", ["Что такое ML?"], [["машинное обучение"]], test_name="")
check("новый тест без названия отклоняется", r.status_code == 400, r.json())
r = save(t, "new_file.xlsx", ["Что такое ML?", "2+2?"], [["машинное обучение"], ["4", "четыре"]], test_name="Новый")
check("создание теста с названием", r.status_code == 200 and os.path.exists("uploaded_files/Новый.xlsx"), r.json())
r = save(t, "new_file.xlsx", ["X?"], [["y"]], test_name="Новый")
check("одноимённый тест не затирается", r.status_code == 409, r.json())
r = save(t, "new_file.xlsx", ["X?"], [["y"]], test_name="../../evil")
check("опасные символы в названии вычищаются", r.status_code == 200 and "/" not in r.json()["filename"] and os.path.exists("uploaded_files/" + r.json()["filename"]), r.json())
check("нет файла new_file.xlsx от создания", not os.path.exists("uploaded_files/new_file.xlsx") or os.path.getmtime("uploaded_files/new_file.xlsx") < __import__("time").time() - 60)
r = t.get("/editor/edit/Новый.xlsx"); check("открытие на редактирование", r.status_code == 200 and "Что такое ML" in r.text)
other_group = "НЕТ-ТАКОЙ-ГРУППЫ"
r = save(t, "Новый.xlsx", ["Что такое ML?", "2+2?", "Столица России?"], [["машинное обучение"], ["4", "четыре"], ["Москва"]],
          time_limit_minutes="15", max_attempts="2", available_until="2099-12-31", allowed_students="", allowed_groups=other_group)
check("сохранение правок и настроек", r.status_code == 200, r.status_code)
st = __import__("services.test_settings", fromlist=["x"]).get_test_settings("Новый.xlsx")
check("настройки записаны", st["time_limit_minutes"] == 15 and st["max_attempts"] == 2, st)
check("студент чужой группы не видит тест", "Новый.xlsx" not in s.get("/select").text)
save(t, "Новый.xlsx", ["Что такое ML?", "2+2?", "Столица России?"], [["машинное обучение"], ["4", "четыре"], ["Москва"]],
          time_limit_minutes="15", max_attempts="2", available_until="2099-12-31", allowed_students="", allowed_groups=first["student"][1] or "")
check("студент своей группы видит тест", "Новый.xlsx" in s.get("/select").text)
r = s.post("/select", data={"filename": "Новый.xlsx"}, follow_redirects=False)
check("студент начинает тест", r.status_code == 303 and "/quiz" in r.headers["location"])
q = s.get("/quiz?idx=2").text; check("в тесте 3 вопроса после правки", "Столица России" in q)
r = t.get("/editor/download/Новый.xlsx"); check("скачивание из редактора", r.status_code == 200 and r.content[:2] == b"PK")
r = t.get("/download/Новый.xlsx"); check("скачивание из списка", r.status_code == 200 and r.content[:2] == b"PK")
check("студент не может скачать файл теста", s.get("/download/Новый.xlsx").status_code == 403)
check("студент не может удалить файл", s.post("/delete_file", data={"filename": "Новый.xlsx"}).status_code == 403)
r = t.post("/delete_file", data={"filename": "Новый.xlsx"}); check("преподаватель удаляет файл", not os.path.exists("uploaded_files/Новый.xlsx"), r.status_code)
check("путь с ../ отклоняется", t.get("/download/..%2Fusers.db").status_code in (400, 404))
import openpyxl
r = save(t, "Несуществующий.xlsx", ["q"], [["a"]])
check("правка несуществующего теста отклоняется", r.status_code == 404)
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
