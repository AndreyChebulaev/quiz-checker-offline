"""Эксперимент: выбор модели и порога для автоматической проверки открытых ответов.

Сравниваются лексические методы (без нейросети) и несколько моделей
предложений (sentence embeddings). Для каждого метода оценка ответа —
максимальное косинусное сходство с эталонными ответами, как в приложении.

Порог выбирается честно: 5-кратная кросс-валидация с разбиением ПО ВОПРОСАМ
(GroupKFold) — порог подбирается по F1 на 4/5 вопросов и проверяется на
оставшейся 1/5, которую модель «не видела».

Запуск из корня проекта:
    python experiments/run_experiment.py                 # все модели (скачает недостающие)
    python experiments/run_experiment.py --offline       # только уже скачанные
    python experiments/run_experiment.py --models minilm-l12 labse

Результаты: experiments/results/<дата_время>/ (report.md, metrics.csv, scores.csv, графики).
"""
import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "experiments" / "data" / "answers_intelligent_systems.csv"
MODELS_DIR = ROOT / "local_models"
CURRENT_THRESHOLD = 0.76  # порог в приложении до эксперимента

CATEGORIES = {
    "para": "верный, перефраз",
    "short": "верный, кратко",
    "partial": "неполный",
    "wrong": "неверный по теме",
    "neg": "искажение смысла",
    "off": "не по теме",
}

PLOT_LABELS = {"para": "верный\nперефраз", "short": "верный\nкратко", "partial": "неполный",
               "wrong": "неверный\nпо теме", "neg": "искажение\nсмысла", "off": "не по\nтеме"}

# Кандидаты: id для командной строки -> описание
EMBEDDING_MODELS = {
    "minilm-l12": dict(hf="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                       local="paraphrase-multilingual-MiniLM-L12-v2", title="multilingual MiniLM-L12 (текущая)"),
    "minilm-l6-en": dict(hf="sentence-transformers/all-MiniLM-L6-v2",
                         local="all-MiniLM-L6-v2", title="all-MiniLM-L6 (англоязычная, онлайн-версия)"),
    "mpnet": dict(hf="sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
                  local="paraphrase-multilingual-mpnet-base-v2", title="multilingual MPNet-base"),
    "labse": dict(hf="sentence-transformers/LaBSE", local="LaBSE", title="LaBSE"),
    "e5-small": dict(hf="intfloat/multilingual-e5-small", local="multilingual-e5-small",
                     title="multilingual-E5-small", prefix="query: "),
    "rubert-tiny2": dict(hf="cointegrated/rubert-tiny2", local="rubert-tiny2", title="ruBERT-tiny2"),
}
LEXICAL = {"jaccard": "Жаккар по основам слов", "tfidf": "TF-IDF по символьным n-граммам"}


# ---------------------------------------------------------------- данные
def load_dataset(path=DATA):
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for r in rows:
        r["references"] = json.loads(r["references"])
        r["label"] = int(r["label"])
        r["question_id"] = int(r["question_id"])
    return rows


# ---------------------------------------------------------------- методы
_WORD = re.compile(r"[a-zа-яё0-9]+")


def _stems(text):
    # грубый стемминг: первые 5 букв слова длиной от 3 символов
    return {w[:5] for w in _WORD.findall(text.lower().replace("ё", "е")) if len(w) >= 3}


def score_jaccard(rows):
    out = []
    for r in rows:
        a = _stems(r["answer"])
        best = 0.0
        for ref in r["references"]:
            b = _stems(ref)
            if a and b:
                best = max(best, len(a & b) / len(a | b))
        out.append(best)
    return np.array(out)


def score_tfidf(rows):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    corpus = sorted({t for r in rows for t in [r["answer"], *r["references"]]})
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True).fit(corpus)
    out = []
    for r in rows:
        sims = cosine_similarity(vec.transform([r["answer"]]), vec.transform(r["references"]))[0]
        out.append(float(sims.max()))
    return np.array(out)


def load_embedding_model(key, offline):
    from sentence_transformers import SentenceTransformer
    spec = EMBEDDING_MODELS[key]
    local = MODELS_DIR / spec["local"]
    if local.exists():
        return SentenceTransformer(str(local), local_files_only=True)
    if offline:
        raise FileNotFoundError(f"нет папки {local}")
    print(f"   скачивание {spec['hf']} ...")
    model = SentenceTransformer(spec["hf"])
    model.save(str(local))
    return model


def score_embeddings(rows, model, prefix=""):
    from sentence_transformers import util
    texts = sorted({t for r in rows for t in [r["answer"], *r["references"]]})
    t0 = time.perf_counter()
    emb = model.encode([prefix + t for t in texts], convert_to_tensor=True, normalize_embeddings=True,
                       batch_size=32, show_progress_bar=False)
    seconds = time.perf_counter() - t0
    idx = {t: i for i, t in enumerate(texts)}
    out = []
    for r in rows:
        a = emb[idx[r["answer"]]]
        refs = emb[[idx[x] for x in r["references"]]]
        out.append(float(util.cos_sim(a, refs).max()))
    return np.array(out), seconds / len(texts) * 1000  # мс на один текст


# ---------------------------------------------------------------- метрики
def confusion(y, scores, thr):
    pred = scores >= thr
    tp = int(np.sum(pred & (y == 1))); fp = int(np.sum(pred & (y == 0)))
    fn = int(np.sum(~pred & (y == 1))); tn = int(np.sum(~pred & (y == 0)))
    acc = (tp + tn) / len(y)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return dict(accuracy=acc, precision=prec, recall=rec, f1=f1, tp=tp, fp=fp, fn=fn, tn=tn)


def best_threshold(y, scores):
    candidates = np.unique(np.round(scores, 4))
    best = max(candidates, key=lambda t: (confusion(y, scores, t)["f1"], confusion(y, scores, t)["accuracy"]))
    return float(best)


def roc_auc(y, scores):
    from sklearn.metrics import roc_auc_score
    return float(roc_auc_score(y, scores))


def cross_validated(y, scores, groups, k=5, seed=42):
    """Порог подбирается на обучающих вопросах, качество меряется на отложенных."""
    rng = np.random.default_rng(seed)
    qids = np.unique(groups); rng.shuffle(qids)
    folds = np.array_split(qids, k)
    f1s, accs, thrs = [], [], []
    for fold in folds:
        test = np.isin(groups, fold)
        thr = best_threshold(y[~test], scores[~test])
        m = confusion(y[test], scores[test], thr)
        f1s.append(m["f1"]); accs.append(m["accuracy"]); thrs.append(thr)
    return dict(cv_f1=float(np.mean(f1s)), cv_f1_std=float(np.std(f1s)),
                cv_accuracy=float(np.mean(accs)), cv_accuracy_std=float(np.std(accs)),
                cv_threshold_mean=float(np.mean(thrs)), cv_threshold_std=float(np.std(thrs)))


# ---------------------------------------------------------------- графики
def make_plots(out_dir, y, results, cats):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from sklearn.metrics import roc_curve
    except ImportError:
        print("   matplotlib не установлен — графики пропущены (pip install matplotlib)")
        return []
    files = []
    plt.figure(figsize=(6.5, 5.5))
    for name, r in results.items():
        fpr, tpr, _ = roc_curve(y, r["scores"])
        plt.plot(fpr, tpr, label=f"{r['title']} (AUC={r['metrics']['roc_auc']:.3f})")
    plt.plot([0, 1], [0, 1], "k--", lw=0.8)
    plt.xlabel("Доля ложноположительных (FPR)"); plt.ylabel("Доля истинноположительных (TPR)")
    plt.title("ROC-кривые методов оценки ответов"); plt.legend(fontsize=7, loc="lower right"); plt.tight_layout()
    plt.savefig(out_dir / "roc.png", dpi=200); plt.close(); files.append("roc.png")

    order = list(CATEGORIES)
    for name, r in results.items():
        plt.figure(figsize=(7.5, 4.5))
        data = [r["scores"][cats == c] for c in order]
        names = [PLOT_LABELS[c] for c in order]
        try:
            plt.boxplot(data, tick_labels=names)   # matplotlib >= 3.9
        except TypeError:
            plt.boxplot(data, labels=names)
        plt.axhline(r["metrics"]["best_threshold"], color="red", ls="--", lw=1,
                    label=f"порог {r['metrics']['best_threshold']:.2f}")
        plt.ylabel("Оценка сходства"); plt.title(f"Распределение оценок по типам ответов: {r['title']}")
        plt.legend(fontsize=8); plt.xticks(fontsize=8); plt.tight_layout()
        fn = f"categories_{name}.png"; plt.savefig(out_dir / fn, dpi=200); plt.close(); files.append(fn)

    plt.figure(figsize=(6.5, 4.5))
    for name, r in results.items():
        s = r["scores"]; grid = np.linspace(max(0, s.min()), min(1, s.max()), 200)
        plt.plot(grid, [confusion(y, s, t)["f1"] for t in grid], label=r["title"])
    plt.xlabel("Порог"); plt.ylabel("F1"); plt.title("Зависимость F1 от порога")
    plt.legend(fontsize=7); plt.tight_layout()
    plt.savefig(out_dir / "f1_vs_threshold.png", dpi=200); plt.close(); files.append("f1_vs_threshold.png")
    return files


# ---------------------------------------------------------------- отчёт
def write_report(out_dir, rows, results, plots):
    y = np.array([r["label"] for r in rows]); cats = np.array([r["category"] for r in rows])
    n_q = len({r["question_id"] for r in rows})
    ranked = sorted(results.items(), key=lambda kv: -kv[1]["metrics"]["cv_f1"])
    L = [f"# Результаты эксперимента ({datetime.now():%d.%m.%Y %H:%M})", "",
         f"Набор: {n_q} вопросов, {len(rows)} ответов (верных {int(y.sum())}, неверных {int(len(y) - y.sum())}).", "",
         "## Сводная таблица", "",
         "| Метод | ROC-AUC | F1 (CV) | Accuracy (CV) | Порог (CV) | F1 при пороге 0.76 | мс/текст |",
         "|---|---|---|---|---|---|---|"]
    for name, r in ranked:
        m = r["metrics"]
        speed = f"{m['ms_per_text']:.1f}" if m.get("ms_per_text") is not None else "—"
        L.append(f"| {r['title']} | {m['roc_auc']:.3f} | {m['cv_f1']:.3f} ± {m['cv_f1_std']:.3f} | "
                 f"{m['cv_accuracy']:.3f} ± {m['cv_accuracy_std']:.3f} | {m['cv_threshold_mean']:.3f} ± {m['cv_threshold_std']:.3f} | "
                 f"{m['at_current']['f1']:.3f} | {speed} |")
    L += ["", "F1 (CV) и Accuracy (CV) — среднее ± ст. отклонение по 5 блокам, разбиение по вопросам: "
          "порог подбирался на одних вопросах и проверялся на других.", ""]

    best_name, best = ranked[0]
    bm = best["metrics"]
    L += ["## Лучший метод", "",
          f"**{best['title']}**: рекомендуемый порог **{bm['best_threshold']:.2f}** "
          f"(по всему набору: F1 = {bm['at_best']['f1']:.3f}, precision = {bm['at_best']['precision']:.3f}, "
          f"recall = {bm['at_best']['recall']:.3f}).", "",
          "## Доля ответов, засчитанных как верные, по типам ответа (порог — лучший для метода)", "",
          "| Метод | " + " | ".join(CATEGORIES.values()) + " |", "|---|" + "---|" * len(CATEGORIES)]
    for name, r in ranked:
        thr = r["metrics"]["best_threshold"]
        cells = [f"{np.mean(r['scores'][cats == c] >= thr):.0%}" for c in CATEGORIES]
        L.append(f"| {r['title']} | " + " | ".join(cells) + " |")
    L += ["", "Для верных типов (первые два столбца) чем выше, тем лучше; для остальных — чем ниже, тем лучше.", "",
          "## Средняя оценка сходства по типам ответа", "",
          "| Метод | " + " | ".join(CATEGORIES.values()) + " |", "|---|" + "---|" * len(CATEGORIES)]
    for name, r in ranked:
        L.append(f"| {r['title']} | " + " | ".join(f"{r['scores'][cats == c].mean():.3f}" for c in CATEGORIES) + " |")
    if plots:
        L += ["", "## Графики", ""] + [f"![{p}]({p})" for p in plots]
    (out_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", default=list(EMBEDDING_MODELS), help="модели: " + ", ".join(EMBEDDING_MODELS))
    ap.add_argument("--offline", action="store_true", help="не скачивать модели, использовать только local_models/")
    ap.add_argument("--no-lexical", action="store_true", help="без лексических методов")
    ap.add_argument("--data", default=str(DATA))
    args = ap.parse_args()

    rows = load_dataset(args.data)
    y = np.array([r["label"] for r in rows]); groups = np.array([r["question_id"] for r in rows])
    cats = np.array([r["category"] for r in rows])
    out_dir = ROOT / "experiments" / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = {}
    if not args.no_lexical:
        raw["jaccard"] = (LEXICAL["jaccard"], score_jaccard(rows), None)
        raw["tfidf"] = (LEXICAL["tfidf"], score_tfidf(rows), None)
    for key in args.models:
        spec = EMBEDDING_MODELS[key]
        print(f"-> {spec['title']}")
        try:
            model = load_embedding_model(key, args.offline)
        except Exception as e:
            print(f"   пропущена: {e}")
            continue
        scores, ms = score_embeddings(rows, model, spec.get("prefix", ""))
        raw[key] = (spec["title"], scores, ms)
        del model

    results = {}
    for name, (title, scores, ms) in raw.items():
        thr = best_threshold(y, scores)
        metrics = dict(roc_auc=roc_auc(y, scores), best_threshold=thr,
                       at_best=confusion(y, scores, thr), at_current=confusion(y, scores, CURRENT_THRESHOLD),
                       ms_per_text=ms, **cross_validated(y, scores, groups))
        results[name] = dict(title=title, scores=scores, metrics=metrics)
        print(f"   {title:45} AUC={metrics['roc_auc']:.3f}  F1(CV)={metrics['cv_f1']:.3f}  порог={thr:.3f}")

    with open(out_dir / "scores.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["answer_id", "question_id", "category", "label", "answer", *results])
        for i, r in enumerate(rows):
            w.writerow([r["answer_id"], r["question_id"], r["category"], r["label"], r["answer"],
                        *[f"{results[k]['scores'][i]:.4f}" for k in results]])
    with open(out_dir / "metrics.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["method", "title", "roc_auc", "cv_f1", "cv_f1_std", "cv_accuracy", "cv_threshold_mean",
                    "best_threshold", "f1_at_best", "precision_at_best", "recall_at_best", "f1_at_0.76", "ms_per_text"])
        for k, r in results.items():
            m = r["metrics"]
            w.writerow([k, r["title"], *(f"{v:.4f}" for v in [m["roc_auc"], m["cv_f1"], m["cv_f1_std"], m["cv_accuracy"],
                        m["cv_threshold_mean"], m["best_threshold"], m["at_best"]["f1"], m["at_best"]["precision"],
                        m["at_best"]["recall"], m["at_current"]["f1"]]), "" if m["ms_per_text"] is None else f"{m['ms_per_text']:.2f}"])
    plots = make_plots(out_dir, y, results, cats)
    write_report(out_dir, rows, results, plots)
    print(f"\nГотово: {out_dir}")


if __name__ == "__main__":
    sys.exit(main())
