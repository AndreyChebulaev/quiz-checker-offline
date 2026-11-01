"""Однократная загрузка ML-модели для офлайн-работы.

Запускается один раз при наличии интернета:
    python download_model.py

После этого приложение работает без интернета: модель берётся
из папки local_models/paraphrase-multilingual-MiniLM-L12-v2.
"""
from pathlib import Path

from sentence_transformers import SentenceTransformer

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TARGET_DIR = Path(__file__).parent / "local_models" / "paraphrase-multilingual-MiniLM-L12-v2"


def main() -> None:
    if (TARGET_DIR / "model.safetensors").exists():
        print(f"Модель уже загружена: {TARGET_DIR}")
        return
    print(f"Загрузка {MODEL_ID} ...")
    model = SentenceTransformer(MODEL_ID)
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    model.save(str(TARGET_DIR))
    print(f"Готово: {TARGET_DIR}")


if __name__ == "__main__":
    main()
