"""Sửa sau review cuối (2026-11-19): giá trị sự kiện phải nằm trong hợp đồng AB."""

import time

import pytest

from pipeline import usage_turn
from pipeline.reply import Reply
from pipeline.usage_tasks import tracker
from services import usage

AB_PLAYBACK = {"confirmed", "unconfirmed"}  # ab-thongke schemas/usage.py AttemptFinished.playback


@pytest.fixture
def events(monkeypatch):
    out: list[dict] = []
    real_emit = usage.emit

    def capture(etype, **fields):
        out.append({"type": etype, **{k: v for k, v in fields.items() if v is not None}})
        return real_emit(etype, **fields)

    monkeypatch.setattr(usage, "emit", capture)
    tracker.reset()
    usage_turn.reset_for_tests()
    yield out
    tracker.reset()


@pytest.mark.parametrize("cut", ["button", "played_aborted"])
def test_cut_readback_while_phone_runs_keeps_playback_in_contract(events, cut):
    attempt = usage_turn.begin("dev-cut", time.monotonic(), transport="ws")
    usage_turn.routed(attempt, Reply(intent="contact_call", lane="D", awaits_action=True))
    usage_turn.audio_sent(attempt, time.monotonic(), 1.0)
    if cut == "button":
        usage_turn.interrupted(attempt)
    else:
        usage_turn.played(attempt, time.monotonic(), {"aborted": True})
    usage_turn.phone_result(attempt, {"request_state": "succeeded"})
    (fin,) = [e for e in events if e["type"] == "attempt_finished"]
    assert fin["status"] == "success"
    assert fin.get("playback", "confirmed") in AB_PLAYBACK


def test_turn_firmware_is_validated_like_hello(events):
    attempt = usage_turn.begin("dev-fw", time.monotonic(), transport="ws", glasses={"fw": "bad fw!"})
    usage_turn.routed(attempt, Reply(intent="chat", lane="A"))
    (start,) = [e for e in events if e["type"] == "attempt_started"]
    assert "firmware_version" not in start
    attempt = usage_turn.begin("dev-fw", time.monotonic(), transport="ws", glasses={"fw": "1.4.2"})
    usage_turn.routed(attempt, Reply(intent="chat", lane="A"))
    assert [e for e in events if e["type"] == "attempt_started"][-1]["firmware_version"] == "1.4.2"

