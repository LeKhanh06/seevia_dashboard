"""STT hai nhánh: căn chỉnh, ứng viên, bỏ phiếu độ tin cậy, Gemma chấm + lùi, và nối vào StreamingTranscriber."""

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

import config
from pipeline import stt_dual as sd
from pipeline.stt_stream import StreamingTranscriber


@pytest.fixture(autouse=True)
def _plain_policy(monkeypatch):
    """Các test chọn/lùi kiểm cơ chế gốc; chính sách bảo vệ lệnh dừng có test riêng bên dưới."""
    monkeypatch.setattr(config, "STT_ADJUDICATE_KEEP_HEAD", False)
    monkeypatch.setattr(config, "STT_ADJUDICATE_KEEP_STOP", False)


def hyp(text, logps=None):
    ws = text.split()
    lps = logps or [0.0] * len(ws)
    return sd.Hyp(text, [sd.Word(w, lp) for w, lp in zip(ws, lps)], 1.0)


def texts(segs):
    return [(" ".join(w.text for w in s.a), " ".join(w.text for w in s.b), s.same) for s in segs]


# ── căn chỉnh ───────────────────────────────────────────────────────────────

def test_align_identical_has_no_conflict():
    segs = sd.align(hyp("TẮT CẢNH BÁO").words, hyp("tắt cảnh báo").words)  # khác hoa/thường vẫn trùng
    assert sd.conflicts(segs) == 0
    assert texts(segs) == [("TẮT CẢNH BÁO", "tắt cảnh báo", True)]


def test_align_substitution_in_middle():
    segs = sd.align(hyp("PHÁT NHẠC BÙI LÊ RÙ").words, hyp("PHÁT NHẠC BOLERO").words)
    t = texts(segs)
    assert t[0][2] and t[0][0] == "PHÁT NHẠC"
    assert t[1] == ("BÙI LÊ RÙ", "BOLERO", False)
    assert sd.conflicts(segs) == 1


def test_align_dropped_first_word_is_one_conflict_with_empty_side():
    segs = sd.align(hyp("CẢNH BÁO").words, hyp("TẮT CẢNH BÁO").words)
    assert texts(segs) == [("", "TẮT", False), ("CẢNH BÁO", "CẢNH BÁO", True)]


def test_align_two_separate_conflicts():
    segs = sd.align(hyp("DỪNG DẪN ĐƯỜNG VỀ NHÀ").words, hyp("TIN GẮN ĐƯỜNG VỀ NHA").words)
    assert sd.conflicts(segs) == 2


def test_align_empty_inputs():
    assert sd.conflicts(sd.align([], hyp("A B").words)) == 1
    assert sd.align([], []) == []


# ── ứng viên ────────────────────────────────────────────────────────────────

def test_candidates_use_only_words_from_the_two_branches():
    a, b = hyp("DỪNG DẪN ĐƯỜNG VỀ NHÀ"), hyp("TIN GẮN ĐƯỜNG VỀ NHA")
    cands = sd.build_candidates(sd.align(a.words, b.words))
    texts_ = [t for t, _ in cands]
    assert texts_[0] == "DỪNG DẪN ĐƯỜNG VỀ NHÀ"           # nhánh 1 đứng đầu
    assert texts_[1] == "TIN GẮN ĐƯỜNG VỀ NHA"            # nhánh 2 thứ hai
    assert set(texts_) == {"DỪNG DẪN ĐƯỜNG VỀ NHÀ", "TIN GẮN ĐƯỜNG VỀ NHA", "DỪNG DẪN ĐƯỜNG VỀ NHA",
                           "TIN GẮN ĐƯỜNG VỀ NHÀ"}
    pool = {w.key for w in a.words + b.words}
    assert all(w.lower() in pool for t in texts_ for w in t.split())


def test_candidates_cap_when_too_many_conflicts():
    a, b = hyp("A1 X A2 X A3 X A4"), hyp("B1 X B2 X B3 X B4")
    cands = sd.build_candidates(sd.align(a.words, b.words), max_conflicts=3)
    assert [t for t, _ in cands] == ["A1 X A2 X A3 X A4", "B1 X B2 X B3 X B4"]


# ── bỏ phiếu độ tin cậy ─────────────────────────────────────────────────────

def test_vote_conf_takes_more_confident_side_per_segment():
    a = hyp("TIN GẮN ĐƯỜNG", [-2.0, -3.0, 0.0])
    b = hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0])
    assert sd.vote_conf(sd.align(a.words, b.words)) == "DỪNG DẪN ĐƯỜNG"
    assert sd.vote_conf(sd.align(b.words, a.words)) == "DỪNG DẪN ĐƯỜNG"


def test_vote_conf_empty_side_loses_to_confident_word_and_wins_over_shaky_one():
    short, long_ = hyp("CẢNH BÁO"), hyp("TẮT CẢNH BÁO", [-0.05, 0, 0])
    assert sd.vote_conf(sd.align(short.words, long_.words)) == "TẮT CẢNH BÁO"
    shaky = hyp("TẮT CẢNH BÁO", [-3.0, 0, 0])
    assert sd.vote_conf(sd.align(short.words, shaky.words)) == "CẢNH BÁO"


def test_rich_result_groups_bpe_tokens_into_words():
    res = SimpleNamespace(text="NHẮN CHO MẸ", tokens=[" NHẮ", "N", " CHO", " MẸ"], ys_log_probs=[-0.1, -2.0, -0.3, -0.4])
    h = sd.rich_result(res, 5.0)
    assert [w.text for w in h.words] == ["NHẮN", "CHO", "MẸ"]
    assert h.words[0].logp == pytest.approx(-2.0)  # tệ nhất trong các token của từ


def test_rich_result_without_probs_still_yields_words():
    h = sd.rich_result(SimpleNamespace(text="A B", tokens=[], ys_log_probs=[]))
    assert [w.text for w in h.words] == ["A", "B"] and h.words[0].logp == 0.0


# ── quyết định ──────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)


class Pick:
    """Giả `llm_probs`: `favor` = ứng viên được ưu tiên (xác suất `prob`); `bias_first` = độ thiên vị vị trí."""

    def __init__(self, favor=None, prob=0.9, exc=None, delay=0.0, bias_first=0.0):
        self.favor, self.prob, self.exc, self.delay, self.bias_first = favor, prob, exc, delay, bias_first
        self.calls = []

    async def __call__(self, cands, device):
        self.calls.append(list(cands))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        n = len(cands)
        raw = [(self.prob if c == self.favor else (1 - self.prob) / max(n - 1, 1)) for c in cands]
        raw[0] += self.bias_first
        t = sum(raw)
        return [x / t for x in raw], 5.0


def test_same_sentence_never_calls_llm():
    pick = Pick()
    d = run(sd.decide(hyp("TẮT CẢNH BÁO"), hyp("tắt cảnh báo"), None, "llm", probs=pick))
    assert d.source == "same" and d.text == "TẮT CẢNH BÁO" and not pick.calls


def test_conflict_calls_llm_and_uses_its_choice():
    pick = Pick(favor="DỪNG DẪN ĐƯỜNG", prob=0.95)
    d = run(sd.decide(hyp("TIN GẮN ĐƯỜNG"), hyp("DỪNG DẪN ĐƯỜNG"), {"navigating": True}, "llm", probs=pick))
    assert d.source == "llm" and d.text == "DỪNG DẪN ĐƯỜNG" and d.conflicts == 1 and d.prob == pytest.approx(0.95)
    # hai thứ tự (xuôi + đảo) để khử thiên vị vị trí
    assert sorted(pick.calls) == sorted([["TIN GẮN ĐƯỜNG", "DỪNG DẪN ĐƯỜNG"], ["DỪNG DẪN ĐƯỜNG", "TIN GẮN ĐƯỜNG"]])


def test_position_bias_is_cancelled_by_two_orders(monkeypatch):
    """Gemma nghiêng mạnh về ứng viên đứng đầu: một thứ tự chọn sai, hai thứ tự thì đúng."""
    pick = Pick(favor="DỪNG DẪN ĐƯỜNG", prob=0.6, bias_first=0.5)
    monkeypatch.setattr(config, "STT_ADJUDICATE_DEBIAS", False)
    assert run(sd.decide(hyp("TIN GẮN ĐƯỜNG"), hyp("DỪNG DẪN ĐƯỜNG"), None, "llm", probs=pick)).text == "TIN GẮN ĐƯỜNG"
    monkeypatch.setattr(config, "STT_ADJUDICATE_DEBIAS", True)
    assert run(sd.decide(hyp("TIN GẮN ĐƯỜNG"), hyp("DỪNG DẪN ĐƯỜNG"), None, "llm", probs=pick)).text == "DỪNG DẪN ĐƯỜNG"


def test_llm_timeout_falls_back_to_confidence_vote(monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE_TIMEOUT_S", 0.05)
    a = hyp("TIN GẮN ĐƯỜNG", [-2.0, -3.0, 0.0])
    b = hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0])
    d = run(sd.decide(a, b, None, "llm", probs=Pick(favor="DỪNG DẪN ĐƯỜNG", delay=2.0)))
    assert d.source.startswith("fallback_") and d.text == "DỪNG DẪN ĐƯỜNG"


def test_llm_error_falls_back_and_never_raises():
    d = run(sd.decide(hyp("A B"), hyp("A C"), None, "llm", probs=Pick(exc=RuntimeError("429"))))
    assert d.source == "fallback_RuntimeError" and d.text in ("A B", "A C")


def test_llm_malformed_probs_fall_back():
    async def bad(cands, device):
        return [0.5], 1.0  # thiếu phần tử

    d = run(sd.decide(hyp("A B"), hyp("A C"), None, "llm", probs=bad))
    assert d.source.startswith("fallback_")


def test_low_probability_choice_falls_back(monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE_MIN_PROB", 0.8)
    a = hyp("TIN GẮN ĐƯỜNG", [-2.0, -3.0, 0.0])
    b = hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0])
    d = run(sd.decide(a, b, None, "llm", probs=Pick(favor="TIN GẮN ĐƯỜNG", prob=0.55)))
    assert d.source == "fallback_lowprob" and d.text == "DỪNG DẪN ĐƯỜNG"


def test_mode_conf_and_none_do_not_call_llm():
    pick = Pick()
    a, b = hyp("TIN GẮN ĐƯỜNG", [-2.0, -3.0, 0.0]), hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0])
    assert run(sd.decide(a, b, None, "conf", probs=pick)).text == "DỪNG DẪN ĐƯỜNG"
    assert run(sd.decide(a, b, None, "none", probs=pick)).text == "TIN GẮN ĐƯỜNG"
    assert not pick.calls


def test_scope_command_skips_llm_when_conflict_misses_command_words(monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE_SCOPE", "command")
    pick = Pick()
    d = run(sd.decide(hyp("HÔM NAY TRỜI ĐẸP"), hyp("HÔM NAY TRỜI ĐẸM"), None, "llm", probs=pick))
    assert d.source == "conf" and not pick.calls
    d = run(sd.decide(hyp("TIN GẮN ĐƯỜNG"), hyp("DỪNG DẪN ĐƯỜNG"), None, "llm", probs=pick))
    assert d.source == "llm" and pick.calls


def test_empty_branch_uses_the_other():
    assert run(sd.decide(hyp(""), hyp("MỞ NHẠC"), None, "llm", probs=Pick())).text == "MỞ NHẠC"
    assert run(sd.decide(hyp("MỞ NHẠC"), sd.Hyp(), None, "llm", probs=Pick())).text == "MỞ NHẠC"
    assert run(sd.decide(hyp("MỞ NHẠC"), None, None, "llm", probs=Pick())).source == "b1"


def test_prompt_lists_candidates_and_device_context():
    p = sd._prompt(["DỪNG DẪN ĐƯỜNG", "TIN GẮN ĐƯỜNG"], {"navigating": True})
    assert "1. dừng dẫn đường" in p and "2. tin gắn đường" in p and "đang chỉ đường=có" in p


# ── nối vào StreamingTranscriber ────────────────────────────────────────────

@pytest.fixture
def fake_branches(monkeypatch):
    """Nhánh 1/2 giả: đếm số lần giải; nhánh 2 chạy qua Future như bản thật (`submit_branch2`)."""
    from concurrent.futures import Future

    calls = {"b1": 0, "b2": 0}

    def b1(samples, hw=None):
        calls["b1"] += 1
        return hyp("TIN GẮN ĐƯỜNG", [-2.0, -3.0, 0.0])

    def b2(samples, hw=None):
        calls["b2"] += 1
        return hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0])

    def submit(samples, hw=None):
        f = Future()
        f.set_result(b2(samples, hw))
        return f

    monkeypatch.setattr(sd, "decode_branch1", b1)
    monkeypatch.setattr(sd, "decode_branch2", b2)
    monkeypatch.setattr(sd, "submit_branch2", submit)
    return calls


def _feed(tr, seconds=0.5):
    tr._chunks.append(np.zeros(int(16000 * seconds), dtype=np.float32))
    tr._n_samples += int(16000 * seconds)


def test_transcriber_dual_final_merges_and_partial_stays_branch1(fake_branches, monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE", "conf")
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    assert tr.partial() == "Tin gắn đường"          # đoán sớm vẫn dùng nhánh 1
    assert fake_branches == {"b1": 1, "b2": 1}       # nhưng nhánh 2 đã chạy ngay trong partial
    assert run(tr.final_async()) == "Dừng dẫn đường"  # bỏ phiếu độ tin cậy
    assert fake_branches == {"b1": 1, "b2": 1}       # bản ghi cuối không giải lại
    assert tr.dual_info()["source"] == "conf"


def test_transcriber_dual_without_partial_decodes_branch2_at_final(fake_branches, monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE", "conf")
    monkeypatch.setattr(config, "STT_DUAL_PARTIAL", False)
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    tr.partial()
    assert fake_branches == {"b1": 1, "b2": 0}
    assert run(tr.final_async()) == "Dừng dẫn đường"
    assert fake_branches["b2"] == 1 and fake_branches["b1"] == 1  # chỉ giải thêm nhánh 2


def test_transcriber_dual_redecodes_after_new_audio(fake_branches, monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE", "none")
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    tr.partial()
    _feed(tr)
    assert run(tr.final_async()) == "Tin gắn đường"  # none = nhánh 1
    assert fake_branches == {"b1": 2, "b2": 2}


def test_transcriber_dual_slow_branch2_gives_blank_vote_then_catches_up(monkeypatch, fake_branches):
    """Nhánh 2 chậm quá STT_DUAL_B2_WAIT_MS: bản ghi cuối không chờ (dùng nhánh 1); xong rồi thì lần sau dùng được."""
    from concurrent.futures import Future

    monkeypatch.setattr(config, "STT_ADJUDICATE", "conf")
    monkeypatch.setattr(config, "STT_DUAL_B2_WAIT_MS", 10)
    slow = Future()
    monkeypatch.setattr(sd, "submit_branch2", lambda samples, hw=None: slow)
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    assert tr.partial() == "Tin gắn đường"
    assert run(tr.final_async()) == "Tin gắn đường"   # nhánh 2 chưa xong: bỏ phiếu trắng
    assert tr.dual_info() is None and tr.b2_late >= 1
    slow.set_result(hyp("DỪNG DẪN ĐƯỜNG", [-0.1, -0.2, 0.0]))
    assert run(tr.final_async()) == "Dừng dẫn đường"   # cùng đoạn: lấy kết quả nhánh 2 đã xong
    assert fake_branches["b1"] == 1                    # không giải lại nhánh 1


def test_transcriber_dual_branch2_crash_degrades_to_branch1(monkeypatch, fake_branches):
    from concurrent.futures import Future

    bad = Future()
    bad.set_exception(RuntimeError("onnx hỏng"))
    monkeypatch.setattr(sd, "submit_branch2", lambda samples, hw=None: bad)
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    assert tr.partial() == "Tin gắn đường"
    assert run(tr.final_async()) == "Tin gắn đường"


def test_transcriber_dual_llm_timeout_keeps_turn_alive(fake_branches, monkeypatch):
    async def boom(*a, **k):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(config, "STT_ADJUDICATE", "llm")
    monkeypatch.setattr(sd, "llm_probs", boom)
    tr = StreamingTranscriber(dual=True)
    _feed(tr)
    assert run(tr.final_async()) == "Dừng dẫn đường"  # lùi về bỏ phiếu độ tin cậy
    assert tr.dual_info()["source"].startswith("fallback_")


def test_transcriber_single_branch_unchanged(monkeypatch):
    from pipeline import stt

    monkeypatch.setattr(stt, "decode_samples", lambda samples, hotwords=None: "ĐỌC GIÚP TÔI")
    monkeypatch.setattr(sd, "submit_branch2", lambda *a: pytest.fail("không được chạy nhánh 2"))
    tr = StreamingTranscriber(dual=False)
    _feed(tr)
    assert run(tr.final_async()) == "Đọc giúp tôi" and tr.dual_info() is None


def test_prefetch_adjudicates_on_partial_and_final_reuses_it(fake_branches, monkeypatch):
    calls = []

    async def probs(cands, device):
        calls.append(list(cands))
        return [0.1, 0.9][:len(cands)] if cands[0] == "TIN GẮN ĐƯỜNG" else [0.9, 0.1][:len(cands)], 3.0

    monkeypatch.setattr(config, "STT_ADJUDICATE", "llm")
    monkeypatch.setattr(sd, "llm_probs", probs)

    async def go():
        tr = StreamingTranscriber(dual=True)
        _feed(tr)
        assert await tr.partial_async() == "Tin gắn đường"
        await asyncio.sleep(0.05)               # task chấm trước chạy xong
        n = len(calls)
        assert n == 2                           # hai thứ tự, gọi ngay trên partial
        assert await tr.final_async() == "Dừng dẫn đường"
        assert len(calls) == n                  # bản ghi cuối dùng lại, không gọi thêm
        assert tr.dual_info()["prefetch"] is True

    run(go())


def test_prefetch_discarded_when_final_text_differs(fake_branches, monkeypatch):
    seq = iter([("TIN GẮN ĐƯỜNG", "DỪNG DẪN ĐƯỜNG"), ("TIN GẮN ĐƯỜNG NHÀ", "DỪNG DẪN ĐƯỜNG NHÀ")])
    cur = {}

    def nxt():
        cur["t"] = next(seq)

    def b1(samples, hw=None):
        return hyp(cur["t"][0])

    def submit(samples, hw=None):
        from concurrent.futures import Future

        nxt()  # nhánh 2 được khởi động trước nhánh 1 trong `_decode`
        f = Future()
        f.set_result(hyp(cur["t"][1]))
        return f

    async def probs(cands, device):
        return [0.2, 0.8][:len(cands)] if cands[0].startswith("TIN") else [0.8, 0.2][:len(cands)], 1.0

    monkeypatch.setattr(sd, "decode_branch1", b1)
    monkeypatch.setattr(sd, "submit_branch2", submit)
    monkeypatch.setattr(config, "STT_ADJUDICATE", "llm")
    monkeypatch.setattr(sd, "llm_probs", probs)

    async def go():
        tr = StreamingTranscriber(dual=True)
        _feed(tr)
        await tr.partial_async()
        await asyncio.sleep(0.05)
        _feed(tr)                                # tiếng mới -> giải lại, cặp chữ khác
        assert await tr.final_async() == "Dừng dẫn đường nhà"
        assert tr.dual_info()["prefetch"] is False

    run(go())


def test_protect_keeps_dropped_leading_stop_word_without_calling_llm(monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE_KEEP_HEAD", True)
    pick = Pick()
    d = run(sd.decide(hyp("CẢNH BÁO"), hyp("TẮT CẢNH BÁO"), None, "llm", probs=pick))
    assert d.text == "TẮT CẢNH BÁO" and d.source == "protect" and not pick.calls


def test_protect_keeps_the_side_that_has_the_stop_verb(monkeypatch):
    monkeypatch.setattr(config, "STT_ADJUDICATE_KEEP_STOP", True)
    pick = Pick()
    d = run(sd.decide(hyp("NHƯNG DẪN ĐƯỜNG"), hyp("DỪNG DẪN ĐƯỜNG"), None, "llm", probs=pick))
    assert d.text == "DỪNG DẪN ĐƯỜNG" and d.source == "protect" and not pick.calls
    # cả hai bên đều có (hoặc đều không có) động từ dừng -> vẫn để Gemma chấm
    pick = Pick(favor="TẮT NHẠC", prob=0.9)
    d = run(sd.decide(hyp("TẮT NHẠC"), hyp("DỪNG NHẠC"), None, "llm", probs=pick))
    assert d.source == "llm" and pick.calls
