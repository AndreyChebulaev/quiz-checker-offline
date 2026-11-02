"""Прохождение теста и подсчёт итогов с помощью ML-модели."""
from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.web import get_template_context, get_user_from_session, templates
from routers.common import render_select_with_error
from services.ai_backend import THRESHOLD, ai_service, ai_status, build_local_ai_help_text
from services.formatting import format_datetime_ru, format_duration
from services.quiz import get_active_quiz, get_quiz_restriction_status, get_unanswered_indexes
from services.results import save_test_result
from services.review import get_answer_results, save_answer_results
from services.scoring import VERDICT_CORRECT, VERDICT_LABELS, VERDICT_REVIEW, evaluate_answer
from services.test_settings import get_scoring_settings

router = APIRouter()


# Эндпоинты теста (требуют авторизации)
@router.get("/quiz", response_class=HTMLResponse)
def quiz_form(request: Request, idx: int = 0):
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)
    

    restriction_status = get_quiz_restriction_status(request)
    if restriction_status["error"]:
        return render_select_with_error(request, restriction_status["error"])
    
    quiz_state = get_active_quiz(request)
    if not quiz_state:
        return render_select_with_error(request, "Тест не начат. Выберите тест из списка.")
    questions = quiz_state["questions"]
    if idx < 0:
        return RedirectResponse(url="/quiz?idx=0", status_code=303)
    if idx >= len(questions):
        return RedirectResponse(url="/final_results", status_code=303)

    current_answer = quiz_state["user_answers"][idx]
    
    context = get_template_context(request)
    context.update({
        "request": request,
        "question": questions[idx],
        "idx": idx,
        "current_answer": current_answer,
        "total_questions": len(questions),
        "questions": questions,
        "remaining_seconds": restriction_status["remaining_seconds"]
    })
    
    return templates.TemplateResponse("quiz.html", context)


@router.post("/answer")
async def save_answer(request: Request, idx: int = Form(...), user_answer: str = Form(...)):
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    restriction_status = get_quiz_restriction_status(request)
    if restriction_status["error"]:
        raise HTTPException(status_code=403, detail=restriction_status["error"])

    quiz_state = get_active_quiz(request)
    if not quiz_state:
        raise HTTPException(status_code=409, detail="Тест не начат")
    if quiz_state.get("result_saved"):
        raise HTTPException(status_code=409, detail="Тест уже завершён")
    if not 0 <= idx < len(quiz_state["questions"]):
        raise HTTPException(status_code=400, detail="Неверный номер вопроса")

    quiz_state["user_answers"][idx] = user_answer.strip()
    return JSONResponse({"status": "success"})


@router.post("/navigate")
async def navigate_question(request: Request, current_idx: int = Form(...), direction: str = Form(...)):
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    restriction_status = get_quiz_restriction_status(request)
    if restriction_status["error"]:
        raise HTTPException(status_code=403, detail=restriction_status["error"])

    quiz_state = get_active_quiz(request)
    if not quiz_state:
        raise HTTPException(status_code=409, detail="Тест не начат")
    questions = quiz_state["questions"]

    try:
        if direction == "next":
            new_idx = current_idx + 1
        else:
            new_idx = current_idx - 1
        
        # Проверяем границы
        if new_idx < 0:
            new_idx = 0
        elif new_idx >= len(questions):
            # Если пытаемся перейти за последний вопрос - перенаправляем на завершение
            return RedirectResponse(url="/final_results", status_code=303)
        
        return RedirectResponse(url=f"/quiz?idx={new_idx}", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def check_test_completion(request: Request):
    """Общая логика проверки завершения теста"""
    user = get_user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    restriction_status = get_quiz_restriction_status(request)
    if restriction_status["error"]:
        raise HTTPException(status_code=403, detail=restriction_status["error"])

    quiz_state = get_active_quiz(request)
    if not quiz_state:
        raise HTTPException(status_code=409, detail="Тест не начат")
    unanswered = [i + 1 for i in get_unanswered_indexes(quiz_state)]
    total = len(quiz_state["questions"])

    return {
        "completed": len(unanswered) == 0,
        "unanswered": unanswered,
        "total_questions": total,
        "answered_count": total - len(unanswered)
    }


@router.post("/check_completion")
async def check_test_completion_post(request: Request): 
    """POST версия для проверки завершения"""
    return await check_test_completion(request)


@router.get("/check_completion")
async def check_test_completion_get(request: Request):
    """GET версия для проверки завершения"""
    return await check_test_completion(request)


@router.get("/final_results", response_class=HTMLResponse)
def show_final_results(request: Request):
    user = get_user_from_session(request)
    if not user:
        return RedirectResponse(url="/", status_code=303)
    if ai_service is None or not ai_service.is_ready():
        return render_select_with_error(
            request,
            build_local_ai_help_text(ai_service.status() if ai_service is not None else ai_status)
        )
    
    quiz_state = get_active_quiz(request)
    if not quiz_state:
        return render_select_with_error(request, "Тест не начат. Выберите тест из списка.")
    questions = quiz_state["questions"]
    reference_answers = quiz_state["reference_answers"]
    all_embeddings = quiz_state["bank"]["embeddings"]

    restriction_status = get_quiz_restriction_status(request)
    force_finish = restriction_status["timed_out"] or restriction_status["deadline_expired"]

    # Не даём завершить тест с пропусками, если время ещё не вышло
    unanswered = get_unanswered_indexes(quiz_state)
    if unanswered and not force_finish and not quiz_state.get("result_saved"):
        return RedirectResponse(url=f"/quiz?idx={unanswered[0]}", status_code=303)

    now = datetime.now()
    started_at = quiz_state.get("started_at")
    if not quiz_state.get("finished_at"):
        quiz_state["finished_at"] = now
    finished_at = quiz_state["finished_at"]

    elapsed_seconds = None
    if started_at:
        elapsed_seconds = max(0, int((finished_at - started_at).total_seconds()))
    answers_for_scoring = list(quiz_state["user_answers"])

    # Порог и «серая зона»: из настроек теста, иначе — значения для модели
    scoring = get_scoring_settings(quiz_state.get("filename") or "")
    threshold = scoring["threshold"] if scoring["threshold"] is not None else THRESHOLD
    review_margin = scoring["review_margin"]

    # Оцениваем один раз; при повторном открытии страницы берём сохранённые оценки,
    # чтобы не пересчитывать и не затирать решения преподавателя
    evaluations = quiz_state.get("evaluations")
    if evaluations is None:
        evaluations = [
            evaluate_answer(ai_service, answer, reference_answers[i], all_embeddings[i], threshold, review_margin)
            for i, answer in enumerate(answers_for_scoring)
        ]
        quiz_state["evaluations"] = evaluations

    results = []
    for i, (answer, ev) in enumerate(zip(answers_for_scoring, evaluations)):
        results.append({
            "question": questions[i],
            "user_answer": answer.strip(),
            "verdict": ev.verdict,
            "verdict_label": VERDICT_LABELS[ev.verdict],
            "is_correct": ev.is_correct,
            "review_reason": ev.reason,
            "score": f"{ev.score:.2f}",
            "best_reference_answer": ev.best_reference,
            "reference_answers": reference_answers[i],
            "max_similarity": max(0.0, min(1.0, ev.score)),
        })

    total_questions = len(questions)
    total_correct = sum(1 for ev in evaluations if ev.verdict == VERDICT_CORRECT)
    pending_review = sum(1 for ev in evaluations if ev.verdict == VERDICT_REVIEW)
    percentage = (total_correct / total_questions) * 100 if total_questions > 0 else 0

    if not quiz_state.get("result_saved"):
        result_id = save_test_result(
            login=user,
            filename=quiz_state.get("filename"),
            attempt_number=int(quiz_state.get("attempt_number") or 0),
            total_questions=total_questions,
            total_correct=total_correct,
            percentage=percentage,
            elapsed_seconds=elapsed_seconds,
            threshold=threshold,
            forced_finish=force_finish,
            started_at=started_at,
            finished_at=finished_at,
            user_answers_list=answers_for_scoring,
            questions_list=questions,
            pending_review=pending_review
        )
        if result_id:
            save_answer_results(result_id, questions, answers_for_scoring, evaluations)
            quiz_state["result_id"] = result_id
        quiz_state["result_saved"] = True
    elif quiz_state.get("result_id"):
        # Преподаватель мог уже проверить ответы — показываем актуальные решения
        reviewed = {item["index"] - 1: item for item in get_answer_results(quiz_state["result_id"])}
        for i, item in enumerate(results):
            if i in reviewed:
                item["verdict"] = reviewed[i]["verdict"]
                item["verdict_label"] = reviewed[i]["verdict_label"]
                item["is_correct"] = item["verdict"] == VERDICT_CORRECT
        total_correct = sum(1 for item in results if item["verdict"] == VERDICT_CORRECT)
        pending_review = sum(1 for item in results if item["verdict"] == VERDICT_REVIEW)
        percentage = (total_correct / total_questions) * 100 if total_questions > 0 else 0

    context = get_template_context(request)
    context.update({
        "request": request,
        "results": results,
        "total_correct": total_correct,
        "total_questions": total_questions,
        "percentage": f"{percentage:.1f}",
        "threshold": threshold,
        "pending_review": pending_review,
        "restriction_message": restriction_status["error"] if force_finish else None,
        "completed_at_display": format_datetime_ru(finished_at),
        "elapsed_time_display": format_duration(elapsed_seconds)
    })
    
    return templates.TemplateResponse("final_results.html", context)
