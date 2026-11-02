"""Владельцы тестов и доступ преподавателей к чужим тестам и результатам."""
import os, sys, shutil, json, re
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/rubert-tiny2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
import sqlite3
pre = sqlite3.connect("users.db"); pre.execute("update users set password = 'pw_' || login")
pre.execute("update users set show_all_tests = 0") if "show_all_tests" in [r[1] for r in pre.execute("pragma table_info(users)")] else None
pre.execute("delete from test_shares") if pre.execute("select name from sqlite_master where name='test_shares'").fetchone() else None
pre.commit()
users = {}
for t, l in pre.execute("select user_type, login from users order by id"): users.setdefault(t, []).append(l)
pre.close()
T1, T2 = users["teacher"][0], users["teacher"][1]; ADMIN = users["admin"][0]; STUD = users["student"][0]
from fastapi.testclient import TestClient
import main
from services.ai_backend import ai_service
ai_service.score_answer = lambda ans, refs, emb: (float(ans.split()[0]), refs[0])
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)
def client(login):
    c = TestClient(main.app); c.post("/login", data={"username": login, "password": "pw_" + login}); return c
db = lambda q, *a: sqlite3.connect("users.db").execute(q, a).fetchall()
t1, t2, adm, st = client(T1), client(T2), client(ADMIN), client(STUD)

check("старые тесты достались администратору", db("select count(*) from test_settings where owner_login is null") == [(0,)]
      and db("select owner_login from test_settings where filename='QuestionsAnswers.xlsx'") == [(ADMIN,)])
check("преподаватель не видит тесты администратора", "QuestionsAnswers" not in t1.get("/select").text)
check("администратор видит свои старые тесты", "QuestionsAnswers" in adm.get("/select").text)

def save(c, filename, **extra):
    data = {"filename": filename, "questions": ["Q1", "Q2"], "answers": [json.dumps(["эталон"]), json.dumps(["другой"])]}
    data.update(extra); return c.post("/editor/save_edit", data=data)
r = save(t1, "new_file.xlsx", test_name="Тест первого")
check("преподаватель 1 создал тест", r.status_code == 200 and db("select owner_login from test_settings where filename='Тест первого.xlsx'") == [(T1,)], r.json())
F = "Тест первого.xlsx"
check("владелец видит свой тест", F.rsplit(".", 1)[0] in t1.get("/select").text)
check("второй преподаватель не видит чужой тест", "Тест первого" not in t2.get("/select").text)
check("администратор тоже не видит чужой тест", "Тест первого" not in adm.get("/select").text)
check("чужой тест не открывается в редакторе", t2.get(f"/editor/edit/{F}").status_code == 404)
check("чужой тест не скачивается", t2.get(f"/download/{F}").status_code == 404 and t2.get(f"/editor/download/{F}").status_code == 404)
check("чужой тест нельзя сохранить", save(t2, F, source="published").status_code == 404)
check("чужой тест нельзя удалить", t2.post("/delete_file", data={"filename": F}).json()["status"] == "error" and os.path.exists(f"uploaded_files/{F}"))
check("чужой тест нельзя начать", "Тест не найден" in t2.post("/select", data={"filename": F}).text)
import io
from openpyxl import Workbook
wb = Workbook(); wb.active.append(["Q", '"a"']); buf = io.BytesIO(); wb.save(buf)
t2.post("/editor/upload", files={"file": (F, buf.getvalue(), "application/octet-stream")})
check("импорт с именем чужого теста не затирает его", save(t2, F, source="import").status_code == 409)

# студент проходит тест первого
st.post("/select", data={"filename": F}); st.post("/answer", data={"idx": 0, "user_answer": "0.90 да"}); st.post("/answer", data={"idx": 1, "user_answer": "0.70 около"})
st.get("/final_results")
rid = db("select id from test_results where filename=? order by id desc", F)[0][0]
check("владелец видит результат", "Тест первого" in t1.get("/results").text and t1.get(f"/results/{rid}").status_code == 200)
r2 = t2.get("/results")
check("чужие результаты не видны в списке", "Тест первого" not in r2.text)
check("чужой результат не открывается", t2.get(f"/results/{rid}", follow_redirects=False).status_code in (303, 404))
check("чужой результат не выгружается", t2.get(f"/results/{rid}/export", follow_redirects=False).status_code in (303, 404))
check("общая выгрузка не содержит чужих", "Тест первого".encode() not in t2.get("/results/export").content)
check("счётчик проверки у владельца", re.search(r"Проверка\s*<span[^>]*>1</span>", t1.get("/select").text) is not None)
check("у второго счётчика нет", re.search(r"Проверка\s*<span", t2.get("/select").text) is None)
check("очередь второго пуста", "Нет ответов, ожидающих проверки" in t2.get("/review").text)
aid = db("select id from answer_results where result_id=? and auto_verdict='review'", rid)[0][0]
check("второй не может проверить чужой ответ", t2.post(f"/review/answers/{aid}", data={"verdict": "correct"}).status_code == 404)

page = t1.get(f"/editor/edit/{F}").text
check("владелец видит выпадающие списки коллег", 'id="share_edit_list"' in page and 'id="share_results_list"' in page and f'value="{T2}"' in page)
check("логины коллег не показываются", f"({T2}," not in page and f">{T2}<" not in page)
# открываем второму только результаты
r = save(t1, F, source="published", teacher_shares=json.dumps([{"login": T2, "can_edit": False, "can_view_results": True}]))
check("владелец открыл доступ к результатам", r.status_code == 200 and db("select can_edit, can_view_results from test_shares where filename=? and teacher_login=?", F, T2) == [(0, 1)])
check("второй видит результаты", "Тест первого" in t2.get("/results").text and t2.get(f"/results/{rid}").status_code == 200)
check("второй может проверить ответ", t2.post(f"/review/answers/{aid}", data={"verdict": "correct"}).status_code == 200)
check("но тест в списке и редакторе не виден", "Тест первого" not in t2.get("/select").text and t2.get(f"/editor/edit/{F}").status_code == 404)
# только редактирование
save(t1, F, source="published", teacher_shares=json.dumps([{"login": T2, "can_edit": True, "can_view_results": False}]))
check("второй видит тест с пометкой владельца", "Владелец:" in t2.get("/select").text and "Тест первого" in t2.get("/select").text)
p2 = t2.get(f"/editor/edit/{F}").text
check("второй открывает редактор, блока настройки доступа нет", "Владелец теста:" in p2 and 'id="share_edit_list"' not in p2)
r = save(t2, F, source="published", teacher_shares=json.dumps([{"login": T2, "can_edit": True, "can_view_results": True}]))
check("второй сохраняет правки", r.status_code == 200)
check("но не может выдать себе доступ к результатам", db("select can_view_results from test_shares where filename=? and teacher_login=?", F, T2) == [(0,)])
check("результаты теперь закрыты", "Тест первого" not in t2.get("/results").text)
check("второй не может переименовать", save(t2, F, source="published", test_name="Захват").status_code == 403)
r = t2.post("/delete_file", data={"filename": F})
check("второй не может удалить", r.status_code == 403 and os.path.exists(f"uploaded_files/{F}"))
# переименование владельцем сохраняет доступ
r = save(t1, F, source="published", test_name="Тест первого v2")
F2 = "Тест первого v2.xlsx"
check("переименование сохраняет владельца и доступ", r.status_code == 200 and db("select owner_login from test_settings where filename=?", F2) == [(T1,)]
      and db("select can_edit from test_shares where filename=?", F2) == [(1,)])
check("история результатов переехала", db("select count(*) from test_results where filename=?", F2) == [(1,)])
# снять доступ
save(t1, F2, source="published", teacher_shares="[]")
check("доступ снят", db("select count(*) from test_shares where filename=?", F2) == [(0,)] and "Тест первого" not in t2.get("/select").text)
check("нельзя выдать доступ студенту", (save(t1, F2, source="published", teacher_shares=json.dumps([{"login": STUD, "can_edit": True}])), db("select count(*) from test_shares where filename=?", F2))[1] == [(0,)])
check("студент по-прежнему видит тест", "Тест первого v2" in st.get("/select").text)
r = t1.post("/delete_file", data={"filename": F2})
check("владелец удаляет тест", r.json()["status"] == "success" and not os.path.exists(f"uploaded_files/{F2}"))
check("после удаления результаты у владельца остались", "Тест первого v2" in t1.get("/results").text)

# --- администратор ---
F3 = "Тест для админа.xlsx"
save(t1, "new_file.xlsx", test_name="Тест для админа")
check("в режиме «Мои» админ не видит чужие тесты", "Тест для админа" not in adm.get("/select").text)
r = adm.get("/scope?show=all", headers={"referer": "http://testserver/results?q=x"}, follow_redirects=False)
check("переключатель возвращает на ту же страницу", r.status_code == 303 and r.headers["location"] == "/results?q=x", r.headers.get("location"))
check("в режиме «Все» админ видит чужой тест с владельцем", "Тест для админа" in adm.get("/select").text and "Владелец:" in adm.get("/select").text)
check("переключатель отмечен", 'href="/scope?show=all" class="is-active"' in adm.get("/select").text)
check("админ видит чужие результаты", "Тест первого" in adm.get("/results").text)
check("админ открывает чужой тест в редакторе", adm.get(f"/editor/edit/{F3}").status_code == 200)
pa = adm.get(f"/editor/edit/{F3}").text
check("у админа есть выбор владельца", 'id="test_owner"' in pa)
r = save(adm, F3, source="published", new_owner=T2)
check("админ передал тест другому преподавателю", r.status_code == 200 and db("select owner_login from test_settings where filename=?", F3) == [(T2,)], r.json())
check("новый владелец видит тест, старый — нет", "Тест для админа" in t2.get("/select").text and "Тест для админа" not in t1.get("/select").text)
check("преподаватель не может передать тест", (save(t2, F3, source="published", new_owner=T1), db("select owner_login from test_settings where filename=?", F3))[1] == [(T2,)])
check("студент не может включить «Все тесты»", (st.get("/scope?show=all"), db("select show_all_tests from users where login=?", STUD))[1] == [(0,)])
check("переключатель студенту не показывается", 'class="scope-switch"' not in st.get("/select").text)
check("чужой referer игнорируется", adm.get("/scope?show=mine", headers={"referer": "http://evil.example//evil"}, follow_redirects=False).headers["location"] in ("/select", "//evil") and
      not adm.get("/scope?show=mine", headers={"referer": "http://evil.example//evil"}, follow_redirects=False).headers["location"].startswith("//"))
check("в режиме «Мои» снова только своё", "Тест для админа" not in adm.get("/select").text)
check("админ может удалить чужой тест", adm.post("/delete_file", data={"filename": F3}).json()["status"] == "success")

# --- закрыть тест для всех студентов ---
F4 = "Черновик.xlsx"
save(t1, "new_file.xlsx", test_name="Черновик")
check("новый тест открыт студентам", "Черновик" in st.get("/select").text)
check("в редакторе переключатель включён", 'id="students_open" checked' in t1.get(f"/editor/edit/{F4}").text)
r = save(t1, F4, source="published", students_closed="1")
check("тест закрыт для студентов", r.status_code == 200 and db("select students_closed from test_settings where filename=?", F4) == [(1,)])
check("студент не видит закрытый тест", "Черновик" not in st.get("/select").text)
check("студент не может начать закрытый тест", "закрыт преподавателем" in st.post("/select", data={"filename": F4}).text)
check("владелец видит закрытый тест с пометкой", "Скрыт от студентов" in t1.get("/select").text)
check("переключатель в редакторе выключен", 'id="students_open" checked' not in t1.get(f"/editor/edit/{F4}").text)
save(t1, F4, source="published", teacher_shares=json.dumps([{"login": T2, "can_edit": True}]))
check("закрытый тест виден преподавателю с доступом", "Черновик" in t2.get("/select").text)
check("сохранение без поля не открывает тест", (save(t1, F4, source="published"), db("select students_closed from test_settings where filename=?", F4))[1] == [(1,)])
save(t1, F4, source="published", students_closed="0")
check("тест снова открыт студентам", "Черновик" in st.get("/select").text)
save(t1, F4, source="published", students_closed="1", test_name="Черновик 2")
check("закрытие сохраняется при переименовании", db("select students_closed from test_settings where filename='Черновик 2.xlsx'") == [(1,)])
check("переключатель «Мои/Все» не в шапке", 'class="scope-switch"' not in t1.get("/select").text)
adm.get("/scope?show=mine")
check("переключатель у админа на списке, результатах и проверке",
      all('class="scope-switch"' in adm.get(u).text for u in ["/select", "/results", "/review"]))
check("но не в редакторе", 'class="scope-switch"' not in adm.get("/editor/create-new").text)
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
