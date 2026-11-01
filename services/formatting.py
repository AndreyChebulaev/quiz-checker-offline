"""Разбор и форматирование дат, длительностей и JSON-списков."""
import json
from datetime import datetime, timedelta
from typing import List, Optional


def parse_available_until(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed_date = datetime.strptime(value, "%Y-%m-%d")
        return parsed_date + timedelta(days=1) - timedelta(seconds=1)
    except ValueError:
        return None


def format_date_ru(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return value


def format_datetime_ru(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S")


def format_datetime_db(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.replace(microsecond=0).isoformat(sep=" ")


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "--"
    safe_seconds = max(0, int(seconds))
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    secs = safe_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_datetime_db(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    normalized = str(value).strip().replace("T", " ")
    if not normalized:
        return None

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def parse_json_list(value: Optional[str]) -> List[str]:
    if not value:
        return []

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []

    if not isinstance(parsed, list):
        return []

    result = []
    for item in parsed:
        if item is None:
            result.append("")
        elif isinstance(item, str):
            result.append(item)
        else:
            result.append(str(item))
    return result


def normalize_date_filter(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None
