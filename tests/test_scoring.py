"""Правила решения по ответу: порог, «серая зона», проверка отрицаний."""
import pytest

from services.scoring import (
    VERDICT_CORRECT,
    VERDICT_INCORRECT,
    VERDICT_REVIEW,
    count_negations,
    decide,
    has_negation_mismatch,
)

pytestmark = pytest.mark.usefixtures("app_module")
REF = ["от фактов к цели"]


@pytest.mark.parametrize("text, expected", [
    ("Это не так", 1),
    ("нет, без учителя", 2),
    ("Неверный ответ, некоторые", 0),   # «не» внутри слова не считается
    ("ничего не знаю", 2),
    ("", 0),
])
def test_count_negations(text, expected):
    assert count_negations(text) == expected


def test_negation_mismatch():
    assert has_negation_mismatch("не от фактов к цели", REF)
    assert not has_negation_mismatch("от фактов к цели", REF)
    # совпадает хотя бы с одним эталоном — расхождения нет
    assert not has_negation_mismatch("без меток", ["на неразмеченных данных", "без разметки"])


@pytest.mark.parametrize("score, answer, expected", [
    (0.90, "от фактов к цели", VERDICT_CORRECT),
    (0.71, "от фактов к цели", VERDICT_CORRECT),      # ровно порог — засчитывается
    (0.70, "почти правильно", VERDICT_REVIEW),        # серая зона [0.68; 0.71)
    (0.68, "почти правильно", VERDICT_REVIEW),
    (0.67, "мимо", VERDICT_INCORRECT),
    (0.95, "не от фактов к цели", VERDICT_REVIEW),    # прошёл порог, но отрицание
    (0.40, "не знаю", VERDICT_INCORRECT),             # отрицание не спасает низкую оценку
])
def test_decide(score, answer, expected):
    verdict, _ = decide(score, answer, REF, threshold=0.71, review_margin=0.03)
    assert verdict == expected


def test_decide_without_gray_zone():
    assert decide(0.70, "почти", REF, threshold=0.71, review_margin=0)[0] == VERDICT_INCORRECT


def test_review_reason_is_explained():
    _, reason = decide(0.95, "не от фактов к цели", REF, threshold=0.71, review_margin=0.03)
    assert "отрицанием" in reason
