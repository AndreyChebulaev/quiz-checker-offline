"""Прохождение теста, итоги, изоляция студентов."""
from conftest import create_test, take_test


def test_take_test_and_scores(client_for, controlled_scores, db):
    name = create_test(client_for("teacher1"), "Квиз базовый",
                       [("Q1", ["эталон один"]), ("Q2", ["эталон два"]), ("Q3", ["эталон три"])])
    page = take_test(client_for("student1"), name, ["0.95 верно", "0.30 мимо", "0.69 около"])
    assert page.status_code == 200
    assert page.text.count("verdict-chip correct") == 1
    assert page.text.count("verdict-chip incorrect") == 1
    assert page.text.count("verdict-chip review") == 1
    total, correct, pending = db("SELECT total_questions, total_correct, pending_review FROM test_results "
                                 "WHERE filename = ? ORDER BY id DESC", name)[0]
    assert (total, correct, pending) == (3, 1, 1)


def test_cannot_finish_with_unanswered(client_for):
    name = create_test(client_for("teacher1"), "Квиз пропуски")
    student = client_for("student1")
    student.post("/select", data={"filename": name})
    student.post("/answer", data={"idx": 0, "user_answer": "ответ"})
    response = student.get("/final_results", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/quiz?idx=1"


def test_two_students_do_not_mix(client_for):
    teacher = client_for("teacher1")
    first = create_test(teacher, "Квиз А", [("Столица Франции?", ["Париж"])])
    second = create_test(teacher, "Квиз Б", [("Автор Онегина?", ["Пушкин"]), ("Река?", ["Лена"])])
    a, b = client_for("student1"), client_for("student2")
    a.post("/select", data={"filename": first})
    b.post("/select", data={"filename": second})
    a.post("/answer", data={"idx": 0, "user_answer": "Париж"})
    b.post("/answer", data={"idx": 0, "user_answer": "Пушкин"})
    assert "Столица Франции" in a.get("/quiz?idx=0").text
    assert "Онегина" in b.get("/quiz?idx=0").text
    assert a.get("/check_completion").json()["total_questions"] == 1
    assert b.get("/check_completion").json()["unanswered"] == [2]


def test_answers_rejected_after_finish(client_for):
    name = create_test(client_for("teacher1"), "Квиз завершение", [("Q", ["эталон"])])
    student = client_for("student1")
    take_test(student, name, ["эталон"])
    assert student.post("/answer", data={"idx": 0, "user_answer": "другое"}).status_code == 409


def test_quiz_without_start(client_for):
    assert "Тест не начат" in client_for("student3").get("/quiz?idx=0").text


def test_attempt_limit(client_for):
    name = create_test(client_for("teacher1"), "Квиз одна попытка", [("Q", ["эталон"])], max_attempts="1")
    student = client_for("student3")
    take_test(student, name, ["эталон"])
    assert "лимит попыток исчерпан" in student.post("/select", data={"filename": name}).text
