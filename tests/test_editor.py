"""Редактор: создание, импорт из Excel, переименование."""
import os

from conftest import create_test, make_xlsx, save_test

Q = [("Что такое ML?", ["машинное обучение"])]


def test_new_test_needs_name_and_unique(client_for):
    teacher = client_for("teacher1")
    assert save_test(teacher, "new_file.xlsx", Q, test_name="").status_code == 400
    create_test(teacher, "Редактор уникальный", Q)
    assert save_test(teacher, "new_file.xlsx", Q, test_name="Редактор уникальный").status_code == 409


def test_unsafe_name_is_cleaned(client_for):
    response = save_test(client_for("teacher1"), "new_file.xlsx", Q, test_name="../../Редактор опасный")
    filename = response.json()["filename"]
    assert "/" not in filename and os.path.exists(os.path.join("uploaded_files", filename))


def test_import_from_excel_and_publish(client_for):
    teacher = client_for("teacher1")
    page = teacher.post("/editor/upload", files={"file": ("Редактор импорт.xlsx", make_xlsx(Q), "application/octet-stream")})
    assert page.status_code == 200 and "Что такое ML" in page.text
    assert not os.path.exists("uploaded_files/Редактор импорт.xlsx")
    response = save_test(teacher, "Редактор импорт.xlsx", Q, source="import", test_name="Редактор опубликован")
    assert response.status_code == 200
    assert "Редактор опубликован" in teacher.get("/select").text


def test_rename_and_case_change(client_for):
    teacher = client_for("teacher1")
    name = create_test(teacher, "редактор регистр", Q)
    response = save_test(teacher, name, Q, source="published", test_name="Редактор Регистр")
    assert response.status_code == 200
    assert os.path.exists("uploaded_files/Редактор Регистр.xlsx")
    assert not os.path.exists("uploaded_files/редактор регистр.xlsx")


def test_student_cannot_use_editor(client_for):
    assert client_for("student1").get("/editor/", follow_redirects=False).status_code == 303


def test_path_traversal_rejected(client_for):
    assert client_for("teacher1").get("/download/..%2Fusers.db").status_code in (400, 404)


def test_case_only_rename_on_case_insensitive_fs(client_for, monkeypatch, db):
    """В Windows «тест.xlsx» и «Тест.xlsx» — один файл. Эмулируем такую файловую систему."""
    from pathlib import Path

    import routers.editor as editor

    def case_insensitive_path(name):
        for existing in os.listdir("uploaded_files"):
            if existing.casefold() == name.casefold():
                return Path("uploaded_files") / existing
        return Path("uploaded_files") / name

    teacher = client_for("teacher1")
    name = create_test(teacher, "редактор windows", Q)
    monkeypatch.setattr(editor, "get_saved_file_path", case_insensitive_path)
    response = save_test(teacher, name, Q, source="published", test_name="Редактор Windows")
    assert response.status_code == 200, response.json()
    assert db("SELECT owner_login FROM test_settings WHERE filename = 'Редактор Windows.xlsx'") == [("teacher1",)]
