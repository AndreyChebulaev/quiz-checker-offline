"""Метрики эксперимента на простых примерах."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments"))
import run_experiment as E  # noqa: E402


def test_confusion_metrics():
    y = np.array([1, 1, 0, 0])
    scores = np.array([0.9, 0.4, 0.6, 0.1])
    m = E.confusion(y, scores, 0.5)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == m["recall"] == m["f1"] == m["accuracy"] == 0.5


def test_best_threshold_separates_classes():
    y = np.array([1, 1, 1, 0, 0, 0])
    scores = np.array([0.9, 0.8, 0.75, 0.5, 0.4, 0.2])
    thr = E.best_threshold(y, scores)
    assert 0.5 < thr <= 0.75
    assert E.confusion(y, scores, thr)["f1"] == 1.0


def test_cross_validation_groups_by_question():
    rng = np.random.default_rng(0)
    groups = np.repeat(np.arange(10), 4)
    y = np.tile([1, 1, 0, 0], 10)
    scores = y * 0.5 + rng.random(40) * 0.4
    cv = E.cross_validated(y, scores, groups)
    assert 0.0 <= cv["cv_f1"] <= 1.0 and cv["cv_f1"] > 0.8


def test_dataset_is_consistent():
    rows = E.load_dataset()
    assert len(rows) == 200 and len({r["question_id"] for r in rows}) == 25
    labels = {r["category"]: r["label"] for r in rows}
    assert labels["para"] == labels["short"] == 1
    assert labels["neg"] == labels["wrong"] == labels["off"] == labels["partial"] == 0
