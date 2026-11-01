"""Дымовой тест: копия проекта, заглушка модели, прогон основных сценариев."""
import os, sys, sqlite3, shutil, re
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/paraphrase-multilingual-MiniLM-L12-v2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
# Тестовые пароли: в копии БД задаём известные открытые пароли (импорт main их захеширует)
_pre = __import__("sqlite3").connect("users.db")
_pre.execute("update users set password = 'pw_' || login")
_pre.commit()
import sqlite3 as _sq
_pre = _sq.connect('users.db')
CREDS = {}
for _t, _l, _p in _pre.execute('select user_type, login, password from users order by id'):
    CREDS.setdefault(_t, []).append((_l, _p))
_pre.close()
from fastapi.testclient import TestClient
import main
c = TestClient(main.app)
db = sqlite3.connect("users.db")
def creds(t):
    return CREDS[t][0]
    r = db.execute("select login,password from users where user_type=? limit 1", (t,)).fetchone()
    return r
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)

def login(t):
    l, p = creds(t)
    c.cookies.clear()
    r = c.post("/login", data={"username": l, "password": p}, follow_redirects=False)
    check(f"вход {t}", r.status_code == 303, r.status_code)
    return l

login("admin")
for url in ["/select", "/results", "/admin/users", "/v2/", "/v2/users", "/v2/register", "/v2/upload", "/editor/", "/editor/create-new", "/editor/edit/QuestionsAnswers.xlsx", "/results/export"]:
    r = c.get(url); check(f"GET {url}", r.status_code == 200, r.status_code)

stud = login("student")
r = c.get("/select"); check("студент видит список тестов", r.status_code == 200)
r = c.post("/select", data={"filename": "QuestionsAnswers.xlsx"}, follow_redirects=False)
check("старт теста", r.status_code == 303 and "/quiz" in r.headers.get("location", ""), (r.status_code, r.headers.get("location"), re.findall(r'class="error[^>]*>([^<]+)', r.text)[:1]))
r = c.get("/quiz?idx=0"); check("вопрос 1", r.status_code == 200)
n = len(main.questions) if hasattr(main, "questions") else 2
for i in range(n):
    r = c.post("/answer", data={"idx": i, "user_answer": "глобус" if i else "саламандра посланница ада"})
    check(f"ответ {i+1}", r.status_code == 200, r.status_code)
r = c.get("/check_completion"); check("проверка завершения", r.status_code == 200 and r.json().get("completed"), r.text[:100])
r = c.get("/final_results"); check("итоги", r.status_code == 200, r.status_code)
m = re.search(r"(\d+(?:\.\d+)?)\s*%", r.text); print("     процент на странице:", m.group(0) if m else None)
r = c.get("/results"); check("история результатов", r.status_code == 200)
r = c.get("/logout", follow_redirects=False); check("выход", r.status_code == 303)
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
