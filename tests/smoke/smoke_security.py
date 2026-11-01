"""Этап 2: проверки безопасности."""
import os, sys, shutil, sqlite3, re, importlib
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/paraphrase-multilingual-MiniLM-L12-v2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
# Тестовые пароли: в копии БД задаём известные открытые пароли (импорт main их захеширует)
_pre = __import__("sqlite3").connect("users.db")
_pre.execute("update users set password = 'pw_' || login")
_pre.commit()
pre = sqlite3.connect("users.db")
CREDS = {}
for t, l, p in pre.execute("select user_type, login, password from users order by id"):
    CREDS.setdefault(t, []).append((l, p))
pre.close()
from fastapi.testclient import TestClient
import main
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)
db = sqlite3.connect("users.db")

pw = [r[0] for r in db.execute("select password from users")]
check("все пароли в БД захешированы", all(p.startswith("pbkdf2_sha256$") for p in pw), len(pw))
check("резервная копия старых паролей создана", any(f.startswith("passwords_before_hashing") for f in os.listdir("_backup")))
check("дефолтного admin/admin123 нет", db.execute("select count(*) from users where login='admin'").fetchone()[0] == 1)

anon = TestClient(main.app)
for url in ["/v2/", "/v2/users", "/v2/register", "/v2/upload", "/v2/users/1/edit"]:
    r = anon.get(url, follow_redirects=False)
    check(f"аноним не попадает на {url}", r.status_code == 303 and r.headers["location"] == "/", r.status_code)
r = anon.post("/v2/register", data={"user_type": "teacher", "last_name": "Взлом", "first_name": "Хакер"}, follow_redirects=False)
check("аноним не может зарегистрировать пользователя", r.status_code == 303 and db.execute("select count(*) from users where last_name='Взлом'").fetchone()[0] == 0)
r = anon.post("/v2/users/2/delete", follow_redirects=False)
check("аноним не может удалить пользователя", r.status_code == 303)

def login_as(c, login, password):
    return c.post("/login", data={"username": login, "password": password}, follow_redirects=False)

st = TestClient(main.app); sl, sp = CREDS["student"][0]
check("студент входит со старым паролем", login_as(st, sl, sp).status_code == 303)
r = st.get("/v2/users", follow_redirects=False)
check("студент не видит /v2/users", r.status_code == 303 and r.headers["location"] == "/select")
r = st.post("/v2/users/1/delete", follow_redirects=False)
check("студент не может удалить пользователя", r.status_code == 403)
tch = TestClient(main.app); tl, tp = CREDS["teacher"][0]
login_as(tch, tl, tp)
check("преподаватель не видит /v2/users", tch.get("/v2/users", follow_redirects=False).status_code == 303)

bad = TestClient(main.app)
r = login_as(bad, sl, "неверный")
check("неверный пароль отклоняется", r.status_code == 200 and "Неверный логин или пароль" in r.text)
check("хеш вместо пароля не подходит", login_as(bad, sl, db.execute("select password from users where login=?", (sl,)).fetchone()[0]).status_code == 200)
for _ in range(5): login_as(bad, sl, "x")
r = login_as(bad, sl, sp)
check("после 5 ошибок вход блокируется", "Слишком много" in r.text)
__import__("routers.auth", fromlist=["x"])._login_failures.clear()

ad = TestClient(main.app); al, ap = CREDS["admin"][0]
check("админ входит", login_as(ad, al, ap).status_code == 303)
r = ad.post("/login", data={"username": al, "password": ap}, follow_redirects=False)
sc = r.headers.get("set-cookie", "").lower()
check("cookie: HttpOnly, SameSite=lax, срок жизни", "httponly" in sc and "samesite=lax" in sc and "max-age" in sc, sc[:120])
for url in ["/v2/", "/v2/users", "/v2/register", "/v2/upload", "/admin/users"]:
    check(f"админ открывает {url}", ad.get(url).status_code == 200)
r = ad.get("/admin/users")
check("хеши паролей не выводятся в HTML", "pbkdf2_sha256" not in r.text and "pbkdf2_sha256" not in ad.get("/v2/users").text)

r = ad.post("/v2/register", data={"user_type": "student", "last_name": "Тестов", "first_name": "Тест", "middle_name": "", "group_name": "РПСб-23-1"})
m = re.search(r"<td><code>([^<]+)</code></td>\s*<td><code>([^<]+)</code></td>", r.text)
check("при регистрации пароль показан один раз", r.status_code == 200 and m, r.status_code)
new_login, new_pass = m.groups()
check("в БД у нового пользователя хеш", db.execute("select password from users where login=?", (new_login,)).fetchone()[0].startswith("pbkdf2"))
nu = TestClient(main.app)
check("новый пользователь входит с выданным паролем", login_as(nu, new_login, new_pass).status_code == 303)
r = ad.post("/v2/register", data={"user_type": "admin", "last_name": "Злой", "first_name": "Админ"})
check("через форму нельзя создать admin", db.execute("select count(*) from users where last_name='Злой'").fetchone()[0] == 0)

uid = db.execute("select id from users where login=?", (new_login,)).fetchone()[0]
r = ad.post(f"/v2/users/{uid}/regenerate_password")
m2 = re.search(r"<td><code>([^<]+)</code></td>\s*<td><code>([^<]+)</code></td>", r.text)
check("перегенерация пароля показывает новый пароль", m2 and m2.group(2) != new_pass)
check("старая сессия после смены пароля завершена", nu.get("/select", follow_redirects=False).status_code == 303)
check("старый пароль больше не работает", login_as(TestClient(main.app), new_login, new_pass).status_code == 200)
check("новый пароль работает", login_as(TestClient(main.app), new_login, m2.group(2)).status_code == 303)

r = ad.post(f"/v2/users/{uid}/update", data={"last_name": "Тестов", "first_name": "Тест", "middle_name": "", "group_name": "РПСб-23-1", "login": new_login, "password": ""}, follow_redirects=False)
check("сохранение без пароля не меняет пароль", r.status_code == 303 and r.headers["location"] == "/v2/users" and login_as(TestClient(main.app), new_login, m2.group(2)).status_code == 303)
r = ad.post(f"/v2/users/{uid}/update", data={"last_name": "Тестов", "first_name": "Тест", "login": al, "password": ""})
check("нельзя занять чужой логин", "уже занят" in r.text)

import io
from openpyxl import Workbook
wb = Workbook(); ws = wb.active
ws.append(["last_name", "first_name", "middle_name", "group_name", "user_type"])
ws.append(["Иванова", "Мария", "Петровна", "РПСб-23-1", "student"])
ws.append(["Сидоров", "Олег", None, None, "teacher"])
buf = io.BytesIO(); wb.save(buf)
r = ad.post("/v2/upload", files={"file": ("users.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
check("массовая загрузка показывает таблицу логинов и паролей", r.status_code == 200 and r.text.count("<td><code>") == 4, r.text.count("<td><code>"))

r = ad.post(f"/v2/users/{uid}/delete", follow_redirects=False)
check("удаление пользователя", r.status_code == 303 and db.execute("select count(*) from users where id=?", (uid,)).fetchone()[0] == 0)
aid = db.execute("select id from users where login=?", (al,)).fetchone()[0]
check("админ не может удалить себя", ad.post(f"/v2/users/{aid}/delete").status_code == 400)

# Сессия переживает «перезапуск»: новый процесс видит токен в БД
tok = ad.cookies.get("session_token")
import core.sessions as session_manager
importlib.reload(session_manager)
check("сессия хранится в БД (переживает перезапуск)", session_manager.get_user_from_session(tok) == al)
check("токен в БД хранится не в открытом виде", db.execute("select count(*) from sessions where token_hash=?", (tok,)).fetchone()[0] == 0)
ad.get("/logout")
check("logout удаляет сессию на сервере", session_manager.get_user_from_session(tok) is None)
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")

# --- смена собственного пароля ---
ok2 = True
c = TestClient(main.app); l, p = CREDS["student"][1]
login_as(c, l, p)
other = TestClient(main.app); login_as(other, l, p)
r = c.post("/account/password", data={"current_password": "wrong", "new_password": "NewPass123", "confirm_password": "NewPass123"})
check("смена пароля: неверный текущий", "неверно" in r.text)
r = c.post("/account/password", data={"current_password": p, "new_password": "short", "confirm_password": "short"})
check("смена пароля: слишком короткий", "не короче" in r.text)
r = c.post("/account/password", data={"current_password": p, "new_password": "NewPass123", "confirm_password": "NewPass124"})
check("смена пароля: не совпадают", "не совпадают" in r.text)
r = c.post("/account/password", data={"current_password": p, "new_password": "NewPass123", "confirm_password": "NewPass123"})
check("смена пароля: успех, пользователь остаётся в системе", r.status_code == 200 and "Пароль изменён" in r.text and c.get("/select", follow_redirects=False).status_code == 200)
check("другие сессии завершены", other.get("/select", follow_redirects=False).status_code == 303)
check("вход с новым паролем", login_as(TestClient(main.app), l, "NewPass123").status_code == 303)
check("страница логина без admin/admin123", "admin123" not in TestClient(main.app).get("/").text)
print("ИТОГ 2:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
