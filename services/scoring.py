"""Решение по ответу студента: «верно», «неверно» или «на проверку преподавателю».

Оценка ответа — максимальное косинусное сходство с эталонами (services/ai_backend.py).
Поверх порога действуют два правила, найденные в эксперименте (experiments/README.md):

1. «Серая зона». Оценка чуть ниже порога ненадёжна: такой ответ может быть и верным
   кратким, и неверным по теме. Решение в диапазоне [порог − ширина; порог) оставляем
   преподавателю.
2. Расхождение отрицаний. Ответ «от цели к фактам» почти совпадает по словам с эталоном
   «от фактов к цели», и модель засчитывает такие ответы. Если ответ прошёл по порогу,
   но число слов-отрицаний в нём не совпадает ни с одним эталоном, решает преподаватель.
"""
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from config import NEGATION_CHECK, REVIEW_MARGIN

VERDICT_CORRECT = "correct"
VERDICT_INCORRECT = "incorrect"
VERDICT_REVIEW = "review"

VERDICT_LABELS = {
    VERDICT_CORRECT: "Верно",
    VERDICT_INCORRECT: "Неверно",
    VERDICT_REVIEW: "На проверке",
}

_NEGATION_WORDS = re.compile(
    r"(?<![а-яё])(не|нет|ни|без|никогда|никаких|никакой|никак|ничего|нельзя)(?![а-яё])",
    re.IGNORECASE,
)


def count_negations(text: str) -> int:
    return len(_NEGATION_WORDS.findall((text or "").lower().replace("ё", "е")))


def has_negation_mismatch(answer: str, references: Sequence[str]) -> bool:
    """True, если число отрицаний в ответе не совпадает ни с одним эталоном."""
    if not references:
        return False
    answer_count = count_negations(answer)
    return all(count_negations(ref) != answer_count for ref in references)


@dataclass
class AnswerEvaluation:
    score: float
    best_reference: str
    verdict: str
    reason: str = ""

    @property
    def is_correct(self) -> bool:
        return self.verdict == VERDICT_CORRECT


def decide(score: float, answer: str, references: Sequence[str],
           threshold: float, review_margin: Optional[float] = None) -> tuple:
    """Решение по уже посчитанной оценке. Возвращает (verdict, reason)."""
    margin = REVIEW_MARGIN if review_margin is None else max(0.0, review_margin)
    if score >= threshold:
        if NEGATION_CHECK and has_negation_mismatch(answer, references):
            return VERDICT_REVIEW, "Ответ близок к эталону, но отличается отрицанием — возможно, смысл перевёрнут"
        return VERDICT_CORRECT, ""
    if margin > 0 and score >= threshold - margin:
        return VERDICT_REVIEW, "Оценка близка к порогу"
    return VERDICT_INCORRECT, ""


def evaluate_answer(ai_service: Any, user_answer: str, references: Sequence[str], reference_embeddings: Any,
                    threshold: float, review_margin: Optional[float] = None) -> AnswerEvaluation:
    answer = (user_answer or "").strip()
    if not answer:
        return AnswerEvaluation(0.0, references[0] if references else "", VERDICT_INCORRECT, "Нет ответа")
    if not references or reference_embeddings is None:
        return AnswerEvaluation(0.0, "", VERDICT_REVIEW, "У вопроса нет эталонного ответа")
    score, best_reference = ai_service.score_answer(answer, references, reference_embeddings)
    verdict, reason = decide(score, answer, references, threshold, review_margin)
    return AnswerEvaluation(float(score), best_reference, verdict, reason)
