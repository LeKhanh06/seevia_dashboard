"""Cứu lệnh bị STT nghe sai (V2): khớp gần âm KHÔNG DẤU với câu lệnh mẫu của từng intent.

Chỉ chạy khi bộ phân loại đã cho `chat` (không bao giờ đổi nhãn khác). Hai mô hình tuyến tính nhỏ, huấn luyện offline
từ `data/intent/*.jsonl` (xem `tools/train_hearfix.py`), trung bình xác suất:
- mô hình từ: từ + cặp từ đã bỏ dấu (tiếng Việt bỏ dấu thanh/dấu mũ vẫn đọc được, và STT hay sai đúng chỗ này);
- mô hình ký tự: n-gram ký tự 2-4 trên dạng "gần âm" (gộp tr/ch, x/s, gi/d/r, ng/nh cuối vần, c/ch/t cuối vần...).

`chat` là một lớp như mọi lớp khác nên câu hỏi kiến thức ("Vịnh Hạ Long thuộc tỉnh nào") được bảo vệ bằng chính dữ liệu
chat. Suy luận bằng numpy, ~0,2 ms một câu.

Đầu ra `suggest()` có ba mức:
- `act`:     khớp mạnh, lệnh không tác dụng phụ hoặc là lệnh dừng/tắt -> chạy luôn;
- `confirm`: khớp mạnh, lệnh có tác dụng phụ (gọi, nhắn, đặt xe, dẫn đường, khẩn cấp, nhạc) -> hỏi "... phải không?";
- `options`: khớp yếu nhưng có manh mối -> đọc lại điều nghe được + tối đa 3 lựa chọn.
"""

import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config

logger = logging.getLogger("blind_assist")

MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "intent" / "hearfix.npz"

# Lệnh chạy luôn khi khớp mạnh: không tác dụng phụ ra ngoài, hoặc là lệnh dừng/tắt đảo ngược được.
ACT_INTENTS = frozenset({
    "space", "hazard", "ocr", "money", "find", "nav_stop", "alert_on", "alert_off", "music_stop", "music_volume",
})
# Lệnh có tác dụng phụ: chỉ hỏi xác nhận bằng đoán tốt nhất (cơ chế `ask_confirm` / `is_yes`).
CONFIRM_INTENTS = frozenset({
    "contact_call", "emergency_call", "message_send", "message_read", "ride_quote", "ride_confirm", "music_play",
    "nav_start",
})

# Tên nói được của từng intent, dùng trong câu hỏi lựa chọn / xác nhận.
SPOKEN = {
    "space": "tả cảnh", "hazard": "quét vật cản", "ocr": "đọc chữ", "money": "đọc tiền", "find": "tìm đồ",
    "nav_stop": "dừng chỉ đường", "alert_on": "bật cảnh báo", "alert_off": "tắt cảnh báo",
    "music_stop": "tắt nhạc", "music_volume": "chỉnh âm lượng", "music_play": "mở nhạc",
    "contact_call": "gọi điện", "message_send": "nhắn tin", "message_read": "nghe tin nhắn",
    "ride_quote": "đặt xe", "ride_confirm": "xác nhận chuyến xe", "nav_start": "chỉ đường",
    "emergency_call": "gọi khẩn cấp",
}


# ── chuẩn hoá ────────────────────────────────────────────────────────────────

def fold(text: str) -> str:
    """Chữ thường, bỏ dấu (kể cả dấu mũ/móc), đ -> d, bỏ dấu câu."""
    text = unicodedata.normalize("NFD", (text or "").lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


_INITIALS = (("ngh", "ng"), ("gh", "g"), ("tr", "ch"), ("ch", "ch"), ("gi", "d"), ("x", "s"), ("r", "d"),
             ("qu", "kw"), ("c", "k"), ("q", "k"))


def _syllable(word: str) -> str:
    for src, dst in _INITIALS:
        if word.startswith(src):
            word = dst + word[len(src):]
            break
    word = re.sub(r"(nh|ng)$", "n", word)
    word = re.sub(r"(ch|c|p)$", "t", word)
    return word


def phon(text: str) -> str:
    """Dạng gần âm: những cặp STT hay nhầm (tr/ch, x/s, ng/nh cuối vần...) về cùng một dạng."""
    return " ".join(_syllable(w) for w in fold(text).split())


def word_features(text: str) -> list[str]:
    toks = fold(text).split()
    return [*toks, *(f"{a} {b}" for a, b in zip(toks, toks[1:]))]


def char_features(text: str) -> list[str]:
    out: list[str] = []
    for word in phon(text).split():
        padded = f" {word} "
        for n in (2, 3, 4):
            out.extend(padded[i:i + n] for i in range(len(padded) - n + 1))
    return out


_FEATURIZERS = {"word": word_features, "char": char_features}


# ── mô hình ─────────────────────────────────────────────────────────────────

class _Linear:
    """TF-IDF (tf = 1 + log) chuẩn hoá L2 rồi hồi quy logistic đa lớp."""

    def __init__(self, kind: str, vocab: list[str], idf: np.ndarray, weights: np.ndarray, bias: np.ndarray) -> None:
        self.kind = kind
        self.featurize = _FEATURIZERS[kind]
        self.index = {f: i for i, f in enumerate(vocab)}
        self.idf, self.weights, self.bias = idf, weights, bias

    def proba(self, text: str) -> np.ndarray:
        counts = Counter(f for f in self.featurize(text) if f in self.index)
        if not counts:
            logits = self.bias
        else:
            ids = np.fromiter((self.index[f] for f in counts), dtype=np.int64, count=len(counts))
            tf = np.fromiter((1.0 + math.log(c) for c in counts.values()), dtype=np.float64, count=len(counts))
            x = tf * self.idf[ids]
            x /= np.linalg.norm(x)
            logits = x @ self.weights[ids] + self.bias
        logits = logits - logits.max()
        e = np.exp(logits)
        return e / e.sum()


class Model:
    def __init__(self, classes: list[str], parts: list[_Linear]) -> None:
        self.classes = classes
        self.parts = parts
        self.chat = classes.index("chat")

    def proba(self, text: str) -> np.ndarray:
        return sum(p.proba(text) for p in self.parts) / len(self.parts)

    # lưu / nạp (npz: mảng số + từ vựng dạng mảng chuỗi)
    def save(self, path: Path) -> None:
        arrays: dict[str, np.ndarray] = {"classes": np.array(self.classes), "kinds": np.array([p.kind for p in self.parts])}
        for k, p in enumerate(self.parts):
            vocab = sorted(p.index, key=p.index.get)
            arrays[f"vocab{k}"] = np.array(vocab)
            arrays[f"idf{k}"] = p.idf.astype(np.float32)
            arrays[f"w{k}"] = p.weights.astype(np.float32)
            arrays[f"b{k}"] = p.bias.astype(np.float32)
        np.savez_compressed(path, **arrays)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "Model":
        with np.load(path, allow_pickle=False) as z:
            classes = [str(c) for c in z["classes"]]
            parts = [
                _Linear(str(kind), [str(v) for v in z[f"vocab{k}"]], z[f"idf{k}"].astype(np.float64),
                        z[f"w{k}"].astype(np.float64), z[f"b{k}"].astype(np.float64))
                for k, kind in enumerate(z["kinds"])
            ]
        return cls(classes, parts)


_model: Model | None = None
_model_failed = False


def model() -> Model | None:
    """Nạp lười; thiếu file/hỏng thì tắt tính năng (không bao giờ làm hỏng lượt)."""
    global _model, _model_failed
    if _model is None and not _model_failed:
        try:
            _model = Model.load()
        except Exception as exc:  # noqa: BLE001
            _model_failed = True
            logger.warning("hearfix: không nạp được mô hình (%r), tắt cứu lệnh nghe sai", exc)
    return _model


# ── quyết định ──────────────────────────────────────────────────────────────

@dataclass
class Suggestion:
    kind: str  # "act" | "confirm" | "options"
    intent: str  # đoán tốt nhất
    prob: float
    options: list[str] = field(default_factory=list)  # chỉ có ở "options": intent theo thứ tự xác suất


def _allowed(intent: str, state: dict) -> bool:
    """Lệnh vô nghĩa với trạng thái kính thì không đề xuất: xác nhận xe khi chưa có báo giá."""
    if intent == "ride_confirm":
        return bool(state.get("ride_quote_pending"))
    return True


def decide(probs: np.ndarray, classes: list[str], state: dict | None = None, words: int = 0) -> Suggestion | None:
    """`words`: số từ của câu nghe được; câu dài hơn `HEARFIX_MAX_WORDS` là lời nói thường, không phải lệnh nghe sai."""
    state = state or {}
    if words > config.HEARFIX_MAX_WORDS:
        return None
    ranked = sorted(
        ((float(probs[i]), c) for i, c in enumerate(classes) if c != "chat" and _allowed(c, state)), reverse=True,
    )
    if not ranked:
        return None
    p_top, top = ranked[0]
    p_chat = float(probs[classes.index("chat")])
    if p_top <= p_chat:
        return None
    if p_top >= config.HEARFIX_STRONG:
        return Suggestion("act" if top in ACT_INTENTS else "confirm", top, p_top)
    if p_top >= config.HEARFIX_WEAK:
        options = [c for p, c in ranked[: config.HEARFIX_MAX_OPTIONS] if p >= config.HEARFIX_OPTION_MIN]
        return Suggestion("options", top, p_top, options or [top])
    return None


def suggest(text: str, state: dict | None = None) -> Suggestion | None:
    """Câu nghe được (đã `chat`) -> gợi ý, hoặc None nếu nên để nguyên (trả lời như chat)."""
    if not config.HEARFIX_ENABLED or not (text or "").strip():
        return None
    m = model()
    if m is None:
        return None
    return decide(m.proba(text), m.classes, state, len(text.split()))


# ── lời nói ─────────────────────────────────────────────────────────────────

def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " hay " + names[-1]


def options_question(heard: str, intents: list[str]) -> str:
    """"Mình nghe là 'tiêu đê bảo'. Bạn muốn đọc chữ, tìm đồ hay tả cảnh?"."""
    heard = " ".join((heard or "").split()).strip(" .,!?\"'")
    return f"Mình nghe là '{heard}'. Bạn muốn {_join([SPOKEN[i] for i in intents])}?"


def generic_confirm(intent: str) -> str:
    """Câu hỏi xác nhận khi chưa trích được tên người / địa điểm / bài hát."""
    return f"Bạn muốn {SPOKEN[intent]} phải không?"
