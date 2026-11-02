import os
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

from sentence_transformers import SentenceTransformer, util

from config import (
    DEFAULT_THRESHOLD,
    FALLBACK_MODEL_NAME,
    MODEL_NAME,
    MODEL_THRESHOLDS,
    MODELS_DIR,
    THRESHOLD_OVERRIDE,
)

# Модели берутся только с диска: приложение должно работать без интернета
DEFAULT_MODEL_CANDIDATES = [
    os.getenv("LOCAL_EMBEDDING_MODEL_PATH", "").strip(),
    os.path.join(MODELS_DIR, MODEL_NAME),
    os.path.join(MODELS_DIR, FALLBACK_MODEL_NAME),
]


def threshold_for(model_source: Optional[str]) -> float:
    """Порог для загруженной модели: ручной из окружения или подобранный в эксперименте."""
    if THRESHOLD_OVERRIDE:
        return float(THRESHOLD_OVERRIDE)
    name = os.path.basename(os.path.normpath(model_source or ""))
    return MODEL_THRESHOLDS.get(name, DEFAULT_THRESHOLD)


@dataclass
class LocalAIStatus:
    ready: bool
    model_source: Optional[str]
    error: Optional[str]


class LocalAIService:
    def __init__(self, candidates: Optional[Sequence[str]] = None) -> None:
        self._candidates = [item for item in (candidates or DEFAULT_MODEL_CANDIDATES) if item]
        self._model: Optional[SentenceTransformer] = None
        self._model_source: Optional[str] = None
        self._error: Optional[str] = None
        self._load_model()

    @property
    def model(self) -> Optional[SentenceTransformer]:
        return self._model

    @property
    def model_source(self) -> Optional[str]:
        return self._model_source

    @property
    def error(self) -> Optional[str]:
        return self._error

    def status(self) -> LocalAIStatus:
        return LocalAIStatus(
            ready=self._model is not None,
            model_source=self._model_source,
            error=self._error,
        )

    def is_ready(self) -> bool:
        return self._model is not None

    def _load_model(self) -> None:
        errors: List[str] = []

        for candidate in self._candidates:
            try:
                model = SentenceTransformer(candidate, local_files_only=True)
                self._model = model
                self._model_source = candidate
                self._error = None
                return
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        self._model = None
        self._model_source = None
        self._error = " | ".join(errors) if errors else "No local model candidates configured"

    def encode(self, texts: Sequence[str], *, convert_to_tensor: bool = True):
        if self._model is None:
            raise RuntimeError("Local AI model is not loaded")
        normalized_texts = [str(text or "").strip() or " " for text in texts]
        return self._model.encode(normalized_texts, convert_to_tensor=convert_to_tensor)

    def encode_one(self, text: str, *, convert_to_tensor: bool = True):
        if self._model is None:
            raise RuntimeError("Local AI model is not loaded")
        return self._model.encode(str(text or "").strip() or " ", convert_to_tensor=convert_to_tensor)

    def score_answer(
        self,
        user_answer: str,
        reference_answers: Sequence[str],
        reference_embeddings: Any,
    ) -> Tuple[float, str]:
        if not reference_answers:
            return 0.0, ""
        if not str(user_answer or "").strip():
            return 0.0, str(reference_answers[0] or "")
        if self._model is None:
            raise RuntimeError("Local AI model is not loaded")

        user_embedding = self.encode_one(user_answer, convert_to_tensor=True)
        similarities = util.cos_sim(user_embedding, reference_embeddings)[0]
        max_sim_idx = int(similarities.argmax())
        return float(similarities[max_sim_idx]), str(reference_answers[max_sim_idx] or "")

    def score_search_documents(self, query: str, documents: Sequence[str]) -> List[float]:
        if self._model is None or not documents:
            return []
        query_embedding = self.encode_one(query, convert_to_tensor=True)
        doc_embeddings = self.encode(documents, convert_to_tensor=True)
        semantic_scores = util.cos_sim(query_embedding, doc_embeddings)[0]
        return [float(score) for score in semantic_scores]


def build_local_ai_help_text(status: LocalAIStatus) -> str:
    if status.ready:
        return f"Локальная AI-модель подключена: {status.model_source}"

    return (
        "Локальная AI-модель не найдена. Выполните один раз при наличии интернета "
        f"`python download_model.py` (модель сохранится в `{MODELS_DIR}/{MODEL_NAME}`) "
        "или укажите путь к модели в переменной окружения `LOCAL_EMBEDDING_MODEL_PATH`."
    )


# Единственный экземпляр сервиса на всё приложение: модель загружается один раз
try:
    ai_service: Optional[LocalAIService] = LocalAIService()
    ai_status: Optional[LocalAIStatus] = ai_service.status()
except Exception:
    # Модель не загрузилась — приложение работает, а пользователю показывается подсказка
    ai_service = None
    ai_status = None

# Порог для фактически загруженной модели
THRESHOLD: float = threshold_for(ai_service.model_source if ai_service else None)
