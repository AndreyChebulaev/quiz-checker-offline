"""Ручная проверка спорных ответов и порог в настройках теста."""
from conftest import create_test, save_test, take_test


def _pending(db, name):
    return db("SELECT a.id FROM answer_results a JOIN test_results r ON r.id = a.result_id "
              "WHERE r.filename = ? AND a.auto_verdict = 'review' AND a.final_verdict IS NULL", name)


def test_review_flow(client_for, controlled_scores, db):
    teacher = client_for("teacher1")
    name = create_test(teacher, "Проверка поток", [("Q1", ["эталон"]), ("Q2", ["от фактов к цели"])])
    take_test(client_for("student1"), name, ["0.70 около порога", "0.90 не от фактов к цели"])
    pending = _pending(db, name)
    assert len(pending) == 2
    queue = teacher.get("/review").text
    assert "Ближайший эталон" in queue and "отрицанием" in queue

    result = teacher.post(f"/review/answers/{pending[0][0]}", data={"verdict": "correct"}).json()
    assert result["total_correct"] == 1 and result["pending_review"] == 1
    result = teacher.post(f"/review/answers/{pending[1][0]}", data={"verdict": "incorrect"}).json()
    assert result["pending_review"] == 0
    result = teacher.post(f"/review/answers/{pending[0][0]}", data={"verdict": "auto"}).json()
    assert result["verdict"] == "review" and result["pending_review"] == 1
    assert teacher.post(f"/review/answers/{pending[0][0]}", data={"verdict": "maybe"}).status_code == 400


def test_students_cannot_review(client_for, controlled_scores, db):
    name = create_test(client_for("teacher1"), "Проверка права", [("Q", ["эталон"])])
    take_test(client_for("student1"), name, ["0.70 около"])
    answer_id = _pending(db, name)[0][0]
    student = client_for("student1")
    assert student.get("/review", follow_redirects=False).status_code == 303
    assert student.post(f"/review/answers/{answer_id}", data={"verdict": "correct"}).status_code == 403


def test_test_threshold_settings(client_for, controlled_scores, db):
    teacher = client_for("teacher1")
    questions = [("Q1", ["a"]), ("Q2", ["b"])]
    name = create_test(teacher, "Проверка порог", questions)
    assert save_test(teacher, name, questions, source="published", threshold="2").status_code == 400
    assert save_test(teacher, name, questions, source="published", threshold="0,95", review_margin="0").status_code == 200
    take_test(client_for("student2"), name, ["0.90 a", "0.96 b"])
    verdicts = db("SELECT a.auto_verdict FROM answer_results a JOIN test_results r ON r.id = a.result_id "
                  "WHERE r.filename = ? ORDER BY a.question_index", name)
    assert [v[0] for v in verdicts] == ["incorrect", "correct"]
