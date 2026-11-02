"""Однократная загрузка ML-модели для офлайн-работы.

Запускается один раз при наличии интернета:
    python download_model.py            # основная модель (config.MODEL_NAME)
    python download_model.py --all      # плюс запасная модель

После этого приложение работает без интернета: модели берутся из local_models/.
"""
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer

from config import FALLBACK_MODEL_NAME, MODEL_HF_IDS, MODEL_NAME, MODELS_DIR

ROOT = Path(__file__).resolve().parent


def download(name: str) -> None:
    target = ROOT / MODELS_DIR / name
    if (target / "model.safetensors").exists():
        print(f"Уже загружена: {target}")
        return
    print(f"Загрузка {MODEL_HF_IDS[name]} ...")
    model = SentenceTransformer(MODEL_HF_IDS[name])
    target.mkdir(parents=True, exist_ok=True)
    model.save(str(target))
    print(f"Готово: {target}")


if __name__ == "__main__":
    download(MODEL_NAME)
    if "--all" in sys.argv:
        download(FALLBACK_MODEL_NAME)
