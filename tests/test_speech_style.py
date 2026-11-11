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
    assert not FORBIDDEN.search(text), f"câu nói có thuật ngữ / bảo xem-bấm điện thoại: {text!r}"
    assert len(text.split()) <= MAX_WORDS, f"quá dài ({len(text.split())} từ): {text!r}"


def test_there_are_many_phrases_checked():
    assert len(_phrases()) > 60
