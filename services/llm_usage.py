"""Đo token mỗi lời gọi LLM, gửi lên AB thành sự kiện `llm_call` (tiền tính ở AB lúc đọc, theo bảng giá).

Đọc `usage` của phản hồi; request stream chỉ thêm `stream_options.include_usage` (models/llm/client.py,
tắt bằng LLM_STREAM_USAGE=0). Khi phản hồi không có `usage` (lượt bị huỷ trước mảnh cuối, server không gửi)
thì ước lượng từ số ký tự + số ảnh và gắn `estimated=True`.

Gắn vào lượt: speculation/prewarm chạy TRƯỚC khi lượt có `attempt_id` (giữ chỗ lúc nhả nút), còn câu trả
lời làn A vẫn stream SAU khi route xong. Nên đầu lượt mở một sổ (`start_turn`, ContextVar — task con copy
context vẫn thấy cùng sổ); lời gọi xong trước khi biết lượt thì giữ trong sổ, `bind_attempt` gắn mã rồi xả.
Lời gọi ngoài lượt (khởi động, giữ kết nối) gửi với `attempt_id` rỗng.

Không có chữ người dùng/câu trả lời trong sự kiện — chỉ số đếm.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import config
from services import usage

logger = logging.getLogger("blind_assist.llm_usage")

STATUSES = ("ok", "cancelled", "error")


class _Ledger:
    __slots__ = ("attempt_id", "bound", "pending", "lock")

    def __init__(self) -> None:
        self.attempt_id: str | None = None
        self.bound = False
        self.pending: list[dict[str, Any]] = []
        self.lock = threading.Lock()


_ledger: contextvars.ContextVar[_Ledger | None] = contextvars.ContextVar("llm_ledger", default=None)
_purpose: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_purpose", default=None)


def start_turn() -> None:
    """Mở sổ cho lượt hiện tại (gọi đầu `pipeline/turn.run`, trước mọi prewarm/speculation)."""
    _ledger.set(_Ledger())


def bind_attempt(attempt_id: str | None) -> None:
    """Lượt đã có (hoặc chắc chắn không có) `attempt_id`: gắn rồi gửi các lời gọi đang giữ."""
    led = _ledger.get()
    if led is None:
        return
    with led.lock:
        led.attempt_id, led.bound = attempt_id, True
        pending, led.pending = led.pending, []
    for fields in pending:
        _emit(attempt_id, fields)


@contextlib.contextmanager
def purpose(name: str) -> Iterator[None]:
    """Đặt mục đích cho các lời gọi LLM bên trong (`money`, `ocr`, `stt_pick`…)."""
    token = _purpose.set(name)
    try:
        yield
    finally:
        _purpose.reset(token)


def current_ledger() -> _Ledger | None:
    """Sổ của lượt đang chạy — lấy LÚC BẮT ĐẦU lời gọi: stream của lượt trước kết thúc muộn trong cùng
    task không bị tính nhầm sang sổ của lượt sau."""
    return _ledger.get()


_CURRENT = object()


def current_purpose(default: str) -> str:
    return _purpose.get() or default


def provider_of(base_url: str) -> str:
    """Nhãn nhánh model từ URL: so chi phí giữa các nhánh chạy song song."""
    host = (urlparse(base_url).netloc or base_url).lower()
    if "uit.edu.vn" in host:
        return "uit"
    if host.endswith(":20128") or "9router" in host:
        return "9router"
    if "generativelanguage.googleapis.com" in host:
        return "aistudio"
    if "aiplatform.googleapis.com" in host:
        return "vertex"
    return host or "unknown"


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def normalize_openai(raw: dict | None) -> dict[str, int] | None:
    """`usage` kiểu OpenAI -> {in, out, reasoning, cached}. `out` = mọi token ra phải trả tiền (gồm thinking).

    Chuẩn OpenAI: `completion_tokens` ĐÃ gồm `reasoning_tokens`. Đo 9router/Antigravity 2026-11-22: có lượt
    `completion_tokens`=3, `reasoning_tokens`=290 (không gồm) — nên reasoning > completion thì cộng thêm."""
    if not isinstance(raw, dict) or "prompt_tokens" not in raw:
        return None
    comp = _int(raw.get("completion_tokens"))
    reasoning = _int((raw.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    cached = _int((raw.get("prompt_tokens_details") or {}).get("cached_tokens"))
    out = comp + reasoning if reasoning > comp else comp
    return {"in": _int(raw.get("prompt_tokens")), "out": out, "reasoning": reasoning, "cached": cached}


def normalize_gemini(meta: Any) -> dict[str, int] | None:
    """`usage_metadata` của google-genai (đối tượng hoặc dict). Gemini tính thinking riêng khỏi candidates."""
    if meta is None:
        return None
    get = meta.get if isinstance(meta, dict) else (lambda k: getattr(meta, k, None))
    if get("prompt_token_count") is None:
        return None
    thoughts = _int(get("thoughts_token_count"))
    return {
        "in": _int(get("prompt_token_count")),
        "out": _int(get("candidates_token_count")) + thoughts,
        "reasoning": thoughts,
        "cached": _int(get("cached_content_token_count")),
    }


def prompt_shape(messages: list[dict]) -> tuple[int, int]:
    """(số ký tự chữ, số ảnh) của prompt — đủ để ước lượng token khi phản hồi không có `usage`."""
    chars = images = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            chars += len(content)
            continue
        for part in content or []:
            if part.get("type") == "text":
                chars += len(part.get("text") or "")
            elif part.get("type") == "image_url":
                images += 1
    return chars, images


def estimate(prompt_chars: int, images: int, output_chars: int) -> dict[str, int]:
    per = max(config.LLM_EST_CHARS_PER_TOKEN, 0.5)
    return {
        "in": round(prompt_chars / per) + images * config.LLM_EST_IMAGE_TOKENS,
        "out": round(output_chars / per),
        "reasoning": 0,
        "cached": 0,
    }


def record(
    *,
    provider: str,
    model: str,
    purpose: str,
    status: str,
    tokens: dict[str, int] | None,
    prompt_chars: int = 0,
    images: int = 0,
    output_chars: int = 0,
    latency_ms: float | None = None,
    ledger: Any = _CURRENT,
) -> None:
    """Ghi một lời gọi. `tokens` None -> ước lượng. Không bao giờ ném lỗi ra đường lượt."""
    try:
        estimated = tokens is None
        if tokens is None:
            # Lỗi trước khi có phản hồi (429/5xx/mất mạng): không sinh token nào -> không tính tiền.
            tokens = estimate(prompt_chars, images, output_chars) if status != "error" or output_chars else {
                "in": 0, "out": 0, "reasoning": 0, "cached": 0}
        fields = {
            "occurred_at": usage.now_iso(),
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "status": status if status in STATUSES else "error",
            "in_tokens": tokens["in"],
            "out_tokens": tokens["out"],
            "reasoning_tokens": tokens["reasoning"],
            "cached_tokens": tokens["cached"],
            "images": images,
            "estimated": estimated,
            "latency_ms": round(latency_ms) if latency_ms is not None else None,
        }
        logger.debug("llm %s/%s %s %s in=%d out=%d%s", provider, model, purpose, fields["status"],
                     fields["in_tokens"], fields["out_tokens"], " (ước)" if estimated else "")
        led = _ledger.get() if ledger is _CURRENT else ledger
        if led is not None:
            with led.lock:
                if not led.bound:
                    led.pending.append(fields)
                    return
                attempt_id = led.attempt_id
        else:
            attempt_id = None
        _emit(attempt_id, fields)
    except Exception:  # noqa: BLE001 — đo đếm không được làm hỏng lượt
        logger.exception("llm_usage: ghi lời gọi lỗi")


def _emit(attempt_id: str | None, fields: dict[str, Any]) -> None:
    usage.emit("llm_call", attempt_id=attempt_id, **fields)
