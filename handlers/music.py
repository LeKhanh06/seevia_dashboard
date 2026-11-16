import re
from collections.abc import Iterator

import config
from handlers.action_flow import recall, remember, run_action

# Đo trên Spotify (2026-10-22): tìm theo tên bài trơn ("Lạc trôi", "Nơi này có anh", "Hãy trao cho anh") ra đúng
# bản gốc ở vị trí 1, nên KHÔNG hỏi "ca sĩ nào?". Câu hỏi đó còn chạy ở nền làn D nên câu trả lời của người dùng
# không quay về đây được: lệnh không bao giờ được gửi. Sai bài thì câu kết quả đọc tên + ca sĩ thật, người dùng nói
# lại kèm ca sĩ là xong.



# "mở nhạc đi" / "bật nhạc lên" / "đổi bài khác" là ý muốn nghe nhạc, chưa phải một bài cụ thể. Bộ phân loại trả
# 'song': 'nhạc' hoặc không trả gì (đo thật). Không đem nguyên câu đi tìm (ra bài tên "Nhạc"), cũng không hỏi lại:
# phát luôn một bài phổ biến, xoay vòng theo kính để "bài khác" ra bài khác; câu kết quả đọc tên bài thật.
_GENERIC_SONG_WORDS = frozenset({
    "nhạc", "bài hát", "bài nhạc", "bài", "một bài", "một bài hát",
    "nhạc đi", "bật nhạc", "mở nhạc", "nghe nhạc", "phát nhạc",
    "bài khác", "bài hát khác", "bài tiếp", "bài tiếp theo", "đổi bài", "đổi bài khác", "chuyển bài",
    "nhạc hay", "bài gì cũng được", "gì cũng được", "bất kỳ",
})
# Truy vấn Spotify cho yêu cầu chung chung (đo 2026-10-22: mỗi cụm ra một bài tiếng Việt phổ biến ở vị trí 1).
_GENERIC_QUERIES = ("nhạc Việt", "nhạc trẻ", "nhạc trữ tình", "nhạc Trịnh", "bolero")
_GENERIC_TURN = "generic_song_turn"

# Không mặc định "tăng": người khiếm thị không nhìn được thanh âm lượng, một
# tiếng to đột ngột không ai yêu cầu là thứ họ không kịp trở tay.
_ASK_VOLUME = "Bạn muốn tăng hay giảm âm lượng?"

# `music_play` là action chờ lâu nhất (tới 40s), nên cũng là chỗ dễ khiến người
# dùng tưởng máy treo nhất.
_PLAY_PROGRESS = (
    "Vẫn đang tìm bài hát trên ứng dụng nhạc.",
    "Đã mở ứng dụng nhạc, đang chờ bài hát bắt đầu phát.",
    "Vẫn đang chờ nhạc, bạn chờ thêm chút nữa.",
    "Ứng dụng nhạc đang tải bài.",
    "Sắp xong rồi, bạn chờ một chút.",
)

_STOP_ACK = "Đang dừng nhạc."
_STOP_FAILURE = "Chưa dừng được nhạc. Bạn thử lại nhé."
_STOP_PROGRESS = ("Vẫn đang dừng nhạc, bạn chờ một chút.",)

_VOLUME_ACK = "Đang chỉnh âm lượng."
_VOLUME_FAILURE = "Chưa chỉnh được âm lượng. Bạn thử lại nhé."
_VOLUME_PROGRESS = ("Vẫn đang chỉnh âm lượng, bạn chờ một chút.",)

# Câu cố định của module này, để `pipeline/phrases.py` dựng sẵn audio. Câu ack
# của `music_play` có tên bài nên không dựng sẵn được cả câu — chỉ nửa sau là cố
# định, và `_sentences()` cắt ở dấu chấm nên đúng nửa đó vẫn trúng cache.
STATIC_SPEECH: tuple[str, ...] = (

    _ASK_VOLUME,
    *_PLAY_PROGRESS,
    _STOP_ACK,
    _STOP_FAILURE,
    *_STOP_PROGRESS,
    _VOLUME_ACK,
    _VOLUME_FAILURE,
    *_VOLUME_PROGRESS,
)


def names_a_song(song: str | None) -> bool:
    """`song` có phải một cái tên thật, hay chỉ là cách nói "tôi muốn nghe nhạc"."""
    cleaned = " ".join((song or "").strip().strip(".,!?").lower().split())
    cleaned = re.sub(r"\s+(?:đi|lên|nhé|nha|với|đi nào|cho tôi|giúp tôi)$", "", cleaned)
    return bool(cleaned) and cleaned not in _GENERIC_SONG_WORDS


def generic_query() -> str:
    """Bài cho yêu cầu chung chung; lần sau ra truy vấn kế tiếp (theo kính)."""
    turn = int(recall(_GENERIC_TURN) or 0)
    remember(_GENERIC_TURN, str(turn + 1))
    return _GENERIC_QUERIES[turn % len(_GENERIC_QUERIES)]


def handle_play(image: bytes, command_text: str, params: dict | None = None) -> Iterator[str]:
    params = params or {}
    volume = params.get("volume")
    song = params["song"].strip() if names_a_song(params.get("song")) else generic_query()

    payload = {"song": song}
    if volume is not None and isinstance(volume, int) and 0 <= volume <= 100:
        payload["volume"] = volume

    yield from run_action(
        "music_play",
        "api/v1/service/music/play",
        payload,
        ack=f"Đang mở nhạc, tìm bài {song}.",
        failure=f"Chưa phát được bài {song}. Bạn thử lại nhé.",
        result_timeout=config.VISIONCARE_MUSIC_RESULT_TIMEOUT_SECONDS,
        progress=_PLAY_PROGRESS,
    )


def handle_stop(image: bytes, command_text: str, params: dict | None = None) -> Iterator[str]:
    yield from run_action(
        "music_stop",
        "api/v1/service/music/stop",
        {},
        ack=_STOP_ACK,
        failure=_STOP_FAILURE,
        progress=_STOP_PROGRESS,
    )


def handle_volume(image: bytes, command_text: str, params: dict | None = None) -> Iterator[str]:
    params = params or {}
    direction = params.get("direction")
    level = params.get("volume")

    payload = {}
    if direction in ("up", "down"):
        payload["direction"] = direction
    elif isinstance(level, int) and 0 <= level <= 100:
        payload["level"] = level
    else:
        # Trước đây mặc định "up". Nghe không rõ mà tự tăng là bắt người khiếm
        # thị hứng một tiếng to họ không yêu cầu, và họ không nhìn được thanh âm
        # lượng để chỉnh lại. Hỏi một câu rẻ hơn nhiều.
        yield _ASK_VOLUME
        return

    yield from run_action(
        "music_volume",
        "api/v1/service/music/volume",
        payload,
        ack=_VOLUME_ACK,
        failure=_VOLUME_FAILURE,
        progress=_VOLUME_PROGRESS,
    )
