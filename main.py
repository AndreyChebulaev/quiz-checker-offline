"""Система автоматизированной проверки открытых вопросов с использованием машинного обучения.

Точка входа приложения. Запуск:
    uvicorn main:app --reload
"""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import STATIC_DIR
from core.database import init_db
from core.web import RedirectRequired
from routers import auth, editor, quiz, results, tests, users

init_db()

app = FastAPI(title="Проверка открытых вопросов")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth.router)
app.include_router(tests.router)
app.include_router(quiz.router)
app.include_router(results.router)
app.include_router(users.router)
app.include_router(editor.router)


@app.exception_handler(RedirectRequired)
async def handle_redirect_required(request: Request, exc: RedirectRequired):
    return RedirectResponse(url=exc.url, status_code=303)


@app.get("/admin/users", include_in_schema=False)
def legacy_admin_users():
    """Старая страница управления пользователями заменена разделом /v2."""
    return RedirectResponse(url="/v2/users", status_code=303)
