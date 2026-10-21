"""Audio mượt: chia câu không cắt giữa cụm, chỗ nối theo loại, vuốt hai đầu, không click.

Hiện tượng gốc (2026-10-22, test người dùng giả lập): model hay ngắt dòng giữa cụm —
"... đang hiển thị thông\\nbáo tắt máy", "THÔNG\\nTIN TUYÊN TRUYỀN", "Đầu tư và\\nPhát triển ..." — và mỗi
dòng thành một lần TTS riêng cách nhau 0,45 s im lặng, nghe như khựng giữa câu.
"""
import time

import numpy as np
import pytest

import config
from pipeline import speech, tts
from pipeline.tts import SOFT_BREAK as SB
from pipeline.tts import _gap_kind, _sentences


# ── chia câu ──────────────────────────────────────────────────────────────────

def test_model_newline_before_lowercase_joins_the_phrase():
    """'... hiển thị thông' + xuống dòng + 'báo tắt máy': dòng sau viết thường = câu vẫn tiếp."""
    parts = list(_sentences(["Màn hình máy tính đang hiển thị thông" + SB + "báo tắt máy. Có một nút bấm."]))
    assert parts == ["Màn hình máy tính đang hiển thị thông báo tắt máy.", "Có một nút bấm."]


def test_phrase_cut_by_the_first_sentence_trick_is_read_at_once_not_awaited():
    """`general.short_first_sentence` cắt câu đầu ở 8 từ rồi đẩy phần sau ở chunk kế. Chờ chunk đó để nối lại
    là trễ tiếng đầu, nên cắt ngay — chỗ nối được làm liền ở tầng PCM (loại 'soft', xem `_gap_kind`)."""
    stream = _sentences(iter(["Đây là nắp của một hộp thiếc đựng" + SB, "thực phẩm. Trên nắp có in mã số."]))
    assert next(stream) == "Đây là nắp của một hộp thiếc đựng"


def test_model_newline_after_dangling_word_joins():
    """'... Đầu tư và' + 'Phát triển': câu không bao giờ kết thúc ở 'và', dù dòng sau viết hoa."""
    parts = list(_sentences(["BIDV - Ngân hàng TMCP Đầu tư và" + SB, "Phát triển Việt Nam, Chi nhánh Bạc Liêu."]))
    assert parts[0].startswith("BIDV - Ngân hàng TMCP Đầu tư và Phát triển Việt Nam")
    assert not any(p.endswith(" và") for p in parts)


def test_short_ocr_line_merges_with_the_next_one():
    """'THÔNG' (5 ký tự) đọc riêng thì TTS đọc cụt; gộp với dòng kế."""
    parts = list(_sentences(["BẢN TIN CÔNG ĐOÀN CÔNG TY 990" + SB + "THÔNG" + SB + "TIN TUYÊN TRUYỀN" + SB
                             + "HOẠT ĐỘNG CÔNG ĐOÀN"]))
    assert parts == ["BẢN TIN CÔNG ĐOÀN CÔNG TY 990", "THÔNG TIN TUYÊN TRUYỀN HOẠT ĐỘNG CÔNG ĐOÀN"]


def test_long_unpunctuated_line_is_cut_without_waiting_for_the_next_one():
    """Dòng đủ dài không dấu câu, model chưa viết gì tiếp: cắt ngay (không chờ => không trễ tiếng đầu)."""
    stream = _sentences(iter(["BẢN TIN CÔNG ĐOÀN CÔNG TY 990" + SB]))
    assert next(stream) == "BẢN TIN CÔNG ĐOÀN CÔNG TY 990"


def test_first_short_sentence_with_model_newline_is_read_immediately():
    """general.short_first_sentence đẩy 'Ngoài trời.\\n' để đọc ngay: nối nó với câu sau là trễ tiếng đầu cả giây."""
    stream = _sentences(iter(["Ngoài trời." + SB]))
    assert next(stream) == "Ngoài trời."


def test_short_numbered_item_in_the_middle_still_merges():
    parts = list(_sentences(["Các mục sau đây rất quan trọng. ", "1." + SB + "Ràng buộc bộ nhớ là bốn gigabyte."]))
    assert all(len(p) > 4 for p in parts), parts


def test_real_newline_from_handlers_still_cuts_hard():
    """'\\n' thật do handler đặt (action_flow._utterance) luôn cắt, kể cả câu rất ngắn."""
    assert next(_sentences(iter(["Đang gọi xe.\n"]))) == "Đang gọi xe."


def test_paragraph_break_cuts():
    parts = list(_sentences(["Phần một nói về cái ghế lệch" + SB + SB + "phần hai nói về cái bàn gỗ."]))
    assert len(parts) == 2


def test_clause_boundary_of_a_long_first_sentence_is_read_first():
    """Câu đầu dài chưa tới dấu chấm mà đã có dấu phẩy: đọc mệnh đề đầu ngay, tiếng đầu không chờ hết câu."""
    first = "Trước mặt bạn là một căn phòng khách rộng rãi với nhiều đồ đạc,"
    stream = _sentences(iter([first + " bên trái là kệ sách lớn đựng đầy sách, bên phải là cửa sổ"]))
    assert next(stream) == first


def test_short_first_sentence_with_comma_is_not_split():
    assert list(_sentences(["Không nhận được âm thanh, vui lòng nói lại."])) == ["Không nhận được âm thanh, vui lòng nói lại."]


def test_split_long_never_cuts_inside_a_word_or_after_a_function_word():
    text = " ".join(["từ%d" % i for i in range(45)]) + " của nhà"  # 'của' rơi gần chỗ cắt 200
    parts = list(_sentences([text]))
    assert " ".join(parts).split() == text.split()
    assert all(len(p) <= 200 for p in parts)
    assert all(not p.endswith((" của", " và", " với")) for p in parts[:-1])


def test_split_long_prefers_a_comma():
    text = ("Đây là một câu rất dài " * 4).strip() + ", " + ("nó tiếp tục mãi không có dấu chấm " * 4)
    first = list(_sentences([text]))[0]
    assert first.endswith(","), first


def test_sync_iter_marks_model_newlines_as_soft():
    """Newline của model đổi thành SOFT_BREAK; '\\n' thật chỉ do handler đặt nên không đi qua SyncIter."""
    import asyncio

    async def agen():
        yield "Đây là nắp của một hộp thiếc đựng\n"
        yield "thực phẩm."

    async def run():
        loop = asyncio.get_running_loop()
        it = speech.sync_iter(agen(), loop)
        return await loop.run_in_executor(None, lambda: list(it))

    chunks = asyncio.run(run())
    assert chunks == ["Đây là nắp của một hộp thiếc đựng" + SB, "thực phẩm."]


# ── loại chỗ nối ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("prev,nxt,kind", [
    ("Xong rồi.", "Tiếp theo", "end"),
    ("Bạn có đi không?", "tôi đi", "end"),
    ("Tôi không thấy rõ chai nước suối,", "vui lòng chụp lại", "clause"),
    ("Tầng 4:", "- Giám đốc", "clause"),
    ("Màn hình đang hiển thị thông", "báo tắt máy", "soft"),
    ("BẢN TIN CÔNG ĐOÀN", "THÔNG TIN", "line"),
    ("Phòng họp", "- Phòng khách VIP", "line"),
])
def test_gap_kind(prev, nxt, kind):
    assert _gap_kind(prev, nxt) == kind


# ── ghép PCM ──────────────────────────────────────────────────────────────────

def _tone(seconds, amp=8000, lead=0.0, trail=0.0, freq=220):
    n = int(seconds * 16000)
    body = (amp * np.sin(2 * np.pi * freq * np.arange(n) / 16000)).astype("<i2")
    return (np.zeros(int(lead * 16000), "<i2").tobytes() + body.tobytes() + np.zeros(int(trail * 16000), "<i2").tobytes())


def _silent_run_lengths(pcm: bytes, thr=200):
    x = np.abs(np.frombuffer(pcm, "<i2").astype(np.int32)) <= thr
    runs, n = [], 0
    for v in x:
        if v:
            n += 1
        elif n:
            runs.append(n)
            n = 0
    if n:
        runs.append(n)
    return runs


def _speak(monkeypatch, sentences_pcm: dict, text_chunks):
    monkeypatch.setattr(tts, "synthesize_stream", lambda s: iter([sentences_pcm[s][i:i + 4000] for i in range(0, len(sentences_pcm[s]), 4000)]))
    return b"".join(tts.synthesize_text_stream(text_chunks))


A = "Cửa ở phía trước hướng mười hai giờ."
B = "Giữa đường có một cái ghế lệch trái."


@pytest.mark.smooth_joins
def test_soft_join_is_nearly_seamless_and_end_join_keeps_the_pause(monkeypatch):
    head, tail = "Màn hình máy tính đang hiển thị thông", "báo tắt máy trước mặt bạn nhé."
    pcm = {head: _tone(1.0, lead=0.1, trail=0.15), tail: _tone(1.0, lead=0.1, trail=0.15),
           A: _tone(1.0, lead=0.1, trail=0.15), B: _tone(1.0, lead=0.1, trail=0.15)}
    soft = _speak(monkeypatch, pcm, [head + SB, tail])
    hard = _speak(monkeypatch, pcm, [A + " ", B])
    # chỗ nối mềm: tối đa vài chục ms (cắt lặng 0,1 + 0,15 s của model), chỗ nối câu: >= quãng nghỉ cấu hình
    soft_gap = max(_silent_run_lengths(soft)) / 16000
    hard_gap = max(_silent_run_lengths(hard)) / 16000
    assert soft_gap < 0.12, soft_gap
    assert hard_gap >= config.SPEECH_SENTENCE_PAUSE_SECONDS, hard_gap


@pytest.mark.smooth_joins
def test_clause_gap_is_between_soft_and_end(monkeypatch):
    c1, c2 = "Tôi không thấy rõ chai nước suối,", "vui lòng chụp lại gần hơn hoặc tìm chỗ khác."
    pcm = {c1: _tone(1.0, trail=0.15), c2: _tone(1.0, lead=0.1)}
    out = _speak(monkeypatch, pcm, [c1 + SB, c2])
    gap = max(_silent_run_lengths(out)) / 16000
    assert 0.1 < gap < config.SPEECH_SENTENCE_PAUSE_SECONDS, gap


@pytest.mark.smooth_joins
def test_no_click_at_joins(monkeypatch):
    """Câu bắt đầu ở biên độ lớn (đo thật: 3675/32768) nối sau lặng là một cái tách; vuốt 6 ms hai đầu."""
    loud_start = (np.full(800, 12000, "<i2")).tobytes()
    pcm = {A: loud_start, B: loud_start}
    out = np.frombuffer(_speak(monkeypatch, pcm, [A + " ", B]), "<i2").astype(np.int32)
    assert abs(out[0]) < 1000, out[0]  # vào từ 0
    nz = np.flatnonzero(np.abs(out) > 200)
    # mọi bước nhảy giữa hai mẫu liền nhau quanh chỗ nối đều nhỏ (đường vuốt, không phải bậc thang 12000)
    assert np.abs(np.diff(out)).max() < 800


@pytest.mark.smooth_joins
def test_first_sentence_leading_silence_is_trimmed(monkeypatch):
    """Lặng đầu 0,12 s của model bị cắt còn ~30 ms: tiếng đầu tới sớm hơn."""
    pcm = {A: _tone(1.0, lead=0.12)}
    out = _speak(monkeypatch, pcm, [A])
    lead = np.flatnonzero(np.abs(np.frombuffer(out, "<i2").astype(np.int32)) > 200)[0] / 16000
    assert lead < 0.06, lead


@pytest.mark.smooth_joins
def test_no_audio_is_lost_by_smoothing(monkeypatch):
    pcm = {A: _tone(1.0, lead=0.05, trail=0.05), B: _tone(1.0, lead=0.05, trail=0.05)}
    out = _speak(monkeypatch, pcm, [A + " ", B])
    voiced = np.count_nonzero(np.abs(np.frombuffer(out, "<i2").astype(np.int32)) > 200)
    ref = sum(np.count_nonzero(np.abs(np.frombuffer(p, "<i2").astype(np.int32)) > 200) for p in pcm.values())
    assert voiced >= ref * 0.99


@pytest.mark.smooth_joins
def test_silent_sentence_does_not_break_the_stream(monkeypatch):
    pcm = {A: b"\x00\x00" * 800, B: _tone(0.5)}
    out = _speak(monkeypatch, pcm, [A + " ", B])
    assert len(out) > 0


@pytest.mark.smooth_joins
def test_streaming_does_not_wait_for_end_of_sentence_to_emit(monkeypatch):
    """Phần có tiếng ra ngay theo từng chunk, chỉ giữ ~6 ms cuối + lặng cuối câu."""
    import threading
    release = threading.Event()
    body = _tone(2.0)

    def synth(sentence):
        yield body[:16000]   # nửa đầu
        release.wait(2.0)    # model còn đang tính phần sau
        yield body[16000:]

    monkeypatch.setattr(tts, "synthesize_stream", synth)
    stream = tts.synthesize_text_stream([A])
    started, got = time.monotonic(), 0
    while got < 14000:       # phải ra được TRƯỚC khi model xong câu
        got += len(next(stream))
    assert time.monotonic() - started < 1.0
    release.set()
    list(stream)


def test_tail_of_a_midphrase_cut_is_read_at_its_own_period_not_glued_to_the_next_sentence():
    """'Đây là nắp ... đựng' (cắt ở 8 từ) + 'thực phẩm.' (10 ký tự): đuôi ngắn này KHÔNG bị giữ lại chờ câu sau —
    giữ là phần đuôi của cụm tới trễ cả câu, và chỗ nối liền không còn đệm quãng nghỉ để che."""
    pieces = ["Đây là nắp của một hộp thiếc đựng" + SB, "thực phẩm. ", "Trên nắp có in các dòng mã số và ngày tháng."]
    assert list(_sentences(pieces)) == [
        "Đây là nắp của một hộp thiếc đựng", "thực phẩm.", "Trên nắp có in các dòng mã số và ngày tháng."]
