"""Поиск по результатам на естественном языке: числа, сравнения, семантическая близость."""
import re
from typing import Any, Dict, List, Optional

from services.ai_backend import ai_service


def normalize_search_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def tokenize_search_text(value: str) -> List[str]:
    normalized = normalize_search_text(value)
    return [token for token in re.split(r"[^\w-]+", normalized, flags=re.UNICODE) if token]


RESULT_SEARCH_STOPWORDS = {
    "покажи", "показать", "найди", "найти", "выведи", "вывести", "все", "всех",
    "результат", "результаты", "результатов", "результате", "тест", "теста", "тестов",
    "где", "у", "для", "по", "с", "со", "и", "или", "а", "но", "на", "в", "во",
    "к", "ко", "из", "от", "до", "это", "этого", "этот", "эта", "эти", "мне",
    "нужны", "нужен", "нужна", "нужно", "есть", "покажите", "выведите"
}


RUSSIAN_NUMBER_WORDS = {
    "ноль": 0,
    "один": 1, "одна": 1, "одного": 1, "одной": 1,
    "два": 2, "две": 2, "двух": 2,
    "три": 3, "трех": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4,
    "пять": 5, "пяти": 5,
    "шесть": 6, "шести": 6,
    "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8,
    "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10
}


def parse_russian_number(value: str) -> Optional[float]:
    normalized = normalize_search_text(value)
    if not normalized:
        return None
    if re.fullmatch(r"\d+(?:[.,]\d+)?", normalized):
        return float(normalized.replace(",", "."))
    return float(RUSSIAN_NUMBER_WORDS[normalized]) if normalized in RUSSIAN_NUMBER_WORDS else None


def detect_comparison_operator(raw_operator: str) -> str:
    operator = normalize_search_text(raw_operator)
    if operator in {"больше", "более", "свыше", "выше", "после", ">"}:
        return "gt"
    if operator in {"не меньше", "не менее", "от", ">="}:
        return "gte"
    if operator in {"меньше", "менее", "ниже", "до", "<"}:
        return "lt"
    if operator in {"не больше", "не более", "<="}:
        return "lte"
    if operator in {"равно", "=", "ровно"}:
        return "eq"
    return "eq"


def compare_numeric_value(value: float, operator: str, threshold: float) -> bool:
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    return value == threshold


def parse_result_search_query(search_query: str) -> Dict[str, Any]:
    normalized_query = normalize_search_text(search_query)
    cleaned_query = normalized_query
    filters: Dict[str, Dict[str, float]] = {}

    structured_patterns = [
        ("attempt_number", r"попыт\w*\s+(не\s+меньше|не\s+менее|не\s+больше|не\s+более|больше|более|свыше|выше|меньше|менее|ниже|до|от|равно|ровно|>=|<=|>|<|=)\s+([a-zа-яё0-9.,-]+)"),
        ("attempt_number", r"(не\s+меньше|не\s+менее|не\s+больше|не\s+более|больше|более|свыше|выше|меньше|менее|ниже|до|от|равно|ровно|>=|<=|>|<|=)\s+([a-zа-яё0-9.,-]+)\s+попыт\w*"),
        ("percentage_value", r"процент(?:\s+\w+){0,2}\s+(не\s+меньше|не\s+менее|не\s+больше|не\s+более|больше|более|свыше|выше|меньше|менее|ниже|до|от|равно|ровно|>=|<=|>|<|=)\s+([a-zа-яё0-9.,-]+)"),
        ("percentage_value", r"(не\s+меньше|не\s+менее|не\s+больше|не\s+более|больше|более|свыше|выше|меньше|менее|ниже|до|от|равно|ровно|>=|<=|>|<|=)\s+([a-zа-яё0-9.,-]+)\s+процент(?:\s+\w+){0,2}")
    ]

    for field_name, pattern in structured_patterns:
        match = re.search(pattern, cleaned_query, flags=re.IGNORECASE)
        if not match:
            continue
        operator = detect_comparison_operator(match.group(1))
        threshold = parse_russian_number(match.group(2))
        if threshold is None:
            continue
        filters[field_name] = {
            "operator": operator,
            "value": threshold
        }
        cleaned_query = re.sub(pattern, " ", cleaned_query, count=1, flags=re.IGNORECASE)

    semantic_tokens = [
        token for token in tokenize_search_text(cleaned_query)
        if token not in RESULT_SEARCH_STOPWORDS and not re.fullmatch(r"\d+(?:[.,]\d+)?", token)
    ]

    return {
        "original_query": normalized_query,
        "semantic_query": " ".join(semantic_tokens).strip(),
        "filters": filters
    }


def build_result_search_document(item: Dict[str, Any]) -> str:
    parts = [
        item.get("full_name", ""),
        item.get("login", ""),
        item.get("group_name", ""),
        item.get("test_name", ""),
        item.get("filename", ""),
        str(item.get("attempt_number", "")),
        str(item.get("percentage_value", "")),
        f"{item.get('total_correct', '')} {item.get('total_questions', '')}",
        item.get("finished_at_display", "")
    ]
    return normalize_search_text(" ".join(part for part in parts if part))


def compute_result_lexical_score(search_query: str, search_doc: str) -> float:
    if not search_query or not search_doc:
        return 0.0

    query_tokens = tokenize_search_text(search_query)
    doc_tokens = tokenize_search_text(search_doc)
    if not query_tokens:
        query_tokens = [normalize_search_text(search_query)]

    matched_tokens = 0
    score = 0.0

    for token in query_tokens:
        if not token:
            continue
        if token == search_doc:
            score += 1.0
            matched_tokens += 1
            continue
        if token in search_doc:
            score += 0.82
            matched_tokens += 1
            continue
        if any(doc_token.startswith(token) or token.startswith(doc_token) for doc_token in doc_tokens):
            score += 0.58
            matched_tokens += 1

    if matched_tokens == 0:
        return 0.0

    coverage_bonus = matched_tokens / max(len(query_tokens), 1)
    return score + coverage_bonus


def apply_ai_result_search(results: List[Dict[str, Any]], search_query: str) -> List[Dict[str, Any]]:
    parsed_query = parse_result_search_query(search_query)
    normalized_query = parsed_query["original_query"]
    semantic_query = parsed_query["semantic_query"]
    structured_filters = parsed_query["filters"]

    if not normalized_query:
        return results

    filtered_input_results = []
    for item in results:
        passes_filters = True
        for field_name, condition in structured_filters.items():
            raw_value = item.get(field_name)
            try:
                numeric_value = float(raw_value)
            except (TypeError, ValueError):
                passes_filters = False
                break
            if not compare_numeric_value(numeric_value, str(condition["operator"]), float(condition["value"])):
                passes_filters = False
                break
        if passes_filters:
            filtered_input_results.append(item)

    if not semantic_query:
        return filtered_input_results

    enriched_results: List[Dict[str, Any]] = []
    documents: List[str] = []

    for item in filtered_input_results:
        search_doc = build_result_search_document(item)
        lexical_score = compute_result_lexical_score(semantic_query, search_doc)

        enriched_item = dict(item)
        enriched_item["_search_doc"] = search_doc
        enriched_item["_lexical_score"] = lexical_score
        enriched_item["_semantic_score"] = 0.0
        enriched_item["_search_score"] = lexical_score
        enriched_results.append(enriched_item)
        documents.append(search_doc or " ")

    if ai_service is not None and ai_service.is_ready() and documents:
        try:
            semantic_scores = ai_service.score_search_documents(semantic_query, documents)
            for idx, item in enumerate(enriched_results):
                semantic_score = float(semantic_scores[idx])
                item["_semantic_score"] = semantic_score
                item["_search_score"] = max(item["_lexical_score"], 0.0) * 1.35 + max(semantic_score, 0.0)
        except Exception:
            pass

    filtered_results: List[Dict[str, Any]] = []
    for item in enriched_results:
        lexical_score = float(item.get("_lexical_score", 0.0) or 0.0)
        semantic_score = float(item.get("_semantic_score", 0.0) or 0.0)
        exact_hit = semantic_query in item.get("_search_doc", "")
        token_hit = lexical_score >= 0.55
        semantic_hit = semantic_score >= 0.42 and lexical_score >= 0.2
        strong_semantic_hit = semantic_score >= 0.67

        if exact_hit or token_hit or semantic_hit or strong_semantic_hit:
            filtered_results.append(item)

    filtered_results.sort(
        key=lambda item: (
            float(item.get("_search_score", 0.0) or 0.0),
            float(item.get("_semantic_score", 0.0) or 0.0),
            float(item.get("_lexical_score", 0.0) or 0.0),
            int(item.get("id", 0) or 0)
        ),
        reverse=True
    )

    for item in filtered_results:
        item.pop("_search_doc", None)
        item.pop("_lexical_score", None)
        item.pop("_semantic_score", None)
        item.pop("_search_score", None)

    return filtered_results
