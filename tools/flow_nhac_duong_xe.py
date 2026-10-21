"""Hội thoại nhiều lượt nhạc / dẫn đường / đặt xe: phân loại Gemma THẬT + trích tham số THẬT, app server GIẢ.

Chạy từ gốc VH:  CUDA_VISIBLE_DEVICES= ~/.venvs/seevia/bin/python tools/flow_nhac_duong_xe.py [lọc-tên] [out.json]
Đếm: số câu hỏi lại (system hỏi), lệnh nào thật sự gửi đi điện thoại, câu người dùng nghe.
"""
import asyncio
import json
import sys
import threading
import time
import uuid

sys.path.insert(0, ".")

import config  # noqa: E402
from handlers import action_flow  # noqa: E402
from lanes import phone  # noqa: E402
from live import device_session  # noqa: E402
from pipeline import cues, device_ctx, final_router, intent_early, pending_action  # noqa: E402
from services.action_callbacks import action_callbacks  # noqa: E402

DEV = "flowtest-dev"
SENT: list[tuple[str, dict]] = []
HEARD: list[str] = []
_RESULTS: dict[str, dict] = {}


FAIL_ONCE: dict[str, str] = {}


def _result_for(endpoint: str, payload: dict) -> dict:
    op = endpoint.rsplit("/", 2)[-2] + "_" + endpoint.rsplit("/", 1)[-1]
    if op in FAIL_ONCE:
        return {"request_state": "failed", "error": {"code": FAIL_ONCE.pop(op)}}
    if op == "music_play":
        return {"request_state": "succeeded", "result": {"title": payload["song"], "artist": "Unknown"}}
    if op == "navigation_start":
        return {"request_state": "succeeded", "result": {"navigation_id": "nav-1",
                                                         "destination": payload["destination"]}}
    if op == "ride_quote":
        return {"request_state": "succeeded", "result": {"quote_id": "quote-1"}}
    if op == "ride_confirm":
        return {"request_state": "succeeded",
                "result": {"ride_status": "requested" if payload.get("confirm") else "cancelled"}}
    if op == "music_volume":
        return {"request_state": "succeeded", "result": {"level": payload.get("level", 60)}}
    return {"request_state": "succeeded", "result": {}}


def fake_call(endpoint, payload):
    rid = payload.get("request_id") or str(uuid.uuid4())
    SENT.append((endpoint.replace("api/v1/service/", ""), {k: v for k, v in payload.items() if k != "request_id"}))
    _RESULTS[rid] = _result_for(endpoint, payload)
    return {"data": {"request_id": rid}}


def fake_status(rid):
    return _RESULTS.get(rid, {"request_state": "processing"})


action_flow.call_service_api = fake_call
action_flow.get_request_status = fake_status
device_session.push_result = lambda device, text, turn_id=None: HEARD.append(text) or True
pending_action.readback_delay = lambda text: 0.05
config.ACTION_TTS_LEAD_S = 0.0


class _Prewarmer:
    class _Session:
        token = "tok"

    def session(self, mode, *a, **k):
        return self._Session()


def _text(reply) -> list[str]:
    out = [cues.fallback_text(c) or f"<cue {c}>" for c in (reply.cues or [])]
    for piece in reply.speech or []:
        if piece and piece.strip():
            out.append(piece.strip())
    return out


async def turn(text: str) -> tuple[list[str], object]:
    gen = pending_action.begin_turn(DEV)
    state = {**action_flow.device_state(), "alerting": False}
    early = intent_early.EarlyIntent(state)
    d = await early.decide(text)
    ctx = final_router.TurnContext(DEV, str(uuid.uuid4())[:6], _Prewarmer(), asyncio.get_running_loop())
    ctx.device_state, ctx.gen = state, gen
    reply = await final_router.route(ctx, d, text)
    said = await asyncio.to_thread(_text, reply)
    if reply.on_audio_start is not None:
        reply.on_audio_start(0.0)
        await asyncio.sleep(0.6)
    return said, d


def is_question(lines: list[str]) -> bool:
    return any("?" in line or "nói lại" in line for line in lines) or phone.has_pending(DEV)


async def scenario(name: str, first: str, answers: dict[str, str], setup=None) -> dict:
    action_flow.reset_state()
    for table in (phone._CONFIRM, phone._PENDING, phone._CHOICE, phone._OFFER, phone._BODY):
        table.clear()
    if setup:
        setup()
    SENT.clear()
    HEARD.clear()
    log, questions, said_text = [], 0, first
    for _ in range(4):
        said, d = await turn(said_text)
        await asyncio.sleep(0.4)
        heard = said + HEARD[:]
        HEARD.clear()
        log.append({"user": said_text, "intent": d.intent, "backend": d.backend,
                    "conf": round(d.confidence, 4), "known": d.confidence_known, "sys": heard})
        if not is_question(heard):
            break
        questions += 1
        reply = next((a for k, a in answers.items() if any(k.lower() in h.lower() for h in heard)), None)
        if reply is None:
            break
        said_text = reply
    return {"name": name, "questions": questions, "sent": list(SENT), "turns": log}


def _music_on():
    action_flow.mark_active("music_playing")


def _nav_on():
    action_flow.remember("navigation_id", "nav-1")
    action_flow.mark_active("navigating")


def _quote():
    action_flow.remember("quote_id", "quote-1")


YES = {"phải không": "Đúng rồi"}


def _nav_not_found():
    FAIL_ONCE["navigation_start"] = "INVALID_DESTINATION"


def _song_not_found():
    FAIL_ONCE["music_play"] = "SONG_NOT_FOUND"
SCENARIOS = [
    ("M1 mở bài", "Mở bài Lạc trôi", {**YES, "ca sĩ nào": "Sơn Tùng", "bài hát nào": "Lạc trôi"}),
    ("M2 bài + ca sĩ", "Phát bài Nơi này có anh của Sơn Tùng", {**YES, "ca sĩ nào": "Sơn Tùng"}),
    ("M3 nhạc chung", "Mở nhạc đi", {**YES, "bài hát nào": "Nhạc Trịnh", "ca sĩ nào": "cứ phát"}),
    ("M4 thể loại", "Cho tôi nghe nhạc trữ tình", {**YES, "ca sĩ nào": "cứ phát"}),
    ("M5 bài dài", "Bật bài hát Em của ngày hôm qua", {**YES, "ca sĩ nào": "Sơn Tùng"}),
    ("M6 tắt nhạc", "Tắt nhạc", YES, _music_on),
    ("M7 tăng", "Tăng âm lượng", {**YES, "tăng hay giảm": "tăng"}, _music_on),
    ("M8 nhỏ tiếng", "Nhỏ tiếng lại một chút", {**YES, "tăng hay giảm": "giảm"}, _music_on),
    ("M9 to quá", "Nhạc to quá", {**YES, "tăng hay giảm": "giảm"}, _music_on),
    ("M10 mức số", "Chỉnh âm lượng 30", {**YES, "tăng hay giảm": "giảm"}, _music_on),
    ("M11 dừng lại", "Dừng lại", YES, _music_on),
    ("M12 bài khác", "Đổi bài khác đi", {**YES, "bài hát nào": "Hãy trao cho anh", "ca sĩ nào": "cứ phát"}, _music_on),
    ("N1 chỉ đường", "Chỉ đường đến chợ Bến Thành", {**YES, "đi đâu": "chợ Bến Thành"}),
    ("N2 dẫn tôi", "Dẫn tôi tới Đại học Bách Khoa", {**YES, "đi đâu": "Đại học Bách Khoa"}),
    ("N3 tìm đường", "Tìm đường đi bệnh viện Chợ Rẫy", {**YES, "đi đâu": "bệnh viện Chợ Rẫy"}),
    ("N4 thiếu điểm", "Chỉ đường cho tôi", {**YES, "đi đâu": "Nhà thờ Đức Bà"}),
    ("N5 dừng", "Dừng chỉ đường", YES, _nav_on),
    ("N6 muốn đi", "Tôi muốn đi bộ tới công viên Tao Đàn", {**YES, "đi đâu": "công viên Tao Đàn"}),
    ("R1 đặt xe", "Đặt xe đi sân bay Tân Sơn Nhất", {**YES, "đi đâu": "sân bay Tân Sơn Nhất"}),
    ("R2 gọi grab", "Gọi grab về quận 7", {**YES, "đi đâu": "quận 7"}),
    ("R3 thiếu điểm", "Đặt xe cho tôi", {**YES, "đi đâu": "Landmark 81"}),
    ("R4 xác nhận", "Xác nhận đặt xe", YES, _quote),
    ("R5 huỷ", "Huỷ chuyến xe", YES, _quote),
    ("N7 không thấy rồi nói lại", "Chỉ đường tới chợ Bến Thàn", {**YES, "nói lại tên địa điểm": "Chợ Bến Thành"},
     _nav_not_found),
    ("N8 thôi khi hỏi", "Chỉ đường giúp tôi", {**YES, "đi đâu": "Thôi"}),
    ("M13 không thấy bài", "Mở bài Lặc trôi", {**YES, "nói lại tên bài": "Lạc trôi"}, _song_not_found),
    ("M14 bài tiếp", "Chuyển bài khác", YES, _music_on),
    ("R6 taxi", "Tôi cần một chiếc taxi tới bến xe Miền Đông", {**YES, "đi đâu": "bến xe Miền Đông"}),
]


async def main() -> None:
    device_ctx.current.set(DEV)
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    out = []
    for name, first, answers, *setup in SCENARIOS:
        if only and only not in name:
            continue
        res = await scenario(name, first, answers, setup[0] if setup else None)
        out.append(res)
        print(f"\n== {name}  hỏi lại={res['questions']}  gửi={res['sent']}")
        for t in res["turns"]:
            print(f"  U: {t['user']}  [{t['intent']} {t['backend']} {t['conf']} known={t['known']}]")
            for s in t["sys"]:
                print(f"     S: {s}")
    total = sum(r["questions"] for r in out)
    print(f"\nTỔNG hỏi lại: {total} / {len(out)} kịch bản")
    with open(sys.argv[2] if len(sys.argv) > 2 else "/dev/null", "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
