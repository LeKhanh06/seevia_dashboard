"""pipeline/usage_tasks.py: ghép lượt -> tác vụ theo các kịch bản nghiệm thu của tài liệu nghiệp vụ."""

import pytest

from pipeline.usage_tasks import Tracker
from services import usage


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []

    def fake_emit(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return "evt"

    monkeypatch.setattr(usage, "emit", fake_emit)
    return out


def _of(events, etype):
    return [e for e in events if e["type"] == etype]


def test_retake_scenario(events):
    """Chụp lại: lượt 1 ảnh bị từ chối (giữ mở), lượt 2 sau 40 s thành công -> 1 tác vụ, 2 lượt."""
    t = Tracker()
    a1, task1 = t.start("g", "ocr", now=0.0)
    t.outcome(a1, "rejected_image", keeps_open=True, now=1.0)
    a2, task2 = t.start("g", "ocr", now=40.0)
    t.outcome(a2, "success", now=45.0)

    assert task1 == task2
    assert [e["status"] for e in _of(events, "attempt_finished")] == ["rejected_image", "success"]
    (done,) = _of(events, "task_finished")
    assert (done["task_id"], done["status"], done["attempts"]) == (task1, "success", 2)


@pytest.mark.parametrize(("gap", "merged"), [(60.0, True), (61.0, False)])
def test_merge_boundary_60_vs_61(events, gap, merged):
    t = Tracker()
    a1, task1 = t.start("g", "ocr", now=0.0)
    t.outcome(a1, "rejected_image", keeps_open=True, now=0.5)
    _, task2 = t.start("g", "ocr", now=gap)

    assert (task1 == task2) is merged
    if not merged:
        (abandoned,) = _of(events, "task_finished")
        assert (abandoned["task_id"], abandoned["status"]) == (task1, "abandoned")


def test_new_task_after_final(events):
    t = Tracker()
    a1, task1 = t.start("g", "ocr", now=0.0)
    t.outcome(a1, "success", now=2.0)
    _, task2 = t.start("g", "ocr", now=5.0)
    assert task1 != task2


def test_independent_contexts_two_tasks(events):
    """Hai yêu cầu độc lập cùng chủ, cùng chức năng, cách vài giây -> hai tác vụ."""
    t = Tracker()
    a1, task1 = t.start("g", "ocr", now=0.0)
    t.outcome(a1, "rejected_image", keeps_open=True, now=1.0)
    _, task2 = t.start("g", "ocr", now=3.0, independent=True)
    assert task1 != task2


def test_concurrent_attempt_opens_new_task(events):
    """Lượt trước còn đang chạy (chưa chốt) -> không ghép."""
    t = Tracker()
    _, task1 = t.start("g", "chat", now=0.0)
    _, task2 = t.start("g", "chat", now=2.0)
    assert task1 != task2


def test_waits_for_playback_and_action(events):
    """Cần cả điện thoại xác nhận và lời đáp phát xong; lỗi điện thoại giữ mã lỗi."""
    t = Tracker()
    a, _ = t.start("g", "call", now=0.0)
    t.require(a, "playback", "action", deadline=100.0)
    t.outcome(a, "success", now=1.0, request_id="req-1")
    assert _of(events, "attempt_finished") == []
    t.confirm(a, "playback", now=2.0)
    assert _of(events, "attempt_finished") == []  # còn chờ điện thoại
    assert t.by_request("req-1") == a
    t.confirm(a, "action", status="failed", error_code="CONTACT_NOT_FOUND", now=3.0)

    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["error_code"], fin["playback"], fin["request_id"]) == (
        "failed", "CONTACT_NOT_FOUND", "confirmed", "req-1")
    assert t.by_request("req-1") is None


def test_success_only_after_both_confirmations(events):
    t = Tracker()
    a, _ = t.start("g", "music", now=0.0)
    t.require(a, "action", "playback", deadline=100.0)
    t.outcome(a, "success", now=1.0)
    t.confirm(a, "action", status="success", now=2.0)
    t.confirm(a, "playback", now=3.0)
    (fin,) = _of(events, "attempt_finished")
    assert fin["status"] == "success"


def test_finish_twice_emits_once(events):
    t = Tracker()
    a, _ = t.start("g", "chat", now=0.0)
    t.outcome(a, "success", now=1.0)
    t.outcome(a, "failed", now=2.0)
    t.finish(a, "cancelled", now=3.0)
    assert len(_of(events, "attempt_finished")) == 1
    assert len(_of(events, "task_finished")) == 1


def test_abandon_after_120s(events):
    t = Tracker()
    a, task = t.start("g", "ocr", now=0.0)
    t.outcome(a, "rejected_image", keeps_open=True, now=1.0)
    t.expire(120.0)
    assert _of(events, "task_finished") == []
    t.expire(121.5)
    (done,) = _of(events, "task_finished")
    assert (done["task_id"], done["status"], done["attempts"]) == (task, "abandoned", 1)


def test_playback_deadline_unconfirmed(events):
    t = Tracker()
    a, _ = t.start("g", "chat", now=0.0)
    t.require(a, "playback", deadline=12.0)
    t.outcome(a, "success", now=2.0)
    t.expire(11.0)
    assert _of(events, "attempt_finished") == []
    t.expire(12.5)
    (fin,) = _of(events, "attempt_finished")
    assert (fin["status"], fin["playback"]) == ("unconfirmed", "unconfirmed")


def test_first_audio_marked_once(events):
    t = Tracker()
    a, _ = t.start("g", "chat", now=0.0)
    t.mark(a, first_audio_at="2026-11-16T02:00:01+00:00")
    t.mark(a, first_audio_at="2026-11-16T02:00:05+00:00")
    marks = _of(events, "attempt_marks")
    assert len(marks) == 1
    assert t.had_first_audio(a)
