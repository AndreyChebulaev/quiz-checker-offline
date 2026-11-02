"""Пароли, сессии, вход и права на разделы."""
import importlib

from core.security import generate_password, hash_password, is_hashed, verify_password


def test_password_hashing():
    stored = hash_password("секрет-123")
    assert is_hashed(stored) and "секрет" not in stored
    assert verify_password("секрет-123", stored)
    assert not verify_password("секрет-124", stored)
    assert hash_password("секрет-123") != stored          # своя соль у каждого хеша
    assert not verify_password(stored, stored)             # хеш не подходит вместо пароля


def test_generated_passwords():
    passwords = {generate_password() for _ in range(50)}
    assert len(passwords) == 50
    assert all(len(p) == 10 and not set(p) & set("0O1lI") for p in passwords)


def test_no_default_admin_password_on_login_page(client_for):
    page = client_for().get("/").text
    assert "admin123" not in page


def test_all_passwords_hashed_in_db(db, users):
    assert all(is_hashed(row[0]) for row in db("SELECT password FROM users"))


def test_login_and_wrong_password(client_for, users):
    client = client_for()
    ok = client.post("/login", data={"username": "student1", "password": users["student1"]}, follow_redirects=False)
    cookie = ok.headers["set-cookie"].lower()
    assert ok.status_code == 303 and "httponly" in cookie and "samesite=lax" in cookie
    bad = client_for().post("/login", data={"username": "student1", "password": "wrong"})
    assert "Неверный логин или пароль" in bad.text


def test_login_lockout(client_for, users):
    from routers import auth
    for _ in range(auth.MAX_LOGIN_FAILURES):
        client_for().post("/login", data={"username": "student3", "password": "wrong"})
    blocked = client_for().post("/login", data={"username": "student3", "password": users["student3"]})
    assert "Слишком много" in blocked.text
    auth._login_failures.clear()


def test_session_survives_restart_and_logout(client_for):
    import core.sessions as sessions
    client = client_for("student1")
    token = client.cookies.get("session_token")
    importlib.reload(sessions)                             # «перезапуск» модуля
    assert sessions.get_user_from_session(token) == "student1"
    client.get("/logout")
    assert sessions.get_user_from_session(token) is None


def test_user_management_admin_only(client_for):
    anonymous, student, teacher, admin = client_for(), client_for("student1"), client_for("teacher1"), client_for("admin")
    assert anonymous.get("/v2/users", follow_redirects=False).headers["location"] == "/"
    assert student.get("/v2/users", follow_redirects=False).headers["location"] == "/select"
    assert teacher.get("/v2/users", follow_redirects=False).status_code == 303
    assert student.post("/v2/users/1/delete").status_code == 403
    assert admin.get("/v2/users").status_code == 200


def test_register_shows_password_once_and_hashes_it(client_for, db):
    admin = client_for("admin")
    page = admin.post("/v2/register", data={"user_type": "student", "last_name": "Новиков",
                                            "first_name": "Ник", "group_name": "ГР-3"}).text
    assert "Пароли показываются" in page
    login = db("SELECT login, password FROM users WHERE last_name = 'Новиков'")[0]
    assert is_hashed(login[1]) and login[1] not in page
    admin.post("/v2/register", data={"user_type": "admin", "last_name": "Злой", "first_name": "Админ"})
    assert db("SELECT COUNT(*) FROM users WHERE last_name = 'Злой'") == [(0,)]


def test_change_own_password(client_for, users, db):
    client = client_for("student2")
    other = client_for("student2")
    response = client.post("/account/password", data={
        "current_password": users["student2"], "new_password": "NewPass-77", "confirm_password": "NewPass-77"})
    assert "Пароль изменён" in response.text
    assert other.get("/select", follow_redirects=False).status_code == 303   # другие сессии завершены
    client.post("/account/password", data={
        "current_password": "NewPass-77", "new_password": users["student2"], "confirm_password": users["student2"]})
