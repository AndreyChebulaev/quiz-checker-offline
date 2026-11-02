"""Общие фикстуры тестов.

Тесты не трогают рабочую базу и файлы: приложение запускается в отдельной временной
папке. Вместо нейросети подключается упрощённая модель (FakeSentenceTransformer), поэтому
тесты идут за секунды и без папки local_models. Проверяется логика приложения, а не
качество модели — его измеряет эксперимент (experiments/).
"""
import importlib
import io
import json
import os
import shutil
import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest

PROJECT = Path(__file__).resolve().parent.parent
ADMIN_PASSWORD = "Admin-pass-123"


# ---------------------------------------------------------------- заглушка модели
class FakeSentenceTransformer:
    """Эмбеддинг — мешок символьных триграмм: похожие строки дают высокое сходство."""
    DIM = 512

    def __init__(self, *args, **kwargs):
        pass

    def _vec(self, text):
        text = f"  {str(text).lower()}  "
        vec = np.zeros(self.DIM, dtype=np.float32)
        for i in range(len(text) - 2):
            vec[sum(map(ord, text[i:i + 3])) % self.DIM] += 1
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def encode(self, texts, **kwargs):
        if isinstance(texts, str):
            return self._vec(texts)
        return np.stack([self._vec(t) for t in texts])


def _fake_cos_sim(a, b):
    return np.atleast_2d(a) @ np.atleast_2d(b).T


fake_module = types.ModuleType("sentence_transformers")
fake_module.SentenceTransformer = FakeSentenceTransformer
fake_module.util = types.SimpleNamespace(cos_sim=_fake_cos_sim)


# ---------------------------------------------------------------- приложение
@pytest.fixture(scope="session")
def workdir(tmp_path_factory):
    """Временная папка с шаблонами и статикой; рабочая папка процесса переключается на неё."""
    root = tmp_path_factory.mktemp("app")
    shutil.copytree(PROJECT / "templates", root / "templates")
    shutil.copytree(PROJECT / "static", root / "static")
    old_cwd = os.getcwd()
    os.chdir(root)
    yield root
    os.chdir(old_cwd)


@pytest.fixture(scope="session")
def app_module(workdir):
    sys.modules["sentence_transformers"] = fake_module
    os.environ["QUIZ_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    sys.path.insert(0, str(PROJECT))
    for name in list(sys.modules):
        if name in ("main", "config") or name.split(".")[0] in ("core", "services", "routers"):
            del sys.modules[name]
    main = importlib.import_module("main")
    return main


@pytest.fixture(scope="session")
def users(app_module):
    """Тестовые пользователи с известными паролями: логин -> пароль."""
    from core.security import hash_password
    people = [
        ("teacher", "Петров", "Пётр", "Петрович", None, "teacher1"),
        ("teacher", "Сидорова", "Анна", "Игоревна", None, "teacher2"),
        ("student", "Иванов", "Иван", None, "ГР-1", "student1"),
        ("student", "Смирнова", "Ольга", None, "ГР-1", "student2"),
        ("student", "Кузнецов", "Олег", None, "ГР-2", "student3"),
    ]
    with sqlite3.connect("users.db") as conn:
        for user_type, last, first, middle, group, login in people:
            conn.execute(
                "INSERT OR IGNORE INTO users (user_type, last_name, first_name, middle_name, group_name, login, password)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_type, last, first, middle, group, login, hash_password(f"pw-{login}")),
            )
    creds = {login: f"pw-{login}" for *_, login in people}
    creds["admin"] = ADMIN_PASSWORD
    return creds


@pytest.fixture
def client_for(app_module, users):
    """client_for("teacher1") — клиент, уже вошедший под этим пользователем."""
    from fastapi.testclient import TestClient

    def make(login=None):
        client = TestClient(app_module.app)
        if login:
            response = client.post("/login", data={"username": login, "password": users[login]},
                                   follow_redirects=False)
            assert response.status_code == 303, f"не удалось войти как {login}"
        return client
    return make


@pytest.fixture
def controlled_scores(app_module):
    """Оценка сходства берётся из начала ответа: «0.90 текст» -> 0.90."""
    from services.ai_backend import ai_service
    original = ai_service.score_answer

    def score(answer, references, embeddings):
        head = answer.split()[0] if answer.split() else ""
        try:
            return float(head), references[0]
        except ValueError:
            return original(answer, references, embeddings)
    ai_service.score_answer = score
    yield
    ai_service.score_answer = original


@pytest.fixture
def db():
    def query(sql, *params):
        with sqlite3.connect("users.db") as conn:
            return conn.execute(sql, params).fetchall()
    return query


# ---------------------------------------------------------------- помощники
def make_xlsx(rows):
    from openpyxl import Workbook
    workbook = Workbook()
    for question, answers in rows:
        workbook.active.append([question, ",".join(f'"{a}"' for a in answers)])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def save_test(client, filename, questions, test_name="", source="", **settings):
    """Сохранение теста через редактор, как это делает страница (ответы — JSON-списки)."""
    data = {
        "filename": filename,
        "questions": [q for q, _ in questions],
        "answers": [json.dumps(a, ensure_ascii=False) for _, a in questions],
        "test_name": test_name,
        "source": source,
    }
    data.update(settings)
    return client.post("/editor/save_edit", data=data)


def create_test(client, name, questions=None, **settings):
    questions = questions or [("Вопрос 1", ["эталон один"]), ("Вопрос 2", ["эталон два"])]
    response = save_test(client, "new_file.xlsx", questions, test_name=name, **settings)
    assert response.status_code == 200, response.json()
    return response.json()["filename"]


def take_test(client, filename, answers):
    response = client.post("/select", data={"filename": filename}, follow_redirects=False)
    assert response.status_code == 303, response.text[:300]
    for index, answer in enumerate(answers):
        assert client.post("/answer", data={"idx": index, "user_answer": answer}).status_code == 200
    return client.get("/final_results")
