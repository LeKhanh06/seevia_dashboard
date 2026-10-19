# ruff: noqa: ANN401, PLR0913, S107, ASYNC240, ASYNC110  # công cụ E2E, không phải code runtime
"""Điện thoại giả lập cho E2E: nhận webhook như FCM, báo kết quả như app Android.

Luồng: đăng ký thiết bị (POST /api/v1/device/register, push_token="sim") -> mở
HTTP server nhận webhook của WebhookDeliveryAdapter (DELIVERY_TRANSPORT=webhook,
DELIVERY_WEBHOOK_URL=http://127.0.0.1:<port>/push) -> sau độ trễ ngẫu nhiên có
seed cố định gửi POST /api/v1/device/report với ``result`` đúng
contracts/contract-matrix.md cho từng action. Mọi lệnh nhận được ghi vào
``phone_sim_log.jsonl`` để E2E kiểm "không có lệnh ngoài ý muốn".

Token thiết bị đọc từ biến môi trường DEVICE_API_TOKEN, hoặc từ file .env
(``--env-file``, mặc định SeeVia_system/.env); không bao giờ in ra log.

Chạy: python scripts/phone_sim.py --app-url http://127.0.0.1:18001 --port 18002 \
        --user-id e2e-user [--glasses-id glasses-e2e] [--scenario scenario.json]

Kịch bản --scenario (JSON, mọi khoá tuỳ chọn, mặc định như bên dưới)::

    {"contacts": {"mẹ": ["0901234567"], "Nam": ["0911111111", "0922222222"]},
     "delay_min": 0.3, "delay_max": 1.0, "seed": 7,
     "emergency_delay": 0.5, "fail_actions": {"music_play": "PLAYBACK_FAILED"}}
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import sys
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

DEFAULT_SCENARIO: dict[str, Any] = {
    "contacts": {"mẹ": ["0901234567"], "Nam": ["0911111111", "0922222222"]},
    "delay_min": 0.3,
    "delay_max": 1.0,
    "seed": 7,
    "emergency_delay": 0.5,
    "fail_actions": {},
}
_DEFAULT_ENV = Path(__file__).resolve().parents[3] / ".env"


def _norm(text: str) -> str:
    """Hạ chữ thường + bỏ dấu để 'Mẹ' khớp 'mẹ'."""
    nfd = unicodedata.normalize("NFD", text.strip().lower().replace("đ", "d"))
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _mask(number: str) -> str:
    """Che số điện thoại, chỉ giữ 3 số cuối (contract: phone_number masked)."""
    return "***" + number[-3:]


def _read_env_token(env_file: Path) -> str | None:
    """Lấy DEVICE_API_TOKEN từ môi trường, rồi từ file .env."""
    if value := os.environ.get("DEVICE_API_TOKEN"):
        return value
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("DEVICE_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    return None


class PhoneSim:
    """Trạng thái + logic trả lời lệnh của điện thoại giả."""

    def __init__(
        self,
        *,
        app_url: str,
        token: str,
        user_id: str,
        device_id: str,
        scenario: dict[str, Any],
        log_path: Path,
    ) -> None:
        """Khởi tạo; scenario đã gộp với mặc định."""
        self.app_url = app_url.rstrip("/")
        self.user_id = user_id
        self.device_id = device_id
        self.sc = scenario
        self.log_path = log_path
        self._rng = random.Random(scenario["seed"])  # noqa: S311 - độ trễ giả, không phải mật mã
        self._http = httpx.AsyncClient(
            base_url=self.app_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
        )
        self._tasks: set[asyncio.Task[None]] = set()
        self._quotes: dict[str, bool] = {}
        self._nav_id: str | None = None
        self._volume = 50
        self._playing = False
        self._seen: set[str] = set()
        self._counter = 0
        self._contacts = {_norm(k): v for k, v in scenario["contacts"].items()}

    def log(self, event: str, **fields: Any) -> None:
        """Ghi một dòng JSONL (lệnh nhận / báo cáo gửi đi)."""
        rec = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def register(self, push_token: str = "sim") -> None:
        """Đăng ký thiết bị (một lần, trước khi nhận lệnh)."""
        body = {
            "user_id": self.user_id,
            "device_id": self.device_id,
            "platform": "android",
            "push_token": push_token,
        }
        resp = await self._http.post("/api/v1/device/register", json=body)
        resp.raise_for_status()

    async def link_glasses(self, glasses_id: str) -> None:
        """Ghép kính với user (giống app điện thoại khi người dùng nhập device_id kính)."""
        resp = await self._http.post(
            "/api/v1/device/glasses/link", json={"user_id": self.user_id, "device_id": glasses_id}
        )
        resp.raise_for_status()

    def on_push(self, data: dict[str, str]) -> None:
        """Nhận một lệnh: ghi log rồi lên lịch báo cáo (trùng request_id thì bỏ qua)."""
        rid = data.get("request_id", "")
        dup = rid in self._seen
        self._seen.add(rid)
        self.log(
            "command",
            request_id=rid,
            action=data.get("action"),
            params=json.loads(data.get("params_json") or "{}"),
            duplicate=dup,
            glasses_device_id=data.get("device_id"),
        )
        if dup:
            return
        task = asyncio.create_task(self._respond(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _respond(self, data: dict[str, str]) -> None:
        action = data["action"]
        delay = self._rng.uniform(self.sc["delay_min"], self.sc["delay_max"])
        if action == "emergency_call":
            delay = self.sc["emergency_delay"]
        await asyncio.sleep(delay)
        params = json.loads(data.get("params_json") or "{}")
        ok, payload = self.execute(action, params)
        body: dict[str, Any] = {
            "user_id": data["user_id"],
            "device_id": self.device_id,
            "request_id": data["request_id"],
            "action": action,
            "execution_state": "succeeded" if ok else "failed",
            "result": payload if ok else None,
            "error": None if ok else payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        try:
            resp = await self._http.post("/api/v1/device/report", json=body)
            self.log(
                "report",
                request_id=data["request_id"],
                action=action,
                state=body["execution_state"],
                http=resp.status_code,
                code=None if ok else payload["code"],
            )
        except httpx.HTTPError as exc:
            self.log("report_error", request_id=data["request_id"], error=type(exc).__name__)

    def _err(self, code: str, message: str, **details: Any) -> tuple[bool, dict[str, Any]]:
        return False, {"code": code, "message": message, "details": details}

    def execute(self, action: str, p: dict[str, Any]) -> tuple[bool, dict[str, Any]]:  # noqa: C901, PLR0911, PLR0912
        """Trả (thành công?, result|error) đúng schema contract-matrix cho từng action."""
        self._counter += 1
        n = self._counter
        forced = self.sc["fail_actions"].get(action)
        if forced:
            return self._err(forced, "Lỗi do kịch bản")
        if action == "contact_call":
            name = str(p.get("name", ""))
            numbers = self._contacts.get(_norm(name), [])
            if not numbers:
                return self._err("CONTACT_NOT_FOUND", "Không tìm thấy liên hệ", name=name)
            if len(numbers) > 1:
                cands = [{"name": name, "phone_number": _mask(x)} for x in numbers]
                return self._err(
                    "MULTIPLE_CONTACTS_FOUND", "Nhiều liên hệ trùng tên", candidates=cands
                )
            return True, {
                "call_state": "calling",
                "contact_name": name,
                "phone_number": _mask(numbers[0]),
            }
        if action == "ride_quote":
            qid = f"quote-{n}"
            self._quotes[qid] = False
            exp = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
            return True, {
                "quote_id": qid,
                "product_type": "sim-car",
                "price_estimate": {"currency": "VND", "amount": 85000.0},
                "eta_minutes": 4,
                "expires_at": exp,
            }
        if action == "ride_confirm":
            qid = str(p.get("quote_id", ""))
            if qid not in self._quotes:
                return self._err("QUOTE_NOT_FOUND", "Không có báo giá", quote_id=qid)
            if self._quotes[qid]:
                return self._err("QUOTE_ALREADY_USED", "Báo giá đã dùng", quote_id=qid)
            self._quotes[qid] = True
            if p.get("confirm"):
                return True, {"quote_id": qid, "ride_id": f"ride-{n}", "ride_status": "requested"}
            return True, {"quote_id": qid, "ride_id": None, "ride_status": "cancelled"}
        if action == "music_play":
            self._playing = True
            if p.get("volume") is not None:
                self._volume = int(p["volume"])
            return True, {
                "track_id": f"track-{n}",
                "title": str(p.get("song", "")),
                "artist": "Sim Artist",
                "playback_state": "playing",
                "volume": self._volume,
            }
        if action == "music_stop":
            if not self._playing:
                return self._err("NO_ACTIVE_PLAYBACK", "Không có nhạc đang phát")
            self._playing = False
            return True, {"playback_state": "stopped"}
        if action == "music_volume":
            if p.get("level") is not None:
                self._volume = int(p["level"])
            else:
                step = 10 if p.get("direction") == "up" else -10
                self._volume = max(0, min(100, self._volume + step))
            return True, {"volume_state": "changed", "level": self._volume}
        if action == "navigation_start":
            self._nav_id = f"nav-{n}"
            dest = p.get("destination") or {}
            return True, {
                "navigation_id": self._nav_id,
                "navigation_state": "navigating",
                "travel_mode": "walking",
                "destination": {
                    "address": dest.get("address"),
                    "lat": dest.get("lat", 10.7769),
                    "lng": dest.get("lng", 106.7009),
                },
            }
        if action == "navigation_stop":
            nid = str(p.get("navigation_id", ""))
            if self._nav_id is None or nid != self._nav_id:
                return self._err("NAVIGATION_NOT_FOUND", "Không có chỉ đường", navigation_id=nid)
            self._nav_id = None
            return True, {"navigation_id": nid, "navigation_state": "stopped"}
        if action == "emergency_call":
            # Rút ngắn: chu kỳ 21 phút thật được thay bằng một lượt gọi được nghe máy.
            return True, {
                "emergency_state": "completed",
                "attempt": 1,
                "answered": True,
                "cycle": "stopped",
                "contact": _mask("0903333333"),
                "sms_sent": True,
            }
        if action == "location_get":
            return True, {
                "lat": 10.7769,
                "lng": 106.7009,
                "address": "Sim Street",
                "captured_at": datetime.now(UTC).isoformat(),
            }
        return True, {"handled": action}  # capabilities/call_answer/call_reject: ngoài phạm vi


def build_app(sim: PhoneSim) -> FastAPI:
    """App nhận webhook: POST /push (body = data FCM, header X-Push-Token)."""
    app = FastAPI(title="phone_sim")

    @app.post("/push")
    async def push(request: Request) -> JSONResponse:
        data = await request.json()
        if request.headers.get("X-Push-Token") != "sim":
            return JSONResponse({"error": "bad push token"}, status_code=403)
        sim.on_push(data)
        return JSONResponse({"ok": True})

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _main(args: argparse.Namespace) -> int:
    token = _read_env_token(Path(args.env_file))
    if not token:
        print("Thiếu DEVICE_API_TOKEN (env hoặc --env-file)", file=sys.stderr)
        return 2
    scenario = {**DEFAULT_SCENARIO}
    if args.scenario:
        scenario.update(json.loads(Path(args.scenario).read_text(encoding="utf-8")))
    log_path = Path(args.log)
    sim = PhoneSim(
        app_url=args.app_url,
        token=token,
        user_id=args.user_id,
        device_id=args.device_id,
        scenario=scenario,
        log_path=log_path,
    )
    # Đăng ký sau khi server webhook đã nghe, để lệnh đầu tiên không rơi vào khoảng trống.
    server = uvicorn.Server(
        uvicorn.Config(build_app(sim), host=args.host, port=args.port, log_level="warning")
    )
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    for attempt in range(20):
        try:
            await sim.register()
            if args.glasses_id:
                await sim.link_glasses(args.glasses_id)
            break
        except httpx.HTTPError as exc:
            if attempt == 19:  # noqa: PLR2004
                print(f"Đăng ký thất bại: {type(exc).__name__}", file=sys.stderr)
                server.should_exit = True
                return 1
            await asyncio.sleep(0.5)
    sim.log("ready", app_url=args.app_url, port=args.port, t=time.time())
    print(f"phone_sim sẵn sàng :{args.port} -> {args.app_url} (log {log_path})", flush=True)
    with contextlib.suppress(asyncio.CancelledError):
        await serve
    return 0


def main() -> None:
    """Điểm vào CLI."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--app-url", default="http://127.0.0.1:18001")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18002)
    ap.add_argument("--user-id", default="e2e-user")
    ap.add_argument("--device-id", default="phone-sim-1")
    ap.add_argument("--glasses-id", default=None, help="nếu đặt: ghép kính này với user-id")
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--log", default="phone_sim_log.jsonl")
    ap.add_argument("--env-file", default=str(_DEFAULT_ENV))
    sys.exit(asyncio.run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
