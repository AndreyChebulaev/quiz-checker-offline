"""Владельцы тестов, доступ преподавателей, закрытие теста для студентов, режим администратора."""
import json

from conftest import create_test, save_test, take_test

Q = [("Q1", ["эталон"])]


def test_teacher_sees_only_own_tests(client_for):
    first, second = client_for("teacher1"), client_for("teacher2")
    name = create_test(first, "Доступ свой")
    assert "Доступ свой" in first.get("/select").text
    assert "Доступ свой" not in second.get("/select").text
    assert second.get(f"/editor/edit/{name}").status_code == 404
    assert second.get(f"/download/{name}").status_code == 404
    assert save_test(second, name, Q, source="published").status_code == 404
    assert second.post("/delete_file", data={"filename": name}).json()["status"] == "error"


def test_results_visible_only_to_owner(client_for, db):
    first, second = client_for("teacher1"), client_for("teacher2")
    name = create_test(first, "Доступ результаты", Q)
    take_test(client_for("student1"), name, ["эталон"])
    result_id = db("SELECT id FROM test_results WHERE filename = ?", name)[0][0]
    assert "Доступ результаты" in first.get("/results").text
    assert "Доступ результаты" not in second.get("/results").text
    assert second.get(f"/results/{result_id}", follow_redirects=False).status_code != 200


def test_share_results_and_edit_separately(client_for, db):
    first, second = client_for("teacher1"), client_for("teacher2")
    name = create_test(first, "Доступ общий", Q)
    take_test(client_for("student1"), name, ["эталон"])

    save_test(first, name, Q, source="published",
              teacher_shares=json.dumps([{"login": "teacher2", "can_view_results": True}]))
    assert "Доступ общий" in second.get("/results").text
    assert "Доступ общий" not in second.get("/select").text

    save_test(first, name, Q, source="published",
              teacher_shares=json.dumps([{"login": "teacher2", "can_edit": True}]))
    assert "Доступ общий" in second.get("/select").text
    assert "Доступ общий" not in second.get("/results").text
    assert save_test(second, name, Q, source="published").status_code == 200
    assert save_test(second, name, Q, source="published", test_name="Захват").status_code == 403
    assert second.post("/delete_file", data={"filename": name}).status_code == 403
    # не владелец не может менять доступ
    save_test(second, name, Q, source="published",
              teacher_shares=json.dumps([{"login": "teacher2", "can_edit": True, "can_view_results": True}]))
    assert db("SELECT can_view_results FROM test_shares WHERE filename = ?", name) == [(0,)]


def test_rename_keeps_owner_shares_and_history(client_for, db):
    first = client_for("teacher1")
    name = create_test(first, "Доступ имя", Q)
    save_test(first, name, Q, source="published", teacher_shares=json.dumps([{"login": "teacher2", "can_edit": True}]))
    take_test(client_for("student1"), name, ["эталон"])
    response = save_test(first, name, Q, source="published", test_name="Доступ имя 2")
    new = response.json()["filename"]
    assert db("SELECT owner_login FROM test_settings WHERE filename = ?", new) == [("teacher1",)]
    assert db("SELECT COUNT(*) FROM test_shares WHERE filename = ?", new) == [(1,)]
    assert db("SELECT COUNT(*) FROM test_results WHERE filename = ?", new) == [(1,)]


def test_close_test_for_students(client_for):
    teacher, student = client_for("teacher1"), client_for("student1")
    name = create_test(teacher, "Доступ черновик", Q)
    assert "Доступ черновик" in student.get("/select").text
    save_test(teacher, name, Q, source="published", students_closed="1")
    assert "Доступ черновик" not in student.get("/select").text
    assert "закрыт преподавателем" in student.post("/select", data={"filename": name}).text
    assert "Скрыт от студентов" in teacher.get("/select").text
    save_test(teacher, name, Q, source="published", students_closed="0")
    assert "Доступ черновик" in student.get("/select").text


def test_group_access(client_for):
    teacher = client_for("teacher1")
    create_test(teacher, "Доступ группа", Q, allowed_groups="ГР-2")
    assert "Доступ группа" in client_for("student3").get("/select").text     # ГР-2
    assert "Доступ группа" not in client_for("student1").get("/select").text # ГР-1


def test_admin_scope_and_transfer(client_for, db):
    teacher, admin = client_for("teacher1"), client_for("admin")
    name = create_test(teacher, "Доступ админ", Q)
    admin.get("/scope?show=mine")
    assert "Доступ админ" not in admin.get("/select").text
    admin.get("/scope?show=all")
    assert "Доступ админ" in admin.get("/select").text
    assert 'class="scope-switch"' in admin.get("/results").text
    assert 'class="scope-switch"' not in teacher.get("/select").text
    save_test(admin, name, Q, source="published", new_owner="teacher2")
    assert db("SELECT owner_login FROM test_settings WHERE filename = ?", name) == [("teacher2",)]
    admin.get("/scope?show=mine")
