"""V4: câu cố định nói tới kính (người khiếm thị chỉ NGHE): ngắn, không bảo xem/bấm điện thoại, không thuật ngữ."""

import re

import pytest

import app
from pipeline import phrases

# Cue (cues.py) là clip nằm trong flash kính, đổi chữ phải sinh lại clip + tăng CUE_VERSION: ngoài phạm vi kiểm tra này.
from pipeline import cues

FORBIDDEN = re.compile(
    r"bấm|nhấn|xem (?:điện thoại|màn hình|thông báo)|kiểm tra (?:thông báo|điện thoại)|thông báo trên|"
    r"token|màn hình|mã lỗi|http|\b4\d\d\b|\b5\d\d\b|api|json|timeout|exception",
    re.I,
)
MAX_WORDS = 17

# Ngoại lệ có chủ đích (người dùng chốt 2026-11-16): Grab không có API đặt xe công khai, chuyến chỉ thành khi chính
# người dùng bấm "Đặt xe" trong Grab trên điện thoại. Nói thẳng việc phải làm còn hơn báo bừa "đã đặt xe".
# Chỉ miễn đúng cụm "Bấm Đặt xe trong Grab"; mọi câu khác vẫn bị cấm bảo bấm/xem điện thoại.
_GRAB_TAP = re.compile(r"Bấm Đặt xe trong Grab")


def _phrases() -> list[str]:
    out: list[str] = []
    for module in phrases._speech_modules():
        if module is cues:
            continue
        out.extend(getattr(module, "STATIC_SPEECH", ()))
    out.extend(app.STATIC_SPEECH)
    return sorted({p for p in out if p.strip()})


@pytest.mark.parametrize("text", _phrases())
def test_phrase_is_short_and_speakable(text):
    checked = _GRAB_TAP.sub("", text)
    assert not FORBIDDEN.search(checked), f"câu nói có thuật ngữ / bảo xem-bấm điện thoại: {text!r}"
    assert len(text.split()) <= MAX_WORDS, f"quá dài ({len(text.split())} từ): {text!r}"


def test_grab_exception_is_narrow():
    """Ngoại lệ Grab không mở cửa cho câu bảo bấm thứ khác."""
    assert FORBIDDEN.search(_GRAB_TAP.sub("", "Bấm Đặt xe trong Grab, rồi bấm vào thông báo."))
    assert not FORBIDDEN.search(_GRAB_TAP.sub("", "Bấm Đặt xe trong Grab để gọi xe."))


def test_there_are_many_phrases_checked():
    assert len(_phrases()) > 60
