"""Huấn luyện mô hình gần âm của `pipeline/hearfix.py` từ dữ liệu intent có sẵn.

    ~/.venvs/seevia/bin/python -m tools.train_hearfix            # ghi data/intent/hearfix.npz (dùng TẤT CẢ dữ liệu)

Nguồn (`data/intent/`): `train`, `eval`, `gold_dev`, `gold_test` (nhãn intent) + `params_*` (câu chat / hướng âm lượng /
huỷ-xác nhận xe, dùng như câu mẫu của lớp tương ứng). Chỉ cần sklearn lúc HUẤN LUYỆN; lúc chạy chỉ cần numpy.
`tools/eval_hearfix.py` dùng `train()` với danh mục loại bớt câu của bàn đo để đo công bằng.
"""

import json
import sys
import zlib
from collections import Counter
from pathlib import Path

import numpy as np
from scipy import sparse
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import hearfix  # noqa: E402

DATA = ROOT / "data" / "intent"
C_REG = 30.0


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


def sources() -> dict[str, list[tuple[str, str]]]:
    """Tên nguồn -> [(câu, nhãn)]. Tách nguồn để bàn đo có thể bỏ riêng gold_dev / gold_test."""
    out = {
        "train": [(r["text"], r["label"]) for r in _load("train")],
        "eval": [(r["text"], r["label"]) for r in _load("eval")],
        "params": (
            [(r["text"], "chat") for r in _load("params_chat_web") + _load("params_chat_location")]
            + [(r["text"], "ride_confirm") for r in _load("params_ride_confirm")]
            + [(r["text"], "music_volume") for r in _load("params_volume_direction")]
        ),
        "gold_dev": [(r["source_text"], r["label"]) for r in _load("gold_dev")],
        "gold_test": [(r["source_text"], r["label"]) for r in _load("gold_test")],
    }
    return out


def catalog(names=None, exclude: set[str] | None = None, cap: int | None = None) -> list[tuple[str, str]]:
    """Danh mục (câu, nhãn) không trùng; `exclude` = tập câu đã `fold` cần loại (câu của bàn đo).

    `cap`: tối đa số câu mỗi nhãn LỆNH (chọn tất định theo crc32; `chat` không cắt) — các tệp `params_*` sinh hàng trăm
    câu cho `music_volume` / `ride_confirm`, kéo mô hình lệch về hai nhãn đó nếu không cắt.
    """
    exclude = exclude or set()
    src = sources()
    seen: set[tuple[str, str]] = set()
    out = []
    for name in names or src:
        for text, label in src[name]:
            key = hearfix.fold(text)
            if not key or key in exclude or (key, label) in seen:
                continue
            seen.add((key, label))
            out.append((text, label))
    if cap:
        by_label: dict[str, list[tuple[str, str]]] = {}
        for item in out:
            by_label.setdefault(item[1], []).append(item)
        out = [it for label, items in by_label.items()
               for it in (items if label == "chat" else sorted(items, key=lambda it: zlib.crc32(it[0].encode()))[:cap])]
    return out


def _fit_part(kind: str, texts: list[str], y: np.ndarray, classes: list[str]) -> hearfix._Linear:
    featurize = hearfix._FEATURIZERS[kind]
    docs = [Counter(featurize(t)) for t in texts]
    df = Counter(f for d in docs for f in d)
    vocab = sorted(df)
    index = {f: i for i, f in enumerate(vocab)}
    n = len(docs)
    idf = np.array([np.log((1 + n) / (1 + df[f])) + 1.0 for f in vocab])
    rows, cols, vals = [], [], []
    for r, d in enumerate(docs):
        ids = [index[f] for f in d]
        v = np.array([1.0 + np.log(c) for c in d.values()]) * idf[ids]
        v /= np.linalg.norm(v)
        rows += [r] * len(ids)
        cols += ids
        vals += list(v)
    X = sparse.csr_matrix((vals, (rows, cols)), shape=(n, len(vocab)))
    lr = LogisticRegression(C=C_REG, max_iter=3000).fit(X, y)
    # LogisticRegression.classes_ đã sắp xếp; ép theo thứ tự `classes` chung của cả hai mô hình.
    order = [list(lr.classes_).index(c) for c in classes]
    w = lr.coef_[order].T if len(lr.classes_) > 2 else np.vstack([-lr.coef_[0], lr.coef_[0]]).T
    b = lr.intercept_[order]
    return hearfix._Linear(kind, vocab, idf, w, b)


def train(cat: list[tuple[str, str]]) -> hearfix.Model:
    classes = sorted({label for _, label in cat})
    texts = [t for t, _ in cat]
    y = np.array([label for _, label in cat])
    return hearfix.Model(classes, [_fit_part(kind, texts, y, classes) for kind in ("word", "char")])


CAP = 150


def main() -> None:
    cat = catalog(cap=CAP)
    print(f"danh mục: {len(cat)} câu", dict(Counter(label for _, label in cat)))
    model = train(cat)
    model.save(hearfix.MODEL_PATH)
    print("đã ghi", hearfix.MODEL_PATH, f"{hearfix.MODEL_PATH.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main()
