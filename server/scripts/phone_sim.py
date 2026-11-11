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
     "emergency_delay": 0.5, "fail_actions": {"music_play": "PLAYBACK_FAILED"},
     "emergency_contact": "0903333333", "incoming_call": false, "geocode_fails": false,
     "capabilities": {"send_sms": false}}

Mọi report khớp byte-by-byte cấu trúc app Android dựng (mobile/.../actions/*Actions.kt); chú thích
từng trường ghi nguồn Kotlin. Tên danh bạ chỉ so không phân biệt hoa/thường (app KHÔNG bỏ dấu).
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
import uuid
from datetime import UTC, datetime
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
    "emergency_contact": "0903333333",  # AppConfig.KEY_EMERGENCY_CONTACT: số hoặc tên danh bạ
    "incoming_call": False,  # True: call_answer/call_reject thành công (có cuộc gọi đang đổ chuông)
    "geocode_fails": False,
    "capabilities": {},  # ghi đè từng khoá của capabilities_get
}
STREAM_MAX = 15  # AudioManager.getStreamMaxVolume(STREAM_MUSIC) điển hình
QUOTE_VALID_MINUTES = 5  # RideActions.kt QUOTE_VALID_MINUTES
GEO_LAT, GEO_LNG = 10.7769, 106.7009  # kết quả geocode/location giả
_DEFAULT_ENV = Path(__file__).resolve().parents[3] / ".env"


def _digits(number: str) -> str:
    return "".join(c for c in number if c.isdigit())


def _mask(number: str) -> str:
    """CallActions.kt ContactLookup.maskPhoneNumber: "***" + 3 chữ số cuối (dưới 3 số: "***")."""
    digits = _digits(number)
    return "***" if len(digits) < 3 else "***" + digits[-3:]  # noqa: PLR2004


def _looks_like_number(value: str) -> bool:
    """CallActions.kt ContactLookup.looksLikePhoneNumber."""
    v = value.strip()
    return bool(v) and all(c.isdigit() or c in "+-() " for c in v)


def _ts(moment: datetime) -> str:
    """core/Timestamps.kt utc(): yyyy-MM-dd'T'HH:mm:ss'Z' (không phần thập phân)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_params(data: dict[str, str]) -> dict[str, Any]:
    """CommandDispatcher.kt PushCommand.fromData: params_json rỗng/thiếu -> "{}"."""
    try:
        parsed = json.loads(data.get("params_json") or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_env_token(env_file: Path) -> str | None:
    """Lấy DEVICE_API_TOKEN từ môi trường, rồi từ file .env."""
    if value := os.environ.get("DEVICE_API_TOKEN"):
        return value
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().removeprefix("export ").lstrip()
            if stripped.startswith("DEVICE_API_TOKEN="):
                return stripped.split("=", 1)[1].strip().strip("\"'") or None
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
        capture_dir: Path | None = None,
    ) -> None:
        """Khởi tạo; scenario đã gộp với mặc định."""
        self.app_url = app_url.rstrip("/")
        self.user_id = user_id
        self.device_id = device_id
        self.sc = scenario
        self.log_path = log_path
        self.capture_dir = capture_dir
        self._rng = random.Random(scenario["seed"])  # noqa: S311 - độ trễ giả, không phải mật mã
        self._http = httpx.AsyncClient(
            base_url=self.app_url, headers={"Authorization": f"Bearer {token}"}, timeout=10.0
        )
        self._tasks: set[asyncio.Task[None]] = set()
        self._quotes: dict[str, dict[str, Any]] = {}
        self._navs: dict[str, str] = {}
        self._stream = STREAM_MAX * 6 // 10  # chỉ số âm lượng STREAM_MUSIC của máy giả (0..15)
        self._playing = False
        self._seen: set[str] = set()
        self._counter = 0

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
            maps_url=data.get("maps_url"),
        )
        if dup:
            return
        if self.capture_dir is not None:  # payload thô để lưu làm fixture cho test phía app
            self.capture_dir.mkdir(parents=True, exist_ok=True)
            out = self.capture_dir / f"{data.get('action', 'unknown')}.json"
            out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        task = asyncio.create_task(self._respond(data))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _respond(self, data: dict[str, str]) -> None:
        rid = data.get("request_id", "")
        action = data.get("action", "")
        try:
            delay = self._rng.uniform(self.sc["delay_min"], self.sc["delay_max"])
            if action == "emergency_call":
                delay = self.sc["emergency_delay"]
            await asyncio.sleep(delay)
            ok, payload = self._safe_execute(action, _parse_params(data))
            # CommandModels.kt DeviceReportPayload.toJson: result/error chỉ có mặt khi khác null;
            # timestamp = Timestamps.utc(executedAt) (giây, hậu tố Z).
            body: dict[str, Any] = {
                "user_id": data.get("user_id", ""),
                # CommandDispatcher.kt: deviceId lấy từ chính payload push, không phải id máy.
                "device_id": data.get("device_id") or self.device_id,
                "request_id": rid,
                "action": action,
                "execution_state": "succeeded" if ok else "failed",
                "timestamp": _ts(datetime.now(UTC)),
            }
            body["result" if ok else "error"] = payload
            resp = await self._http.post("/api/v1/device/report", json=body)
            self.log(
                "report",
                request_id=rid,
                action=action,
                state=body["execution_state"],
                http=resp.status_code,
                code=None if ok else payload["code"],
            )
        except Exception as exc:  # noqa: BLE001 - mọi lỗi đều phải được ghi, không nuốt task
            self.log("report_error", request_id=rid, error=type(exc).__name__, detail=str(exc))

    def _safe_execute(self, action: str, params: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Chạy execute; ngoại lệ (tham số xấu) thành kết quả failed thay vì treo thao tác."""
        try:
            return self.execute(action, params)
        except Exception as exc:  # noqa: BLE001
            self.log("execute_error", action=action, error=type(exc).__name__, detail=str(exc))
            return self._err("INTERNAL_ERROR", f"phone_sim không xử lý được tham số: {exc}")

    def _err(self, code: str, message: str, **details: Any) -> tuple[bool, dict[str, Any]]:
        # ActionHandler.kt failure(code, message, details = emptyMap()) -> {code,message,details}
        return False, {"code": code, "message": message, "details": details}

    def _find_contacts(self, query: str) -> list[tuple[str, str]]:
        """Tìm liên hệ như CallActions.kt ContactLookup.findByName.

        So tên không phân biệt hoa/thường (KHÔNG bỏ dấu), trùng số
        (so theo chữ số) chỉ tính một lần; trả (tên lưu trong danh bạ, số).
        """
        wanted = query.strip().lower()
        if not wanted:
            return []
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for name, numbers in self.sc["contacts"].items():
            if name.strip().lower() != wanted:
                continue
            for number in numbers:
                if _digits(number) not in seen:
                    seen.add(_digits(number))
                    out.append((name, number))
        return out

    def _resolve_place(self, dest: Any) -> dict[str, Any] | None:
        """Như ActionSessions.kt ResolvedPlace.from: {address?, lat?, lng?}.

        Thiếu toạ độ thì geocode địa chỉ (sim: toạ độ cố định,
        hoặc lat/lng None nếu scenario geocode_fails).
        """
        if not isinstance(dest, dict):
            return None
        address = str(dest.get("address") or "").strip() or None
        if "lat" in dest and "lng" in dest:
            return {"address": address, "lat": float(dest["lat"]), "lng": float(dest["lng"])}
        if address is None:
            return None
        if self.sc.get("geocode_fails"):
            return {"address": address, "lat": None, "lng": None}
        return {"address": address, "lat": GEO_LAT, "lng": GEO_LNG}

    def _set_stream(self, level: int) -> None:
        """AudioManager.setStreamVolume(STREAM_MUSIC, level*max/100) (MusicActions.kt)."""
        self._stream = (level * STREAM_MAX) // 100

    def execute(self, action: str, p: dict[str, Any]) -> tuple[bool, dict[str, Any]]:  # noqa: C901, PLR0911, PLR0912, PLR0915
        """Trả (thành công?, result|error) đúng cái handler Kotlin tương ứng trả."""
        forced = self.sc["fail_actions"].get(action)
        if forced:
            return self._err(forced, "Lỗi do kịch bản")
        if action == "contact_call":  # CallActions.kt ContactCallHandler
            name = str(p.get("name", "")).strip()
            if not name:
                return self._err("CONTACT_NOT_FOUND", "Contact name must not be empty")
            matches = self._find_contacts(name)
            if not matches:
                return self._err("CONTACT_NOT_FOUND", f"No contact matched '{name}'")
            if len(matches) > 1:
                cands = [{"name": nm, "phone_number": _mask(num)} for nm, num in matches]
                return self._err(
                    "MULTIPLE_CONTACTS_FOUND", "Multiple contacts matched", candidates=cands
                )
            match_name, number = matches[0]
            return True, {
                "call_state": "calling",
                "contact_name": match_name,  # tên LƯU trong danh bạ, không phải tên người dùng nói
                "phone_number": _mask(number),
            }
        if action == "contact_sms":  # CallActions.kt ContactSmsHandler
            name = str(p.get("name", "")).strip()
            text = str(p.get("text", "")).strip()
            if not name:
                return self._err("CONTACT_NOT_FOUND", "Contact name must not be empty")
            if not text:
                return self._err("SMS_SEND_FAILED", "SMS text must not be empty")
            matches = self._find_contacts(name)
            if not matches:
                return self._err("CONTACT_NOT_FOUND", f"No contact matched '{name}'")
            if len(matches) > 1:
                cands = [{"name": nm, "phone_number": _mask(num)} for nm, num in matches]
                return self._err(
                    "MULTIPLE_CONTACTS_FOUND", "Multiple contacts matched", candidates=cands
                )
            return True, {"contact_name": matches[0][0], "sms_sent": True}
        if action == "ride_quote":  # RideActions.kt RideQuoteHandler
            place = self._resolve_place(p.get("destination"))
            if place is None or place["lat"] is None:
                return self._err("INVALID_DESTINATION", "Destination could not be located")
            qid = f"quote-{uuid.uuid4().hex[:8]}"
            expires = int(time.time()) + QUOTE_VALID_MINUTES * 60
            self._quotes[qid] = {"expires_at": expires, "used": False}
            return True, {
                "quote_id": qid,
                "product_type": "GrabCar",
                "price_estimate": {"currency": "VND", "amount": 85000},  # số nguyên như Kotlin
                "eta_minutes": 6,
                "expires_at": _ts(datetime.fromtimestamp(expires, UTC)),
            }
        if action == "ride_confirm":  # RideActions.kt RideConfirmHandler (thứ tự kiểm tra như app)
            qid = str(p.get("quote_id", "")).strip()
            quote = self._quotes.get(qid)
            if quote is None:
                return self._err("QUOTE_NOT_FOUND", f"No quote '{qid}' on this phone")
            if quote["used"]:
                return self._err("QUOTE_ALREADY_USED", f"Quote '{qid}' was already used")
            if time.time() > quote["expires_at"]:
                return self._err("QUOTE_EXPIRED", f"Quote '{qid}' has expired")
            quote["used"] = True
            if not p.get("confirm", False):
                return True, {"quote_id": qid, "ride_id": None, "ride_status": "cancelled"}
            return True, {
                "quote_id": qid,
                "ride_id": f"ride-{uuid.uuid4().hex[:8]}",
                "ride_status": "requested",
            }
        if action == "music_play":  # MusicActions.kt MusicPlayHandler (đường App Remote thành công)
            song = str(p.get("song", "")).strip()
            if not song:
                return self._err("SONG_NOT_FOUND", "music_play called without a song")
            volume = max(0, min(100, int(p["volume"]))) if "volume" in p else 60
            uri = str(p.get("spotify_uri") or "")
            track_uri = uri if uri.startswith("spotify:track:") else None
            title, sep, artist = song.partition(" - ")  # SongQuery.parse
            self._set_stream(volume)
            self._playing = True
            return True, {
                "track_id": track_uri,
                "title": title.strip() if sep else song,
                "artist": artist.strip() if sep else "Unknown",
                "playback_state": "playing",
                "provider": "Spotify",
                "volume": volume,
            }
        if action == "music_stop":  # nhạc đã tắt cũng là success (không bao giờ NO_ACTIVE_PLAYBACK)
            self._playing = False
            return True, {"playback_state": "stopped"}
        if action == "music_volume":  # MusicVolumeHandler: tuyệt đối 0..100 hoặc bước max/10
            if "level" in p:
                level = max(0, min(100, int(p["level"])))
            elif "direction" in p:
                step = max(STREAM_MAX // 10, 1)
                delta = step if p.get("direction") == "up" else -step
                level = (max(0, min(STREAM_MAX, self._stream + delta)) * 100) // STREAM_MAX
            else:
                return self._err("INVALID_VOLUME", "Either level or direction is required")
            self._set_stream(level)
            return True, {"volume_state": "changed", "level": level}
        if action == "navigation_start":  # NavigationActions.kt NavigationStartHandler
            place = self._resolve_place(p.get("destination"))
            if place is None:
                return self._err("INVALID_DESTINATION", "destination needs an address or lat/lng")
            nid = f"nav-{uuid.uuid4().hex[:8]}"
            self._navs[nid] = "navigating"
            return True, {
                "navigation_id": nid,
                "navigation_state": "navigating",
                "travel_mode": "walking",
                "destination": place,
            }
        if action == "navigation_stop":  # NavigationStopHandler
            nid = str(p.get("navigation_id", "")).strip()
            if nid not in self._navs:
                return self._err("NAVIGATION_NOT_FOUND", f"No navigation '{nid}' on this phone")
            if self._navs[nid] == "stopped":
                return self._err(
                    "NAVIGATION_ALREADY_STOPPED", f"Navigation '{nid}' is already stopped"
                )
            self._navs[nid] = "stopped"
            return True, {"navigation_id": nid, "navigation_state": "stopped"}
        if action == "emergency_call":  # CallActions.kt EmergencyCallHandler
            # Liên hệ khẩn cấp là cấu hình cục bộ trên máy (Safety screen), không nằm trong params.
            configured = str(self.sc.get("emergency_contact") or "").strip()
            if not configured:
                return self._err(
                    "EMERGENCY_CONTACT_NOT_CONFIGURED", "No emergency contact configured on device"
                )
            if _looks_like_number(configured):
                number = configured
            else:
                found = self._find_contacts(configured)
                if not found:
                    return self._err(
                        "EMERGENCY_CONTACT_NOT_CONFIGURED",
                        f"Configured emergency contact '{configured}' not found in device contacts",
                    )
                number = found[0][1]
            # Rút gọn chu kỳ 21 phút: một lượt gọi. App thật báo answered=False (không biết
            # có nghe máy hay không), nên sim cũng vậy.
            return True, {
                "emergency_state": "completed",
                "attempt": 1,
                "answered": False,
                "cycle": "stopped",
                "contact": _mask(number),
                "sms_sent": True,
            }
        if action == "location_get":  # StatusActions.kt LocationGetHandler
            return True, {
                "lat": GEO_LAT,
                "lng": GEO_LNG,
                "address": "Sim Street",
                "captured_at": _ts(datetime.now(UTC)),
            }
        if action == "capabilities_get":  # StatusActions.kt CapabilitiesGetHandler (thứ tự khoá)
            caps: dict[str, Any] = {
                "notification_listener": True,
                "system_alert_window": True,
                "battery_optimization_off": True,
                "background_location": "always",
                "fine_location": True,
                "read_contacts": True,
                "read_phone_state": True,
                "call_phone": True,
                "send_sms": True,
                "emergency_contact_set": bool(str(self.sc.get("emergency_contact") or "").strip()),
            }
            caps.update(self.sc.get("capabilities") or {})
            return True, {"capabilities": caps}
        if action in ("call_answer", "call_reject"):  # CallActions.kt ringingCall + handlers
            if not self.sc.get("incoming_call"):
                verb = "answer" if action == "call_answer" else "reject"
                return self._err("NO_INCOMING_CALL", f"No ringing call is available to {verb}")
            return True, {"call_state": "answered" if action == "call_answer" else "rejected"}
        # ActionHandler.kt ActionRegistry.getHandler
        return self._err(
            "UNSUPPORTED_ACTION", f"Action '{action}' is not supported by Android client"
        )


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
        capture_dir=Path(args.capture_dir) if args.capture_dir else None,
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
    ap.add_argument("--capture-dir", default=None, help="lưu payload webhook thô: <action>.json")
    ap.add_argument("--env-file", default=str(_DEFAULT_ENV))
    sys.exit(asyncio.run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
