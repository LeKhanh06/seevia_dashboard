"""Kết quả của Final Router cho một lượt `/v2/turn`."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


@dataclass
class Reply:
    # Cue phát từ flash TRƯỚC audio (`X-Cue`).
    cues: list[str] = field(default_factory=list)
    # Mảnh text cho VieNeu-TTS; rỗng nếu chỉ có cue. Iterable đồng bộ vì TTS đọc
    # nó trong thread nền (`tts.synthesize_text_stream`).
    speech: Iterable[str] = ()
    # "ocr" | "nav": sau khi phát xong kính mở `WS /v2/live` (`X-Live`).
    live: str | None = None
    live_token: str | None = None
    # Nhãn cuối, chỉ để log/bench.
    intent: str = ""
    lane: str = ""
    # Tham số làn D đã rút ra (vd. {"destination": "..."}), chỉ để hiện ở khung debug của demo.
    params: dict = field(default_factory=dict)
    # Làn D: gọi khi khối audio ĐỌC LẠI đầu tiên đã được giao cho kính (socket / StreamingResponse). Đối số là
    # số giây audio đứng trước câu đọc lại phía transport (vd. "Đã tiếp nhận yêu cầu" + đệm của /process).
    # Lệnh chờ chỉ bắt đầu đếm ngược từ đây (`pending_action.arm`), không phải từ lúc `route()`.
    on_audio_start: Callable[[float], None] | None = None
    # Thống kê (pipeline/usage_turn.py): kết quả DỰ KIẾN của lượt, chốt khi kính phát xong. `keeps_open`: lượt
    # kết thúc bằng một câu hỏi/nhắc chụp lại -> tác vụ còn mở chờ người dùng thử lại (ghép lượt kế tiếp).
    outcome: str = "success"
    error_code: str | None = None
    keeps_open: bool = False
    # Lượt thống kê của lời đáp này (đường HTTP chốt `playback` khi giao xong byte cuối).
    usage_attempt: str | None = None
