"""Этап 5: серая зона, отрицания, проверка преподавателем, порог теста."""
import os, sys, shutil, io, re, json
SRC = sys.argv[1]; WORK = os.path.expanduser("~/smoke")
shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns(".venv", "local_models", "_backup", ".git", "__pycache__"))
os.makedirs(os.path.join(WORK, "local_models/rubert-tiny2"), exist_ok=True)
os.chdir(WORK); sys.path.insert(0, WORK)
import sqlite3
pre = sqlite3.connect("users.db"); pre.execute("update users set password = 'pw_' || login"); pre.commit()
first = {}
for t, l, g in pre.execute("select user_type, login, group_name from users order by id"): first.setdefault(t, (l, g))
pre.close()
from openpyxl import Workbook
wb = Workbook(); ws = wb.active
for q, a in [("Q1", '"эталон один"'), ("Q2", '"эталон два"'), ("Q3", '"эталон три"'), ("Q4", '"от фактов к цели"'), ("Q5", '"эталон пять"')]:
    ws.append([q, a])
wb.save("uploaded_files/Проверка.xlsx")

from fastapi.testclient import TestClient
import main
from services.ai_backend import ai_service, THRESHOLD
# Управляемая оценка: число в начале ответа и есть «сходство»
ai_service.score_answer = lambda ans, refs, emb: (float(ans.split()[0]), refs[0])
ok = True
def check(name, cond, extra=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name, extra); ok &= bool(cond)
def client(t):
    c = TestClient(main.app); l = first[t][0]
    c.post("/login", data={"username": l, "password": "pw_" + l}); return c
db = lambda q, *a: sqlite3.connect("users.db").execute(q, a).fetchall()
print("порог модели:", THRESHOLD)

st, te = client("student"), client("teacher")
def take(answers):
    r = st.post("/select", data={"filename": "Проверка.xlsx"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    for i, a in enumerate(answers): st.post("/answer", data={"idx": i, "user_answer": a})
    return st.get("/final_results")
r = take(["0.90 верно", "0.50 неверно", "0.70 около порога", "0.90 не от фактов к цели", "0.95 верно"])
check("итоговая страница открывается", r.status_code == 200)
check("предупреждение о проверке у студента", "Ответов на проверке у преподавателя: <strong>2</strong>" in r.text)
check("чипы решений", r.text.count("verdict-chip correct") == 2 and r.text.count("verdict-chip review") == 2 and r.text.count("verdict-chip incorrect") == 1)
check("причина: отрицание", "отличается отрицанием" in r.text)
res = db("select id, total_correct, pending_review, percentage from test_results order by id desc limit 1")[0]
rid = res[0]
check("в БД: 2 верных, 2 на проверке, 40%", res[1:] == (2, 2, 40.0), res)
rows = db("select question_index, auto_verdict, round(score,2) from answer_results where result_id=? order by question_index", rid)
check("оценки ответов сохранены", [r[1] for r in rows] == ["correct", "incorrect", "review", "review", "correct"], rows)
r2 = st.get("/final_results")
check("повторное открытие итогов не дублирует запись", db("select count(*) from test_results where id>=?", rid) == [(1,)] and r2.status_code == 200)

check("студент не видит очередь проверки", st.get("/review", follow_redirects=False).status_code == 303)
aid = db("select id from answer_results where result_id=? and question_index=2", rid)[0][0]
check("студент не может выставить оценку", st.post(f"/review/answers/{aid}", data={"verdict": "correct"}).status_code == 403)
page = te.get("/select").text
check("счётчик в меню преподавателя", re.search(r"Проверка\s*<span[^>]*>2</span>", page) is not None)
q = te.get("/review")
check("очередь показывает 2 ответа", q.status_code == 200 and q.text.count("<article class=\"answer-item\" data-answer-card>") == 2, q.status_code)
check("в очереди виден эталон", "Ближайший эталон" in q.text)

r = te.post(f"/review/answers/{aid}", data={"verdict": "correct"}).json()
check("засчитать: итог пересчитан", r["total_correct"] == 3 and r["pending_review"] == 1 and r["percentage_display"] == "60.0", r)
aid2 = db("select id from answer_results where result_id=? and question_index=3", rid)[0][0]
r = te.post(f"/review/answers/{aid2}", data={"verdict": "incorrect"}).json()
check("не засчитать: проверено всё", r["total_correct"] == 3 and r["pending_review"] == 0, r)
check("очередь пуста", "Нет ответов, ожидающих проверки" in te.get("/review").text)
check("кто проверил — записано", db("select reviewed_by from answer_results where id=?", aid) == [(first["teacher"][0],)])
aid0 = db("select id from answer_results where result_id=? and question_index=1", rid)[0][0]
r = te.post(f"/review/answers/{aid0}", data={"verdict": "correct"}).json()
check("преподаватель может исправить и автоматическое решение", r["total_correct"] == 4, r)
r = te.post(f"/review/answers/{aid0}", data={"verdict": "auto"}).json()
check("вернуть автоматическое решение", r["total_correct"] == 3 and r["verdict"] == "incorrect", r)
check("неверное значение решения", te.post(f"/review/answers/{aid0}", data={"verdict": "maybe"}).status_code == 400)

d = st.get(f"/results/{rid}").text
check("студент видит решения в деталях", "verdict-chip correct" in d and "Засчитать" not in d and "Ближайший эталон" not in d)
d = te.get(f"/results/{rid}").text
check("преподаватель видит кнопки и эталоны", "Засчитать" in d and "Ближайший эталон" in d and "решение преподавателя" in d)
r = st.get("/final_results")
check("итоги студента обновились после проверки", "Ответов на проверке" not in r.text and r.text.count("verdict-chip correct") == 3)
x = te.get(f"/results/{rid}/export")
from openpyxl import load_workbook
w = load_workbook(io.BytesIO(x.content)); cells = [str(c.value) for row in w["Ответы"].iter_rows() for c in row]
check("Excel: решения в выгрузке", "Верно (решение преподавателя)" in cells and "Неверно" in cells, cells[:12])

# порог в настройках теста
def save(**extra):
    data = {"filename": "Проверка.xlsx", "source": "published",
            "questions": ["Q1", "Q2", "Q3", "Q4", "Q5"],
            "answers": [json.dumps([a], ensure_ascii=False) for a in ["эталон один", "эталон два", "эталон три", "от фактов к цели", "эталон пять"]]}
    data.update(extra); return te.post("/editor/save_edit", data=data)
check("порог 2 отклоняется", save(threshold="2").status_code == 400)
check("ширина 0.6 отклоняется", save(review_margin="0.6").status_code == 400)
r = save(threshold="0,95", review_margin="0")
check("порог 0,95 (с запятой) и без серой зоны сохранены", r.status_code == 200 and db("select threshold, review_margin from test_settings where filename='Проверка.xlsx'") == [(0.95, 0.0)], r.json())
check("значения подставляются в форму", 'value="0.95"' in te.get("/editor/edit/Проверка.xlsx").text)
take(["0.90 a", "0.96 b", "0.94 c", "0.99 d", "0.99 e"])
rid2 = db("select id from test_results order by id desc limit 1")[0][0]
check("строгий порог теста применяется", [r[0] for r in db("select auto_verdict from answer_results where result_id=? order by question_index", rid2)]
      == ["incorrect", "correct", "incorrect", "correct", "correct"])
check("порог попытки записан в результат", db("select threshold from test_results where id=?", rid2) == [(0.95,)])
r = save(threshold="", review_margin="")
check("пустые поля = значения по умолчанию", db("select threshold, review_margin from test_settings where filename='Проверка.xlsx'") == [(None, None)])
print("ИТОГ:", "ВСЁ ОК" if ok else "ЕСТЬ ОШИБКИ")
