"""Проверка преподавателем ответов, которые система отправила на ручное решение."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from core.users_repo import get_user_full_info, get_user_permissions
from core.web import RedirectRequired, get_template_context, get_user_from_session, templates
from services.review import get_review_queue, set_answer_verdict


def require_teacher(request: Request) -> str:
    """Зависимость: только преподаватель или администратор."""
    login = get_user_from_session(request)
    if not login:
        raise RedirectRequired("/")
    user_info = get_user_full_info(login)
    if not user_info or not get_user_permissions(user_info["user_type"])["can_edit_tests"]:
        if request.method == "GET":
            raise RedirectRequired("/select")
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    return login


router = APIRouter(prefix="/review")


@router.get("", response_class=HTMLResponse)
def review_queue_page(request: Request, test: str = "", login: str = Depends(require_teacher)):
    queue = get_review_queue(test_filter=test)
    context = get_template_context(request)
    context.update({
        "request": request,
        "items": queue["items"],
        "tests": queue["tests"],
        "test_files": queue["test_files"],
        "test_filter": test,
    })
    return templates.TemplateResponse("review.html", context)


@router.post("/answers/{answer_id}")
def review_answer(answer_id: int, verdict: str = Form(...), login: str = Depends(require_teacher)):
    """verdict: correct / incorrect / auto (вернуть автоматическое решение)."""
    try:
        result = set_answer_verdict(answer_id, None if verdict == "auto" else verdict, login)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Ответ не найден")
    return JSONResponse(result)
