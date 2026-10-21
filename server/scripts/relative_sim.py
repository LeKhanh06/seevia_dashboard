# ruff: noqa: ANN401, ASYNC240, ASYNC110, C901, E501, S105  # công cụ E2E, không phải code runtime
"""Người thân giả lập cho E2E gọi điện/nhắn tin với kính (thay app điện thoại của người thân).

Mỗi ``--user <số điện thoại>`` là MỘT tài khoản người thân: đăng ký + xác minh OTP (đọc mã từ log AB
``--ab-log``) hoặc đăng nhập nếu đã có, đăng ký thiết bị ``push_token="sim-<chữ số>"``, tự chấp nhận lời mời
``/api/v1/care``. Webhook FCM (``DELIVERY_TRANSPORT=webhook``, một URL cho cả AB) vào ``POST /push`` và được chia theo
``X-Push-Token``; token đúng ``sim`` (điện thoại chủ kính, ``phone_sim.py``) chuyển tiếp nguyên văn tới
``--forward-sim``.

Hành xử theo kịch bản (``--scenario`` JSON, mọi khoá tuỳ chọn; ``/ctl/behavior`` đổi giữa chừng)::

    {"names": {"0900000001": "Mẹ Hoa"},
     "defaults": {"incoming": "answer", "answer_delay_s": 1.0, "hangup_after_s": null, "media": true,
                  "play_wav": null, "click_first_s": 0.3, "click_period_s": 1.0},
     "users": {"0900000002": {"incoming": "ignore"}}}

``incoming``: ``answer`` (nghe sau ``answer_delay_s``), ``reject``, ``ignore`` (đổ chuông không ai nghe).

Cuộc gọi: nghe xong thì mở ``WS /v2/call?token=`` (``media_url`` + ``media_token`` AB trả), phát PCM16LE 16 kHz
mono khung 20 ms (640 B) đúng nhịp thời gian thực, và ghi tiếng nhận được vào ``--audio-dir`` (``rx_*.wav`` kèm
``rx_*.json`` giờ tới từng khung, để đo trễ một chiều). Nguồn phát mặc định = im lặng + tiếng "tách" 5 ms
(80 mẫu nhiễu giả ngẫu nhiên) mỗi ``click_period_s`` giây; dò tách ở đầu kia bằng tương quan chéo. Đồng hồ thu âm của
nguồn phát: mẫu ``n`` được "thu" lúc ``tx_t0 + n/16000`` và khung chứa nó gửi đi khi khung đầy.

Stack thật (AB ``DELIVERY_TRANSPORT=fcm`` + ``DELIVERY_WEBHOOK_URL``): token ``sim*`` đi webhook, FCM thật đi FCM.
``--media-url ws://127.0.0.1:18888/v2/call`` ghi đè ``media_url`` công khai của AB (sim cùng máy VH);
``--auto-reply "câu"`` (hoặc ``defaults.auto_reply``) tự nhắn lại chủ kính khi nhận ``message_in``;
mật khẩu tài khoản sim lấy từ ``RELSIM_PASSWORD`` (stack thật phải đặt mạnh). Xem ``scripts/live_calls_setup.py``.

Điều khiển (HTTP, chỉ localhost): ``POST /ctl/behavior`` ``{user?, ...}``, ``POST /ctl/call_glasses``
``{user, owner_user_id}``, ``POST /ctl/message`` ``{user, owner_user_id, text}``, ``POST /ctl/hangup`` ``{user}``, ``POST /ctl/get_call`` ``{user, call_id}``, ``POST /ctl/history`` ``{user, owner_user_id}``,
``GET /ctl/users``. Log JSONL (``--log``): mọi push nhận, HTTP gửi đi, mở/đóng media, kèm ``wall`` (time.time()).

Chạy: python scripts/relative_sim.py --app-url http://127.0.0.1:18001 --port 18003 --user 0900000001 \
        --user 0900000002 --ab-log ab.log --forward-sim http://127.0.0.1:18002/push --scenario s.json
Token Device API đọc từ DEVICE_API_TOKEN (môi trường hoặc ``--env-file``), không in ra log.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import time
import uuid
import wave
from array import array
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from websockets.asyncio.client import connect as ws_connect

SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320  # 20 ms
CLICK_SAMPLES = 80  # 5 ms
CLICK_AMPLITUDE = 12_000
DEFAULT_BEHAVIOR: dict[str, Any] = {
    "incoming": "answer",
    "answer_delay_s": 1.0,
    "hangup_after_s": None,
    "media": True,
    "play_wav": None,
    "click_first_s": 0.3,
    "click_period_s": 1.0,
    "auto_reply": None,  # chuỗi: tự nhắn lại chủ kính khi nhận message_in; None = không
    "auto_reply_delay_s": 1.5,
}
PASSWORD = os.environ.get("RELSIM_PASSWORD") or "relsim-pass-1"  # stack thật: đặt RELSIM_PASSWORD mạnh
_DEFAULT_ENV = Path(__file__).resolve().parents[3] / ".env"
_OTP_RE = re.compile(r"Demo OTP for (\S+): (\d+)")


def click_template() -> array[int]:
    """Tiếng "tách" 5 ms: nhiễu giả ngẫu nhiên xác định (LCG), cùng công thức ở ``e2e_calls.py``."""
    state = 20_261_020
    out: array[int] = array("h")
    for _ in range(CLICK_SAMPLES):
        state = (state * 1_103_515_245 + 12_345) & 0x7FFFFFFF
        out.append((state % (2 * CLICK_AMPLITUDE + 1)) - CLICK_AMPLITUDE)
    return out


CLICK = click_template()


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, UTC).isoformat()


class Signal:
    """Nguồn phát: WAV (nếu có) hoặc im lặng + tách định kỳ. ``frame(i)`` = 640 B PCM16LE."""

    def __init__(self, behavior: dict[str, Any]) -> None:
        """Dựng từ hành xử; WAV phải là PCM16 16 kHz mono."""
        self._first = int(float(behavior["click_first_s"]) * SAMPLE_RATE)
        self._period = int(float(behavior["click_period_s"]) * SAMPLE_RATE)
        self._wav: bytes | None = None
        if behavior.get("play_wav"):
            with wave.open(str(behavior["play_wav"]), "rb") as wav:
                if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (
                    SAMPLE_RATE,
                    1,
                    2,
                ):
                    msg = "play_wav phải là PCM16 16 kHz mono"
                    raise ValueError(msg)
                self._wav = wav.readframes(wav.getnframes())

    def click_starts(self, upto_samples: int) -> list[int]:
        """Vị trí mẫu bắt đầu mọi tách tới ``upto_samples`` (rỗng khi phát WAV)."""
        if self._wav is not None:
            return []
        return list(range(self._first, upto_samples, self._period))

    def frame(self, index: int) -> bytes:
        """Khung ``index`` (320 mẫu)."""
        if self._wav is not None:
            chunk = self._wav[index * FRAME_SAMPLES * 2 : (index + 1) * FRAME_SAMPLES * 2]
            return chunk.ljust(FRAME_SAMPLES * 2, b"\x00")
        start = index * FRAME_SAMPLES
        buf = array("h", bytes(FRAME_SAMPLES * 2))
        k = max(0, (start - self._first - CLICK_SAMPLES) // self._period)
        while True:
            c0 = self._first + k * self._period
            if c0 >= start + FRAME_SAMPLES:
                break
            for j in range(max(c0, start), min(c0 + CLICK_SAMPLES, start + FRAME_SAMPLES)):
                buf[j - start] = CLICK[j - c0]
            k += 1
        return buf.tobytes()


class Sim:
    """Trạng thái chung: người dùng, HTTP, log, media."""

    def __init__(
        self, args: argparse.Namespace, scenario: dict[str, Any], device_token: str
    ) -> None:
        """Khởi tạo; chưa gọi mạng."""
        self.app_url = args.app_url.rstrip("/")
        self.log_path = Path(args.log)
        self.audio_dir = Path(args.audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.ab_log = Path(args.ab_log) if args.ab_log else None
        self.forward = args.forward_sim
        self.media_url = args.media_url
        self.scenario = scenario
        self.device_token = device_token
        self.http = httpx.AsyncClient(base_url=self.app_url, timeout=10.0)
        self.users: dict[str, User] = {}
        self.by_push: dict[str, User] = {}
        self.tasks: set[asyncio.Task[None]] = set()

    def log(self, event: str, **fields: Any) -> None:
        """Ghi một dòng JSONL."""
        wall = time.time()
        rec = {"ts": _ts(wall), "wall": wall, "event": event, **fields}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def spawn(self, coro: Any) -> None:
        """Chạy nền, giữ tham chiếu và ghi lỗi (không nuốt)."""
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def done(t: asyncio.Task[None]) -> None:
            self.tasks.discard(t)
            if not t.cancelled() and (exc := t.exception()) is not None:
                self.log("task_error", error=type(exc).__name__, detail=str(exc))

        task.add_done_callback(done)

    async def setup_user(self, phone: str) -> User:
        """Đăng ký/đăng nhập, đăng ký thiết bị."""
        name = (self.scenario.get("names") or {}).get(phone) or f"Người thân {_digits(phone)[-4:]}"
        user = User(self, phone, name)
        await user.setup()
        self.users[phone] = user
        self.by_push[user.push_token] = user
        return user


class User:
    """Một tài khoản người thân + điện thoại giả."""

    def __init__(self, sim: Sim, phone: str, display_name: str) -> None:
        """Khởi tạo hành xử từ kịch bản."""
        self.sim = sim
        self.phone = phone
        self.display_name = display_name
        self.digits = _digits(phone)
        self.device_id = f"rel-{self.digits}"
        self.push_token = f"sim-{self.digits}"
        self.token = ""
        self.public_id = ""
        sc = sim.scenario
        self.behavior: dict[str, Any] = {
            **DEFAULT_BEHAVIOR,
            **(sc.get("defaults") or {}),
            **((sc.get("users") or {}).get(phone) or {}),
        }
        self.media: dict[str, Media] = {}
        self.last_call_id = ""
        self.owner_ids: list[str] = []  # chủ kính đã nhận lời mời (để tự trả lời tin)

    def auth(self) -> dict[str, str]:
        """Header Bearer của phiên app."""
        return {"Authorization": f"Bearer {self.token}"}

    async def _otp_from_log(self, offset: int) -> str:
        """Mã OTP trong log AB (AB không có kênh SMS: ghi vào log)."""
        if self.sim.ab_log is None:
            msg = "tài khoản mới cần --ab-log để đọc mã OTP"
            raise RuntimeError(msg)
        end = time.time() + 15
        while time.time() < end:
            if self.sim.ab_log.exists():
                text = self.sim.ab_log.read_bytes()[offset:].decode("utf-8", "replace")
                found = [
                    c for p, c in _OTP_RE.findall(text) if _digits(p).endswith(self.digits[-9:])
                ]
                if found:
                    return str(found[-1])
            await asyncio.sleep(0.2)
        msg = f"không thấy OTP của {self.phone} trong log AB"
        raise RuntimeError(msg)

    async def setup(self) -> None:
        """Register -> OTP -> session, hoặc login; rồi đăng ký thiết bị."""
        http = self.sim.http
        offset = (
            self.sim.ab_log.stat().st_size if self.sim.ab_log and self.sim.ab_log.exists() else 0
        )
        resp = await http.post(
            "/api/v1/auth/register",
            json={
                "phone_number": self.phone,
                "password": PASSWORD,
                "display_name": self.display_name,
            },
        )
        if resp.status_code == httpx.codes.CONFLICT:
            resp = await http.post(
                "/api/v1/auth/login", json={"phone_number": self.phone, "password": PASSWORD}
            )
        else:
            resp.raise_for_status()
            code = await self._otp_from_log(offset)
            resp = await http.post(
                "/api/v1/auth/otp/verify", json={"phone_number": self.phone, "otp_code": code}
            )
        resp.raise_for_status()
        data = resp.json()["data"]
        self.token, self.public_id = data["access_token"], data["public_user_id"]
        reg = await http.post(
            "/api/v1/device/register",
            headers={"Authorization": f"Bearer {self.sim.device_token}"},
            json={
                "user_id": self.public_id,
                "device_id": self.device_id,
                "platform": "android",
                "push_token": self.push_token,
            },
        )
        reg.raise_for_status()
        self.sim.log(
            "user_ready",
            user=self.phone,
            public_user_id=self.public_id,
            display_name=self.display_name,
        )

    async def accept_pending(self) -> None:
        """Chấp nhận mọi lời mời đang chờ (app thật có màn hình mời; sim tự nhận)."""
        resp = await self.sim.http.get("/api/v1/care/links", headers=self.auth())
        if resp.status_code != httpx.codes.OK:
            return
        for link in resp.json()["data"]["links"]:
            if (
                link["role"] == "relative"
                and link["status"] == "active"
                and link["owner_user_id"] not in self.owner_ids
            ):
                self.owner_ids.append(link["owner_user_id"])
            if link["role"] == "relative" and link["status"] == "pending":
                done = await self.sim.http.post(
                    f"/api/v1/care/{link['id']}/accept", headers=self.auth()
                )
                self.sim.log(
                    "care_accepted",
                    user=self.phone,
                    link_id=link["id"],
                    http=done.status_code,
                    owner_user_id=link["owner_user_id"],
                )

    # ---------- push ----------
    def on_push(self, data: dict[str, str]) -> None:
        """Một thông điệp FCM data."""
        kind = data.get("type", "")
        self.sim.log(
            "push",
            user=self.phone,
            type=kind,
            call_id=data.get("call_id"),
            message_id=data.get("message_id"),
            emergency=data.get("emergency"),
            reason=data.get("reason"),
            from_display_name=data.get("from_display_name"),
            text=data.get("text"),
        )
        if kind == "message_in":
            reply = self.behavior.get("auto_reply")
            if reply:
                self.sim.spawn(self._auto_reply(str(reply)))
        if kind == "call_incoming":
            self.last_call_id = data["call_id"]
            self.sim.spawn(self._incoming(data))
        elif kind == "call_answered":
            self.last_call_id = data["call_id"]
            if self.behavior["media"] and data.get("media_token"):
                self.sim.spawn(
                    self._open_media(data["call_id"], data["media_url"], data["media_token"])
                )
        elif kind == "call_ended":
            media = self.media.get(data["call_id"])
            if media is not None:
                media.stop("call_ended_push")

    async def _auto_reply(self, text: str) -> None:
        """Nhắn lại chủ kính một câu ngắn (kiểm tin hai chiều: kính phải đọc được tin này)."""
        await asyncio.sleep(float(self.behavior["auto_reply_delay_s"]))
        if not self.owner_ids:
            await self.accept_pending()  # lần đầu: tải danh sách link để biết chủ kính
        for owner in self.owner_ids:
            await self.send_message(owner, text, str(uuid.uuid4()))

    async def _incoming(self, data: dict[str, str]) -> None:
        """Cuộc gọi tới: nghe / từ chối / bỏ qua theo hành xử."""
        mode = self.behavior["incoming"]
        call_id = data["call_id"]
        if mode == "ignore":
            return
        await asyncio.sleep(float(self.behavior["answer_delay_s"]))
        http = self.sim.http
        if mode == "reject":
            resp = await http.post(f"/api/v1/calls/{call_id}/reject", headers=self.auth())
            self.sim.log("reject_http", user=self.phone, call_id=call_id, http=resp.status_code)
            return
        t_send = time.time()
        resp = await http.post(f"/api/v1/calls/{call_id}/answer", headers=self.auth())
        self.sim.log(
            "answer_http",
            user=self.phone,
            call_id=call_id,
            http=resp.status_code,
            t_send=t_send,
            body=resp.json() if resp.status_code != httpx.codes.OK else None,
        )
        if resp.status_code != httpx.codes.OK:
            return
        body = resp.json()["data"]
        if self.behavior["media"] and body.get("media_token"):
            await self._open_media(call_id, body["media_url"], body["media_token"])

    async def _open_media(self, call_id: str, url: str, token: str) -> None:
        media = Media(self, call_id, self.sim.media_url or url, token)
        self.media[call_id] = media
        await media.run()

    # ---------- hành động chủ động ----------
    async def call_glasses(self, owner_user_id: str) -> dict[str, Any]:
        """``POST /api/v1/calls``: gọi tới kính của chủ."""
        t_send = time.time()
        resp = await self.sim.http.post(
            "/api/v1/calls", headers=self.auth(), json={"owner_user_id": owner_user_id}
        )
        body = resp.json()
        call_id = body.get("data", {}).get("call_id", "")
        self.last_call_id = call_id
        self.sim.log(
            "call_create_http",
            user=self.phone,
            call_id=call_id,
            http=resp.status_code,
            t_send=t_send,
        )
        return {"http": resp.status_code, "call_id": call_id, "t_send": t_send}

    async def send_message(self, owner_user_id: str, text: str, request_id: str) -> dict[str, Any]:
        """``POST /api/v1/messages``: nhắn tin tới kính."""
        t_send = time.time()
        resp = await self.sim.http.post(
            "/api/v1/messages",
            headers=self.auth(),
            json={"owner_user_id": owner_user_id, "text": text, "request_id": request_id},
        )
        body = resp.json()
        self.sim.log(
            "message_sent_http",
            user=self.phone,
            http=resp.status_code,
            t_send=t_send,
            message_id=body.get("data", {}).get("message_id"),
        )
        return {"http": resp.status_code, "t_send": t_send, **(body.get("data") or {})}

    async def hangup(self) -> dict[str, Any]:
        """Cúp cuộc gọi hiện tại từ phía app: ``{"t":"hangup"}`` trên media WS + ``POST /calls/{id}/hangup``."""
        call_id = self.last_call_id
        media = self.media.get(call_id)
        if media is not None:
            await media.send_hangup()
        resp = await self.sim.http.post(f"/api/v1/calls/{call_id}/hangup", headers=self.auth())
        self.sim.log("hangup_http", user=self.phone, call_id=call_id, http=resp.status_code)
        return {"http": resp.status_code, "call_id": call_id}


class Media:
    """Một kết nối ``WS /v2/call``: phát nguồn tách đúng nhịp, ghi tiếng nhận."""

    def __init__(self, user: User, call_id: str, url: str, token: str) -> None:
        """Khởi tạo."""
        self.user, self.call_id = user, call_id
        self.uri = f"{url}{'&' if '?' in url else '?'}token={token}"
        self.sim = user.sim
        self.rx: list[tuple[float, bytes]] = []
        self._stop = asyncio.Event()
        self.ws: Any = None
        self.state_log: list[dict[str, Any]] = []

    def stop(self, why: str) -> None:
        """Dừng vòng phát/nhận."""
        self.sim.log("media_stop", user=self.user.phone, call_id=self.call_id, why=why)
        self._stop.set()

    async def send_hangup(self) -> None:
        """Gửi khung cúp của app."""
        if self.ws is not None:
            with contextlib.suppress(Exception):
                await self.ws.send(json.dumps({"t": "hangup"}))

    async def run(self) -> None:
        """Mở WS rồi chạy hai vòng; luôn ghi WAV khi xong."""
        sim, user = self.sim, self.user
        t_open = time.time()
        try:
            async with ws_connect(self.uri, max_size=None) as ws:
                self.ws = ws
                sim.log(
                    "media_open",
                    user=user.phone,
                    call_id=self.call_id,
                    t_open=t_open,
                    t_connected=time.time(),
                )
                tx = asyncio.create_task(self._send_loop(ws))
                rx = asyncio.create_task(self._recv_loop(ws))
                stopper = asyncio.create_task(self._stop.wait())
                after = user.behavior.get("hangup_after_s")
                hang = asyncio.create_task(asyncio.sleep(float(after))) if after else None
                waits = {rx, stopper} | ({hang} if hang else set())
                done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                if hang is not None and hang in done:
                    await self.send_hangup()
                    await user.sim.http.post(
                        f"/api/v1/calls/{self.call_id}/hangup", headers=user.auth()
                    )
                    sim.log("hangup_by_app", user=user.phone, call_id=self.call_id)
                    await asyncio.wait({rx}, timeout=3)
                for task in (tx, rx, stopper, hang):
                    if task is not None:
                        task.cancel()
        except Exception as exc:  # noqa: BLE001
            sim.log(
                "media_error",
                user=user.phone,
                call_id=self.call_id,
                error=type(exc).__name__,
                detail=str(exc),
            )
        finally:
            self._flush()

    async def _send_loop(self, ws: Any) -> None:
        signal = Signal(self.user.behavior)
        t0, wall0 = time.perf_counter(), time.time()
        b = self.user.behavior
        self.sim.log(
            "tx_start",
            user=self.user.phone,
            call_id=self.call_id,
            tx_t0=wall0,
            click_first_s=b["click_first_s"],
            click_period_s=b["click_period_s"],
            wav=bool(b.get("play_wav")),
        )
        i = 0
        while True:
            # mẫu cuối của khung i "được thu" lúc t0 + (i+1)*20 ms: khung chỉ gửi được khi đầy
            await asyncio.sleep(
                max(0.0, t0 + (i + 1) * FRAME_SAMPLES / SAMPLE_RATE - time.perf_counter())
            )
            await ws.send(signal.frame(i))
            i += 1

    async def _recv_loop(self, ws: Any) -> None:
        first = True
        async for msg in ws:
            now = time.time()
            if isinstance(msg, bytes):
                if first:
                    first = False
                    self.sim.log(
                        "rx_first_audio", user=self.user.phone, call_id=self.call_id, bytes=len(msg)
                    )
                self.rx.append((now, msg))
            else:
                frame = json.loads(msg)
                self.state_log.append({"wall": now, **frame})
                self.sim.log(
                    "media_state",
                    user=self.user.phone,
                    call_id=self.call_id,
                    **{k: v for k, v in frame.items() if k in ("t", "state", "reason")},
                )

    def _flush(self) -> None:
        stem = self.sim.audio_dir / f"rx_{self.user.digits}_{self.call_id[:8]}"
        pcm = b"".join(b for _, b in self.rx)
        with wave.open(str(stem.with_suffix(".wav")), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm)
        stem.with_suffix(".json").write_text(
            json.dumps(
                {
                    "call_id": self.call_id,
                    "user": self.user.phone,
                    "arrivals": [[w, len(b)] for w, b in self.rx],
                }
            ),
            encoding="utf-8",
        )
        self.user.media.pop(self.call_id, None)
        self.sim.log(
            "media_closed",
            user=self.user.phone,
            call_id=self.call_id,
            frames=len(self.rx),
            bytes=len(pcm),
            wav=str(stem.with_suffix(".wav")),
            states=self.state_log,
        )


def _read_env_token(env_file: Path) -> str | None:
    if value := os.environ.get("DEVICE_API_TOKEN"):
        return value
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().removeprefix("export ").lstrip()
            if stripped.startswith("DEVICE_API_TOKEN="):
                return stripped.split("=", 1)[1].strip().strip("\"'") or None
    return None


def build_app(sim: Sim) -> FastAPI:
    """Webhook FCM + điều khiển."""
    app = FastAPI(title="relative_sim")

    @app.post("/push")
    async def push(request: Request) -> JSONResponse:
        data = await request.json()
        token = request.headers.get("X-Push-Token", "")
        if token == "sim":  # điện thoại chủ kính (phone_sim): chuyển tiếp nguyên văn
            if not sim.forward:
                return JSONResponse({"ok": True, "dropped": True})
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(sim.forward, json=data, headers={"X-Push-Token": "sim"})
            return JSONResponse({"ok": resp.is_success}, status_code=resp.status_code)
        user = sim.by_push.get(token)
        if user is None:
            return JSONResponse({"error": "bad push token"}, status_code=403)
        user.on_push(data)
        return JSONResponse({"ok": True})

    def pick(body: dict[str, Any]) -> User:
        phone = body.get("user")
        return sim.users[phone] if phone else next(iter(sim.users.values()))

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/ctl/users")
    async def users() -> dict[str, Any]:
        return {
            p: {
                "public_user_id": u.public_id,
                "display_name": u.display_name,
                "device_id": u.device_id,
            }
            for p, u in sim.users.items()
        }

    @app.post("/ctl/behavior")
    async def behavior(request: Request) -> dict[str, Any]:
        body = await request.json()
        phone = body.pop("user", None)
        targets = [sim.users[phone]] if phone else list(sim.users.values())
        for u in targets:
            u.behavior.update(body)
        sim.log("behavior", user=phone, **body)
        return {"ok": True}

    @app.post("/ctl/call_glasses")
    async def call_glasses(request: Request) -> dict[str, Any]:
        body = await request.json()
        return await pick(body).call_glasses(body["owner_user_id"])

    @app.post("/ctl/message")
    async def message(request: Request) -> dict[str, Any]:
        body = await request.json()
        return await pick(body).send_message(
            body["owner_user_id"], body["text"], body.get("request_id") or str(uuid.uuid4())
        )

    @app.post("/ctl/get_call")
    async def get_call(request: Request) -> dict[str, Any]:
        body = await request.json()
        user = pick(body)
        resp = await sim.http.get(f"/api/v1/calls/{body['call_id']}", headers=user.auth())
        return {"http": resp.status_code, **(resp.json().get("data") or {})}

    @app.post("/ctl/history")
    async def history(request: Request) -> dict[str, Any]:
        body = await request.json()
        user = pick(body)
        resp = await sim.http.get(
            "/api/v1/messages", params={"with": body["owner_user_id"]}, headers=user.auth()
        )
        return {"http": resp.status_code, **(resp.json().get("data") or {})}

    @app.post("/ctl/hangup")
    async def hangup(request: Request) -> dict[str, Any]:
        return await pick(await request.json()).hangup()

    return app


async def _accept_loop(sim: Sim) -> None:
    while True:
        for user in list(sim.users.values()):
            with contextlib.suppress(httpx.HTTPError):
                await user.accept_pending()
        await asyncio.sleep(0.5)


async def _main(args: argparse.Namespace) -> int:
    token = _read_env_token(Path(args.env_file))
    if not token:
        print("Thiếu DEVICE_API_TOKEN (env hoặc --env-file)", file=sys.stderr)
        return 2
    scenario: dict[str, Any] = (
        json.loads(Path(args.scenario).read_text(encoding="utf-8")) if args.scenario else {}
    )
    if args.auto_reply:
        scenario.setdefault("defaults", {})["auto_reply"] = args.auto_reply
    sim = Sim(args, scenario, token)
    server = uvicorn.Server(
        uvicorn.Config(build_app(sim), host=args.host, port=args.port, log_level="warning")
    )
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    for phone in args.user:
        await sim.setup_user(phone)
    sim.spawn(_accept_loop(sim))
    sim.log("ready", app_url=args.app_url, port=args.port, users=list(sim.users))
    print(
        f"relative_sim sẵn sàng :{args.port} -> {args.app_url} ({len(sim.users)} người thân, log {args.log})",
        flush=True,
    )
    with contextlib.suppress(asyncio.CancelledError):
        await serve
    return 0


def main() -> None:
    """Điểm vào CLI."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--app-url", default="http://127.0.0.1:18001")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18003)
    ap.add_argument(
        "--user",
        action="append",
        required=True,
        help="số điện thoại người thân (lặp lại cho nhiều người)",
    )
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--log", default="relative_sim_log.jsonl")
    ap.add_argument("--audio-dir", default="relative_sim_audio")
    ap.add_argument(
        "--ab-log", default=None, help="file log AB để đọc mã OTP khi đăng ký tài khoản mới"
    )
    ap.add_argument(
        "--forward-sim",
        default=None,
        help="URL /push của phone_sim (chủ kính) cho push token 'sim'",
    )
    ap.add_argument(
        "--media-url",
        default=None,
        help="ghi đè media_url của AB (vd ws://127.0.0.1:18888/v2/call khi sim chạy cùng máy VH)",
    )
    ap.add_argument(
        "--auto-reply",
        default=None,
        help="câu tự nhắn lại chủ kính mỗi khi nhận tin (mọi người thân); mặc định không",
    )
    ap.add_argument("--env-file", default=str(_DEFAULT_ENV))
    sys.exit(asyncio.run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
