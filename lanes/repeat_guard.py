"""Chặn Gemma sinh vòng lặp khi đọc ảnh ("Coca-Cola" x100, "K2 - K3 - K4 ... K28", "..." mãi).

Đo 2026-10-22 trên VinText (120 ảnh x 5 cỡ): 6/600 lượt đọc chữ chạy tới trần 1024 token toàn dòng lặp; người khiếm thị
nghe ~25 s tiếng lặp và không có cách ngắt. Bộ chặn nhìn đuôi văn bản đã sinh; thấy lặp thì dừng stream và cắt phần lặp,
chỉ giữ lần xuất hiện đầu. Thuần chuỗi, vài chục micro giây mỗi lần kiểm.
"""

import re

# Số lần liên tiếp tối thiểu một đơn vị phải lặp mới coi là vòng lặp, theo độ dài chu kỳ (1 dòng lặp 4 lần; "A\nB" lặp 3 lần).
_MIN_REPEATS = {1: 4, 2: 3, 3: 3}
_UNIT = re.compile(r"[^\n]+")
_SEQ_ITEM = re.compile(r"([A-Za-zÀ-ỹ]{0,3})(\d{1,4})")
_SEQ_MIN = 6  # chuỗi đánh số tăng dần liên tục K2, K3, K4... từ đây là bịa


def _norm(unit: str) -> str:
    return re.sub(r"\s+", " ", unit).strip().lower()


def _line_loop(text: str) -> int | None:
    """Vị trí cắt nếu các DÒNG cuối lặp theo chu kỳ 1-3."""
    units = [(m.start(), _norm(m.group())) for m in _UNIT.finditer(text)]
    units = [(p, u) for p, u in units if u]
    for period, need in _MIN_REPEATS.items():
        if len(units) < period * need:
            continue
        tail = units[-period * need:]
        pattern = [u for _, u in tail[:period]]
        if all(tail[i][1] == pattern[i % period] for i in range(len(tail))):
            if period == 1 and len(pattern[0]) < 1:
                continue
            return tail[period][0]  # giữ một chu kỳ đầu, bỏ phần lặp
    return None


def _count_loop(text: str) -> int | None:
    """Vị trí cắt nếu đuôi là dãy đánh số +1 liên tục (K2 - K3 - K4 ...) dài >= _SEQ_MIN mục."""
    items = list(_SEQ_ITEM.finditer(text))
    if len(items) < _SEQ_MIN:
        return None
    run = [items[-1]]
    for prev, cur in zip(reversed(items[:-1]), reversed(items[1:])):
        gap = text[prev.end():cur.start()]
        if (prev.group(1) == cur.group(1) and int(cur.group(2)) == int(prev.group(2)) + 1
                and len(gap) <= 6 and not re.search(r"[.\n]", gap.strip(" -,;")) ):
            run.append(prev)
        else:
            break
    if len(run) >= _SEQ_MIN:
        return run[-3].start()  # giữ 2 mục đầu của dãy
    return None


def cut_point(text: str) -> int | None:
    """Vị trí nên cắt `text` (giữ text[:vị trí]) nếu đuôi đang lặp; None nếu bình thường."""
    if len(text) < 24:
        return None
    for fn in (_line_loop, _count_loop):
        at = fn(text)
        if at is not None:
            return at
    return None
