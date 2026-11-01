"""История результатов, детальный просмотр и выгрузка в Excel."""
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.users_repo import get_user_full_info
from core.web import get_template_context, get_user_from_session, templates
from services.results import (
    get_results_filters_from_request,
    get_test_result_detail_for_view,
    get_test_result_tests_for_view,
    get_test_results_for_view,
)
from services.results_export import (
    build_test_result_detail_workbook,
    build_test_results_workbook,
    workbook_to_excel_response,
)

router = APIRouter()


@router.get("/results", response_class=HTMLResponse)
def results_page(request: Request):
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)

    user_info = get_user_full_info(login)
    if not user_info:
        return RedirectResponse(url="/", status_code=303)

    filters = get_results_filters_from_request(request)

    results = get_test_results_for_view(
        user_info,
        search_query=filters["q"],
        status_filter=filters["status"],
        test_filter=filters["test"],
        date_from=filters["date_from"],
        date_to=filters["date_to"]
    )
    test_options = get_test_result_tests_for_view(user_info)

    context = get_template_context(request)
    context.update({
        "request": request,
        "results": results,
        "results_count": len(results),
        "show_user_column": user_info.get("user_type") in ["teacher", "admin"],
        "test_options": test_options,
        "filters": filters
    })
    return templates.TemplateResponse("test_results.html", context)


@router.get("/results/export")
def export_results_page(request: Request):
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)

    user_info = get_user_full_info(login)
    if not user_info:
        return RedirectResponse(url="/", status_code=303)

    filters = get_results_filters_from_request(request)
    show_user_column = user_info.get("user_type") in ["teacher", "admin"]
    results = get_test_results_for_view(
        user_info,
        limit=None,
        search_query=filters["q"],
        status_filter=filters["status"],
        test_filter=filters["test"],
        date_from=filters["date_from"],
        date_to=filters["date_to"]
    )

    workbook = build_test_results_workbook(results, filters, show_user_column)
    export_name = f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return workbook_to_excel_response(workbook, export_name)


@router.get("/results/{result_id}/export")
def export_result_detail(request: Request, result_id: int):
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)

    user_info = get_user_full_info(login)
    if not user_info:
        return RedirectResponse(url="/", status_code=303)

    result_item = get_test_result_detail_for_view(user_info, result_id)
    if not result_item:
        raise HTTPException(status_code=404, detail="Результат не найден")

    show_user_meta = user_info.get("user_type") in ["teacher", "admin"]
    workbook = build_test_result_detail_workbook(result_item, show_user_meta)
    safe_test_name = re.sub(r"[^A-Za-z0-9_-]+", "_", result_item.get("test_name") or "result").strip("_") or "result"
    export_name = f"test_result_{safe_test_name}_{result_item.get('attempt_number', 0)}.xlsx"
    return workbook_to_excel_response(workbook, export_name)


@router.get("/results/{result_id}", response_class=HTMLResponse)
def result_detail_page(request: Request, result_id: int):
    login = get_user_from_session(request)
    if not login:
        return RedirectResponse(url="/", status_code=303)

    user_info = get_user_full_info(login)
    if not user_info:
        return RedirectResponse(url="/", status_code=303)

    result_item = get_test_result_detail_for_view(user_info, result_id)
    if not result_item:
        raise HTTPException(status_code=404, detail="Результат не найден")

    context = get_template_context(request)
    context.update({
        "request": request,
        "result_item": result_item,
        "show_user_meta": user_info.get("user_type") in ["teacher", "admin"]
    })
    return templates.TemplateResponse("test_result_detail.html", context)
