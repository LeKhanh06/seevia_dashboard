"""STT hai nhánh song song + chấm chốt chỗ mâu thuẫn (docs/2026-10-22-stt-hai-nhanh.md).

Nhánh 1 = Zipformer-vi 30M (`pipeline/stt.py`, beam + hotword). Nhánh 2 = model thứ hai (mặc định Zipformer-vi
70M) chạy CÙNG audio, thread riêng. Hai chuỗi từ được căn chỉnh theo từ (Levenshtein):
  - cả câu trùng -> lấy luôn, không gọi ai;
  - có đoạn mâu thuẫn -> dựng các câu ứng viên chỉ từ hai phương án của mỗi đoạn (không bịa từ mới),
    rồi `STT_ADJUDICATE` chọn:
      llm   Gemma `generate_choice` chọn MỘT số ứng viên (có xác suất); quá `STT_ADJUDICATE_TIMEOUT_S`, lỗi
            hoặc xác suất < `STT_ADJUDICATE_MIN_PROB` thì lùi về `conf`;
      conf  bỏ phiếu theo độ tin cậy của từng model (log-prob trung bình của từ trong đoạn), không gọi LLM;
      none  luôn lấy nhánh 1 (= tắt nhánh 2 về kết quả).
Phần thuần (căn chỉnh, dựng ứng viên, bỏ phiếu) không phụ thuộc sherpa/LLM nên test được riêng.
"""

import asyncio
import glob
import itertools
import logging
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

import numpy as np

import config
from services import llm_usage

logger = logging.getLogger("blind_assist")


# ── từ + độ tin cậy ─────────────────────────────────────────────────────────

@dataclass
class Word:
    text: str          # chữ thô của model (CHỮ HOA)
    logp: float = 0.0  # log-prob thấp nhất trong các token của từ (0 = chắc chắn / không biết)

    @property
    def key(self) -> str:
        return norm_word(self.text)


def norm_word(word: str) -> str:
    return unicodedata.normalize("NFC", word.lower())


def words_of(text: str) -> list[Word]:
    return [Word(w) for w in re.sub(r"[^\w\s]", " ", text or "").split()]


@dataclass
class Hyp:
    text: str = ""
    words: list[Word] = field(default_factory=list)
    ms: float = 0.0


def rich_result(result, ms: float = 0.0) -> Hyp:
    """Kết quả sherpa -> Hyp, gộp token BPE thành từ (token mở từ có khoảng trắng đứng đầu)."""
    text = (result.text or "").strip()
    toks = list(getattr(result, "tokens", None) or [])
    lps = list(getattr(result, "ys_log_probs", None) or [])
    if not toks or len(lps) != len(toks) or " ".join(t.strip() for t in toks if t.strip()).replace(" ", "") \
            != text.replace(" ", ""):
        # Không khớp token <-> chữ (hotword/lạ): vẫn có từ, chỉ mất độ tin cậy.
        return Hyp(text, words_of(text), ms)
    out: list[Word] = []
    cur: list[str] = []
    cur_lp = 0.0
    for tok, lp in zip(toks, lps):
        if tok[:1].isspace() and cur:
            out.append(Word("".join(cur).strip(), cur_lp))
            cur, cur_lp = [], 0.0
        cur.append(tok)
        cur_lp = min(cur_lp, lp)
    if cur:
        out.append(Word("".join(cur).strip(), cur_lp))
    return Hyp(text, out, ms)


# ── căn chỉnh hai chuỗi từ ──────────────────────────────────────────────────

@dataclass
class Seg:
    a: list[Word]
    b: list[Word]
    same: bool


def align(a: list[Word], b: list[Word]) -> list[Seg]:
    """Levenshtein theo từ -> dãy đoạn xen kẽ trùng / mâu thuẫn (đoạn mâu thuẫn liền nhau được gộp)."""
    n, m = len(a), len(b)
    ka, kb = [w.key for w in a], [w.key for w in b]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (ka[i - 1] != kb[j - 1]))
    ops: list[tuple[str, int, int]] = []  # ("=", i, j) | ("x", i, j) thế | ("d", i, -1) | ("i", -1, j)
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ka[i - 1] == kb[j - 1] and dp[i][j] == dp[i - 1][j - 1]:
            ops.append(("=", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            ops.append(("x", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            ops.append(("d", i - 1, -1))
            i -= 1
        else:
            ops.append(("i", -1, j - 1))
            j -= 1
    ops.reverse()
    segs: list[Seg] = []
    for kind, ia, ib in ops:
        if kind == "=":
            segs.append(Seg([a[ia]], [b[ib]], True))
            continue
        if not segs or segs[-1].same:
            segs.append(Seg([], [], False))
        if ia >= 0:
            segs[-1].a.append(a[ia])
        if ib >= 0:
            segs[-1].b.append(b[ib])
    # gộp các từ trùng liền nhau thành một đoạn trùng
    merged: list[Seg] = []
    for s in segs:
        if s.same and merged and merged[-1].same:
            merged[-1].a += s.a
            merged[-1].b += s.b
        else:
            merged.append(s)
    return merged


def conflicts(segs: list[Seg]) -> int:
    return sum(1 for s in segs if not s.same)


def _join(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def build_candidates(segs: list[Seg], max_conflicts: int = 3) -> list[tuple[str, tuple[int, ...]]]:
    """Các câu ứng viên = chọn phương án a(0)/b(1) cho từng đoạn mâu thuẫn. Trả [(câu, lựa chọn)].

    Ứng viên đầu là toàn nhánh 1, thứ hai toàn nhánh 2. Quá `max_conflicts` đoạn thì chỉ giữ hai câu đó
    (2^k bùng nổ, và nhiều đoạn sai cùng lúc thường là câu hỏng chứ không phải lệnh)."""
    k = conflicts(segs)
    if k == 0:
        return [(_join([w for s in segs for w in s.a]), ())]
    picks = list(itertools.product((0, 1), repeat=k)) if k <= max_conflicts else [(0,) * k, (1,) * k]
    picks.sort(key=lambda p: (sum(p), p))  # (0,..,0) trước, (1,..,1) cuối: nhánh 1 đứng đầu
    out, seen = [], set()
    for p in picks:
        it = iter(p)
        words: list[Word] = []
        for s in segs:
            words += s.a if s.same else (s.a, s.b)[next(it)]
        text = _join(words)
        key = norm_word(text)
        if key not in seen:
            seen.add(key)
            out.append((text, p))
    # nhánh 2 toàn phần đứng thứ hai cho dễ đọc trong prompt
    out.sort(key=lambda x: (0 if sum(x[1]) == 0 else 1 if sum(x[1]) == k else 2, x[1]))
    return out


def side_score(words: list[Word], empty_score: float) -> float:
    """Độ tin của một phương án trong đoạn mâu thuẫn = log-prob trung bình các từ (rỗng = `empty_score`)."""
    if not words:
        return empty_score
    return sum(w.logp for w in words) / len(words)


def vote_conf(segs: list[Seg], bias: float = 0.0, empty_score: float = -0.5) -> str:
    """Bỏ phiếu theo độ tin cậy: mỗi đoạn mâu thuẫn lấy phía có điểm cao hơn (`bias` cộng cho nhánh 2)."""
    words: list[Word] = []
    for s in segs:
        if s.same:
            words += s.a
        else:
            words += s.b if side_score(s.b, empty_score) + bias > side_score(s.a, empty_score) else s.a
    return _join(words)


# ── Gemma chấm chốt ─────────────────────────────────────────────────────────

@dataclass
class Decision:
    text: str
    source: str            # same | b1 | b2 | conf | llm | fallback_<lý do>
    conflicts: int = 0
    n_cands: int = 1
    prob: float | None = None
    llm_ms: float = 0.0
    cands: list[str] = field(default_factory=list)


def _prompt(cands: list[str], device: dict | None) -> str:
    from models.clef import LABELS

    d = device or {}
    abilities = "; ".join(f"{v}" for k, v in LABELS.items() if k not in ("chat",))
    ctx = (f"đang phát nhạc={'có' if d.get('music_playing') else 'không'}, "
           f"đang chỉ đường={'có' if d.get('navigating') else 'không'}, "
           f"đang bật cảnh báo vật cản={'có' if d.get('alerting') else 'không'}, "
           f"chuyến xe chờ xác nhận={'có' if d.get('ride_quote_pending') else 'không'}")
    lines = "\n".join(f"{i}. {c.lower()}" for i, c in enumerate(cands, 1))
    return (
        "Một người khiếm thị đeo kính hỗ trợ vừa nói MỘT câu tiếng Việt với kính. Hai bộ nhận dạng giọng nói "
        "nghe ra các phiên bản KHÁC NHAU; mỗi bộ hay nghe sai vài từ (sai dấu thanh, nhầm từ gần âm, rớt từ "
        "đầu câu).\n"
        f"Kính làm được: {abilities}. Ngoài ra người dùng còn hỏi đáp, trò chuyện bình thường.\n"
        f"Bối cảnh thiết bị: {ctx}.\n"
        "Chọn phiên bản nhiều khả năng là câu người dùng THẬT SỰ nói: tự nhiên, đúng ngữ pháp, có nghĩa; ưu tiên "
        "câu lệnh/câu hỏi hợp với chức năng của kính hơn câu vô nghĩa.\n"
        f"{lines}\n"
        "Trả số của phiên bản đúng nhất."
    )


async def llm_probs(cands: list[str], device: dict | None) -> tuple[list[float], float]:
    """Xác suất Gemma gán cho từng ứng viên (một thứ tự) = softmax trên nhãn "1".."n" của token đầu tiên.

    `structured_outputs: {choice}` ép đáp án nằm trong tập nhãn; `top_logprobs=n` cho cả phân phối chứ không
    chỉ nhãn thắng (cần để lấy trung bình hai thứ tự, xem `scores`). Trả (xác suất, ms)."""
    from models import llm

    labels = [str(i) for i in range(1, len(cands) + 1)]
    t0 = time.perf_counter()
    with llm_usage.purpose("stt_pick"):
        choice = await llm.complete_raw(
            [llm.user_message(_prompt(cands, device))], max_tokens=1, temperature=0.0, logprobs=True,
            top_logprobs=min(len(labels), 8), structured_outputs={"choice": labels})
    ms = (time.perf_counter() - t0) * 1000
    items = (choice.get("logprobs") or {}).get("content") or []
    top = {t.get("token", "").strip(): t.get("logprob", -30.0) for t in (items[0].get("top_logprobs") or [])} \
        if items else {}
    chosen = (choice["message"].get("content") or "").strip()
    if not top:
        if chosen not in labels:
            raise ValueError(f"Gemma không trả nhãn: {chosen!r}")
        return [1.0 if lb == chosen else 0.0 for lb in labels], ms
    raw = [math.exp(top.get(lb, -30.0)) for lb in labels]
    total = sum(raw)
    if total <= 0:
        raise ValueError("không có xác suất nhãn")
    return [x / total for x in raw], ms


async def scores(cands: list[str], device: dict | None, timeout: float, probs=None,
                 debias: bool | None = None) -> tuple[list[float], float]:
    """Điểm của từng ứng viên = trung bình xác suất qua thứ tự xuôi và đảo (hai lệnh gọi SONG SONG).

    Đo 2026-10-22: Gemma thiên vị vị trí mạnh (đổi thứ tự hai ứng viên thì 500/1085 lượt đổi đáp án, và nghiêng
    về ứng viên đứng SAU); trung bình hai thứ tự khử độ lệch đó mà không thêm trễ (song song)."""
    n = len(cands)
    probs = probs or llm_probs
    debias = config.STT_ADJUDICATE_DEBIAS if debias is None else debias
    orders = [list(range(n))] + ([list(range(n))[::-1]] if debias and n > 1 else [])
    results = await asyncio.wait_for(
        asyncio.gather(*(probs([cands[i] for i in o], device) for o in orders)), timeout)
    avg = [0.0] * n
    for o, (pr, _) in zip(orders, results):
        for pos, idx in enumerate(o):
            avg[idx] += pr[pos] / len(orders)
    return avg, max(ms for _, ms in results)


# Động từ DỪNG/TẮT: một nhánh nghe ra mà nhánh kia không thì giữ — nghe rớt "Tắt"/"Dừng" biến lệnh tắt thành lệnh bật
# ("Tắt cảnh báo" -> "Cảnh báo" -> alert_on), còn nghe thừa thì tệ nhất là dừng nhầm một thứ có thể bật lại.
STOP_WORDS = frozenset({"tắt", "dừng", "ngừng", "ngưng", "huỷ", "hủy"})


def _protect(segs: list[Seg]) -> list[Seg]:
    """Giải trước (không qua Gemma) các đoạn mâu thuẫn làm mất lệnh dừng:
    - `STT_ADJUDICATE_KEEP_HEAD`: nhánh rớt từ ĐẦU câu (đoạn đầu chỉ một phía có chữ) -> giữ phần đầu của nhánh còn đủ;
    - `STT_ADJUDICATE_KEEP_STOP`: đoạn mà đúng một phía chứa động từ dừng/tắt -> giữ phía đó."""
    out = []
    for i, sg in enumerate(segs):
        if not sg.same:
            if config.STT_ADJUDICATE_KEEP_HEAD and i == 0 and not (sg.a and sg.b):
                keep = sg.a or sg.b
                sg = Seg(keep, keep, True)
            elif config.STT_ADJUDICATE_KEEP_STOP:
                sa = any(w.key in STOP_WORDS for w in sg.a)
                sb = any(w.key in STOP_WORDS for w in sg.b)
                if sa != sb:
                    keep = sg.a if sa else sg.b
                    sg = Seg(keep, keep, True)
        out.append(sg)
    return out


async def decide(h1: Hyp, h2: Hyp | None, device: dict | None = None, mode: str | None = None,
                 probs=None) -> Decision:
    """Hợp hai nhánh thành MỘT câu. `probs` thay được trong test/bench (cùng chữ ký với `llm_probs`)."""
    mode = mode or config.STT_ADJUDICATE
    if h2 is None or mode == "none" or not h2.text.strip():
        return Decision(h1.text, "b1")
    if not h1.text.strip():
        return Decision(h2.text, "b2")
    segs = align(h1.words, h2.words)
    k = conflicts(segs)
    if k == 0:
        return Decision(h1.text, "same")
    segs = _protect(segs)
    k2 = conflicts(segs)
    if k2 == 0:
        return Decision(_join([w for sg in segs for w in sg.a]), "protect", k, 1)
    k = k2
    fallback = vote_conf(segs, config.STT_VOTE_BIAS, config.STT_VOTE_EMPTY)
    if mode != "llm" or (config.STT_ADJUDICATE_SCOPE == "command" and not touches_command(segs)):
        return Decision(fallback, "conf", k, 1)
    cands = build_candidates(segs, config.STT_ADJUDICATE_MAX_CONFLICTS)
    texts = [t for t, _ in cands]
    started = time.perf_counter()
    try:
        sc, ms = await scores(texts, device, config.STT_ADJUDICATE_TIMEOUT_S, probs)
    except Exception as exc:  # noqa: BLE001 - quá hạn/429/mạng: lùi về bỏ phiếu độ tin cậy
        ms = (time.perf_counter() - started) * 1000
        logger.info("STT dual: Gemma chấm lỗi (%s: %s) -> lùi về bỏ phiếu độ tin cậy", type(exc).__name__, exc)
        return Decision(fallback, f"fallback_{type(exc).__name__}", k, len(texts), None, ms, texts)
    best = max(range(len(texts)), key=lambda i: sc[i])
    if sc[best] < config.STT_ADJUDICATE_MIN_PROB:
        return Decision(fallback, "fallback_lowprob", k, len(texts), sc[best], ms, texts)
    return Decision(texts[best], "llm", k, len(texts), sc[best], ms, texts)


_command_words: frozenset[str] | None = None


def command_words() -> frozenset[str]:
    """Từ nằm trong cụm lệnh (hotword lệnh cố định) — để `STT_ADJUDICATE_SCOPE=command` chỉ chấm khi
    mâu thuẫn chạm vào lệnh."""
    global _command_words
    if _command_words is None:
        from pipeline import stt

        _command_words = frozenset(norm_word(w) for phrase in stt.COMMAND_HOTWORDS for w in phrase.split())
    return _command_words


def touches_command(segs: list[Seg]) -> bool:
    cw = command_words()
    return any(w.key in cw for s in segs if not s.same for w in s.a + s.b)


# ── nhánh 2: nạp và giải mã ─────────────────────────────────────────────────

def _pick_file(model_dir: str, pattern: str, prefer: str = "") -> str:
    files = sorted(glob.glob(os.path.join(model_dir, pattern)))
    if prefer:
        files = [f for f in files if prefer in os.path.basename(f)] or files
    if not files:
        raise FileNotFoundError(f"không thấy {pattern} trong {model_dir}")
    return files[0]


_asr2 = None
_biasing2 = False


def _load_asr2():
    """Recognizer nhánh 2 (Zipformer transducer khác, sherpa-onnx CPU như nhánh 1)."""
    global _asr2, _biasing2
    if _asr2 is not None:
        return _asr2
    import onnxruntime  # noqa: F401  (nạp TRƯỚC sherpa_onnx, xem pipeline/stt.py)
    from sherpa_onnx import OfflineRecognizer

    from pipeline import stt

    d = config.STT_DUAL_MODEL_DIR
    method = config.STT_DUAL_DECODING
    extra = {}
    if method == "modified_beam_search":
        vocab = stt._bpe_vocab(d, "bpe2.vocab")
        if vocab is None:
            method = "greedy_search"
        else:
            extra = dict(max_active_paths=config.STT_BEAM_PATHS, modeling_unit="bpe", bpe_vocab=vocab,
                         hotwords_score=config.STT_HOTWORDS_SCORE)
            if config.STT_DUAL_HOTWORDS and config.STT_HOTWORDS:
                extra["hotwords_file"] = stt._hotwords_file()
    _biasing2 = method == "modified_beam_search"
    _asr2 = OfflineRecognizer.from_transducer(
        encoder=_pick_file(d, "encoder*.int8.onnx"),
        decoder=_pick_file(d, "decoder*.onnx", prefer=""),
        joiner=_pick_file(d, "joiner*.int8.onnx"),
        tokens=os.path.join(d, "tokens.txt"),
        num_threads=config.STT_DUAL_NUM_THREADS, sample_rate=16000, feature_dim=80,
        decoding_method=method, **extra)
    return _asr2


def decode_branch1(samples: np.ndarray, hotwords=None) -> Hyp:
    """Nhánh 1 (đúng recognizer của `stt.decode_samples`) kèm độ tin cậy từng từ."""
    from pipeline import stt

    rec = stt._load_asr()
    t0 = time.perf_counter()
    stream = rec.create_stream("\n".join(hotwords)) if hotwords and stt._biasing and config.STT_USER_HOTWORDS \
        else rec.create_stream()
    stream.accept_waveform(16000, samples)
    rec.decode_stream(stream)
    return rich_result(stream.result, (time.perf_counter() - t0) * 1000)


def decode_branch2(samples: np.ndarray, hotwords=None) -> Hyp:
    rec = _load_asr2()
    t0 = time.perf_counter()
    stream = rec.create_stream("\n".join(hotwords)) if hotwords and _biasing2 and config.STT_USER_HOTWORDS \
        else rec.create_stream()
    stream.accept_waveform(16000, samples)
    rec.decode_stream(stream)
    return rich_result(stream.result, (time.perf_counter() - t0) * 1000)


def decode_both(samples: np.ndarray, hotwords=None) -> tuple[Hyp, Hyp]:
    """Hai nhánh CHẠY SONG SONG (sherpa nhả GIL lúc giải), đợi đủ cả hai -> (nhánh 1, nhánh 2). Dùng cho bench;
    đường production dùng `submit_branch2` để giới hạn thời gian chờ nhánh 2."""
    fut = submit_branch2(samples, hotwords)
    h1 = decode_branch1(samples, hotwords)
    return h1, fut.result()


_executor = None


def _pool():
    # MỘT luồng cho nhánh 2: recognizer không bị hai lượt giải đè nhau, và nhánh 2 quá hạn (bị bỏ phiếu trắng) không
    # chất thêm việc song song lên CPU đang chật.
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor

        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt2")
    return _executor


def submit_branch2(samples: np.ndarray, hotwords=None):
    return _pool().submit(decode_branch2, samples, hotwords)


def preload() -> None:
    _load_asr2()


def warm() -> bool:
    """Nạp nhánh 2 và chạy thử 1 lần mỗi nhánh. Lỗi (thiếu model/gói) -> tắt `config.STT_DUAL`, trả False."""
    try:
        silence = np.zeros(16000, dtype=np.float32)
        decode_branch1(silence)
        decode_branch2(silence)
        return True
    except Exception:  # noqa: BLE001 - STT hai nhánh là phần thêm, không được làm sập server
        logger.exception("STT dual: không nạp được nhánh 2 (%s) -> tắt STT_DUAL, chỉ chạy nhánh 1",
                         config.STT_DUAL_MODEL_DIR)
        config.STT_DUAL = False
        return False
