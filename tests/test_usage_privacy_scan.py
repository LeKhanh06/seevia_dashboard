"""tools/usage_privacy_scan.py: tìm chuỗi riêng tư trong spool thống kê + dòng log của bộ thống kê."""

import json

from tools import usage_privacy_scan as scan


def _spool(tmp_path, events):
    d = tmp_path / "usage"
    d.mkdir()
    (d / "spool-20261119.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    return d


def test_clean_spool_and_log_pass(tmp_path):
    d = _spool(tmp_path, [{"type": "attempt_started", "device_id": "glasses-123", "feature": "call"}])
    log = tmp_path / "server.log"
    log.write_text("x INFO blind_assist: v2 turn nghe='gọi em iu' (log lượt, không phải thống kê)\n"
                   "x WARNING blind_assist.usage: usage: gửi lô lỗi mạng (ConnectError)\n")
    assert scan.scan(d, log, devices=["glasses-123"]) == []


def test_finds_private_values_outside_device_id(tmp_path):
    d = _spool(tmp_path, [
        {"type": "attempt_signal", "device_id": "glasses-123", "reason_code": "em iu"},
        {"type": "attempt_finished", "device_id": "x", "error_code": "0912345678"},
        {"type": "session_started", "device_id": "x", "kind": "glasses-123"},
        {"type": "attempt_signal", "device_id": "x", "signal": "money_uncertain", "reason_code": "50 nghìn đồng"},
    ])
    log = tmp_path / "server.log"
    log.write_text("x WARNING blind_assist.usage: usage: AB từ chối glasses-123 (CONFLICT)\n")
    hits = scan.scan(d, log, devices=["glasses-123"], patterns=["em iu"])
    kinds = sorted({h.split(":", 2)[1] for h in hits})
    assert kinds == ["device", "money", "pattern", "phone"]
    assert len(hits) == 5


def test_cli_exit_code(tmp_path, capsys):
    d = _spool(tmp_path, [{"type": "x", "device_id": "d", "error_code": "0987654321"}])
    log = tmp_path / "server.log"
    log.write_text("")
    assert scan.main([str(d), str(log)]) == 1
    assert "phone" in capsys.readouterr().out
