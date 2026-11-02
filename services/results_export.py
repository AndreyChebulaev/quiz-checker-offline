"""Выгрузка результатов в Excel."""
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from services.formatting import format_datetime_ru
from services.results import format_result_status_label, format_results_filter_status_label


def autosize_worksheet_columns(worksheet, min_width: int = 12, max_width: int = 60) -> None:
    for column_cells in worksheet.columns:
        max_length = 0
        column_index = None
        for cell in column_cells:
            column_index = cell.column
            value = "" if cell.value is None else str(cell.value)
            if len(value) > max_length:
                max_length = len(value)
        if column_index is None:
            continue
        column_letter = get_column_letter(column_index)
        worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, min_width), max_width)


def workbook_to_excel_response(workbook: Workbook, filename: str) -> StreamingResponse:
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers
    )


def build_test_results_workbook(
    results: List[Dict[str, Any]],
    filters: Dict[str, str],
    show_user_column: bool
) -> Workbook:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Результаты"

    title_font = Font(bold=True, size=14)
    label_font = Font(bold=True)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(fill_type="solid", fgColor="1F6AA5")

    worksheet["A1"] = "Результаты тестов"
    worksheet["A1"].font = title_font

    meta_rows = [
        ("Сформировано", format_datetime_ru(datetime.now())),
        ("Поиск", filters.get("q") or "—"),
        ("Статус", format_results_filter_status_label(filters.get("status") or "all")),
        ("Тест", filters.get("test") or "Все тесты"),
        ("Дата с", filters.get("date_from") or "—"),
        ("Дата по", filters.get("date_to") or "—"),
        ("Количество результатов", str(len(results)))
    ]

    for row_index, (label, value) in enumerate(meta_rows, start=3):
        worksheet.cell(row=row_index, column=1, value=label).font = label_font
        worksheet.cell(row=row_index, column=2, value=value)

    header_row = 11
    headers = []
    if show_user_column:
        headers.extend(["Пользователь", "Логин", "Группа"])
    headers.extend([
        "Тест",
        "Попытка",
        "Правильных ответов",
        "Всего вопросов",
        "Процент",
        "Время",
        "Завершен",
        "Статус"
    ])

    for column_index, title in enumerate(headers, start=1):
        cell = worksheet.cell(row=header_row, column=column_index, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, item in enumerate(results, start=header_row + 1):
        values = []
        if show_user_column:
            values.extend([
                item.get("full_name", ""),
                item.get("login", ""),
                item.get("group_name", "")
            ])
        values.extend([
            item.get("test_name", ""),
            item.get("attempt_number", ""),
            item.get("total_correct", 0),
            item.get("total_questions", 0),
            f"{item.get('percentage_display', '0.0')}%",
            item.get("elapsed_time_display", ""),
            item.get("finished_at_display", ""),
            format_result_status_label(bool(item.get("forced_finish")))
        ])

        for column_index, value in enumerate(values, start=1):
            worksheet.cell(row=row_index, column=column_index, value=value)

    worksheet.freeze_panes = f"A{header_row + 1}"
    autosize_worksheet_columns(worksheet)
    return workbook


def answer_status_text(answer: Dict[str, Any]) -> str:
    """Решение по ответу для выгрузки; для старых попыток без оценок — наличие ответа."""
    if answer.get("verdict_label"):
        suffix = " (решение преподавателя)" if answer.get("is_overridden") else ""
        return f"{answer['verdict_label']}{suffix}"
    return "Есть ответ" if answer.get("has_answer") else "Нет ответа"


def build_test_result_detail_workbook(result_item: Dict[str, Any], show_user_meta: bool) -> Workbook:
    workbook = Workbook()

    summary_sheet = workbook.active
    summary_sheet.title = "Сводка"
    summary_sheet["A1"] = "Детали результата теста"
    summary_sheet["A1"].font = Font(bold=True, size=14)

    summary_rows = []
    if show_user_meta:
        summary_rows.extend([
            ("Пользователь", result_item.get("full_name", "")),
            ("Логин", result_item.get("login", "")),
            ("Группа", result_item.get("group_name", "") or "—")
        ])

    summary_rows.extend([
        ("Тест", result_item.get("test_name", "")),
        ("Попытка", result_item.get("attempt_number", "")),
        ("Правильных ответов", result_item.get("total_correct", 0)),
        ("На проверке у преподавателя", result_item.get("pending_review", 0)),
        ("Всего вопросов", result_item.get("total_questions", 0)),
        ("Процент", f"{result_item.get('percentage_display', '0.0')}%"),
        ("Статус", format_result_status_label(bool(result_item.get("forced_finish")))),
        ("Начало", result_item.get("started_at_display", "")),
        ("Завершение", result_item.get("finished_at_display", "")),
        ("Время прохождения", result_item.get("elapsed_time_display", ""))
    ])

    for row_index, (label, value) in enumerate(summary_rows, start=3):
        summary_sheet.cell(row=row_index, column=1, value=label).font = Font(bold=True)
        summary_sheet.cell(row=row_index, column=2, value=value)

    answers_start_row = len(summary_rows) + 5
    summary_sheet.cell(row=answers_start_row, column=1, value="Ответы пользователя").font = Font(bold=True, size=12)

    summary_headers_row = answers_start_row + 1
    answer_headers = ["№", "Вопрос", "Ответ пользователя", "Статус ответа"]
    for column_index, title in enumerate(answer_headers, start=1):
        cell = summary_sheet.cell(row=summary_headers_row, column=column_index, value=title)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="1F6AA5")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, answer in enumerate(result_item.get("answer_items", []), start=summary_headers_row + 1):
        answer_text = answer.get("user_answer") or "Нет ответа"
        status_text = answer_status_text(answer)

        summary_sheet.cell(row=row_index, column=1, value=answer.get("index", row_index - summary_headers_row))
        summary_sheet.cell(row=row_index, column=2, value=answer.get("question", ""))
        summary_sheet.cell(row=row_index, column=3, value=answer_text)
        summary_sheet.cell(row=row_index, column=4, value=status_text)

    answers_sheet = workbook.create_sheet("Ответы")
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(fill_type="solid", fgColor="1F6AA5")

    for column_index, title in enumerate(answer_headers, start=1):
        cell = answers_sheet.cell(row=1, column=column_index, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for row_index, answer in enumerate(result_item.get("answer_items", []), start=2):
        answer_text = answer.get("user_answer") or "Нет ответа"
        answers_sheet.cell(row=row_index, column=1, value=answer.get("index", row_index - 1))
        answers_sheet.cell(row=row_index, column=2, value=answer.get("question", ""))
        answers_sheet.cell(row=row_index, column=3, value=answer_text)
        answers_sheet.cell(
            row=row_index,
            column=4,
            value=answer_status_text(answer)
        )

    summary_sheet.freeze_panes = "A2"
    answers_sheet.freeze_panes = "A2"
    autosize_worksheet_columns(summary_sheet)
    autosize_worksheet_columns(answers_sheet)
    return workbook
