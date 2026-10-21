import io
import re
import threading
import wave
from collections.abc import Iterable, Iterator
from functools import lru_cache
from queue import Full, Queue

import numpy as np

import config
from pipeline import adpcm, device_ctx

# VieNeu-TTS v3 Turbo outputs 48 kHz mono float32 audio.
SAMPLE_RATE = 48000

# Tốc độ lấy mẫu của PCM stream gửi về MCU. 16 kHz phủ đủ dải giọng nói mà chỉ
# tốn 32 KB/s thay vì 96 KB/s, nên MCU cần buffer nhỏ hơn và WiFi nhẹ tải hơn.
OUTPUT_SAMPLE_RATE = 16000

_DECIMATION = SAMPLE_RATE // OUTPUT_SAMPLE_RATE

# Cắt câu ở dấu kết câu KÈM khoảng trắng theo sau, hoặc ở xuống dòng. Đòi hỏi
# khoảng trắng là để "50.000 đồng" không bị xẻ đôi — tiếng Việt dùng dấu chấm
# làm dấu phân cách hàng nghìn, cắt ở đó thì TTS đọc thành "năm mươi" rồi
# "không nghìn đồng".
_SENTENCE_END = re.compile(r"[.!?…]\s|\n")

# Xuống dòng do MODEL viết (`speech.SyncIter` đổi "\n" thành dấu này). Khác "\n"
# thật — dấu do handler đặt để báo "câu này trọn vẹn, đọc đi" (xem
# `_UTTERANCE_END` ở `handlers/action_flow.py`) và luôn cắt — xuống dòng của model
# chỉ là gợi ý: model hay ngắt dòng giữa cụm ("... đang hiển thị thông\nbáo tắt
# máy", "THÔNG\nTIN TUYÊN TRUYỀN"), và cắt cứng ở đó thì mỗi mảnh là một lần TTS
# riêng, ngữ điệu gãy giữa cụm. `_sentences()` quyết định cắt hay nối.
SOFT_BREAK = "\u2028"

# Text dài mà không có dấu câu nào (model liệt kê một mạch) vẫn phải cắt, nếu
# không câu đầu phải chờ tới lúc Gemini viết xong. 200 nằm dưới ngưỡng chia
# chunk 256 ký tự của VieNeu nên không tạo thêm lần cắt thứ hai bên trong model.
_MAX_SENTENCE_CHARS = 200

# Mảnh ngắn hơn ngưỡng này thì gộp vào phần text theo sau thay vì đọc riêng.
# Chủ yếu để cứu đầu mục đánh số: "1. Ràng buộc bộ nhớ..." bị luật cắt-ở-dấu-
# chấm xẻ thành "1." và phần còn lại, khiến TTS đọc rời "một." rồi mới tới nội
# dung — nghe cụt. Mỗi lệnh gọi TTS còn có chi phí cố định đáng kể: đo thật
# thấy 3 mảnh "1." "2." "3." tốn 2.94s để sinh 1.68s audio.
_MIN_SENTENCE_CHARS = 24

# Trần hàng đợi PCM giữa thread tổng hợp và luồng gửi. Mảnh PCM đo được trung
# bình ~0.8s audio, nên 32 mảnh ~ 25 giây audio ~ 800 KB — đủ để thread nền
# không bao giờ phải chờ luồng gửi, mà vẫn chặn trần bộ nhớ mỗi request.
_PIPELINE_QUEUE_CHUNKS = 32

# CBR để byte-rate cố định suốt stream — server tính đệm preroll bằng phép
# nhân đơn giản (giây * byte-rate), giống cách đã làm với PCM trần.
MP3_BITRATE_KBPS = 32
MP3_BYTE_RATE = MP3_BITRATE_KBPS * 1000 // 8

# ADPCM cũng CBR: 4 bit/mẫu cộng 4 byte đầu mỗi khối 256 byte.
ADPCM_BYTE_RATE = adpcm.BYTE_RATE  # ~8110.9 B/s ở 16 kHz


# Mệnh đề đầu tiên của lượt: câu đầu đã dài (>= TRIGGER ký tự mà chưa tới dấu chấm)
# và có dấu phẩy/chấm phẩy/hai chấm sau MIN ký tự thì cắt ở đó, để tiếng đầu không
# phải chờ model viết hết cả câu. Câu ngắn hơn TRIGGER giữ nguyên một mảnh: cắt đôi
# câu ngắn chỉ thêm một lần TTS và một chỗ gãy ngữ điệu.
_FIRST_CLAUSE_MIN_CHARS = 24
_FIRST_CLAUSE_TRIGGER_CHARS = 70
_CLAUSE_END = re.compile(r"[,;:]\s")

# Từ chức năng không bao giờ đứng cuối một cụm: câu kết thúc ở đây là câu bị cắt
# giữa chừng, nối với mảnh sau thay vì đọc riêng.
_DANGLING = frozenset({
    "và", "của", "với", "để", "cho", "là", "ở", "tại", "trong", "từ", "đến", "tới", "các", "những", "một",
    "như", "hoặc", "hay", "thì", "mà", "bởi", "do", "theo", "về", "nếu", "khi", "nên", "rằng", "vì", "sẽ",
    "đã", "đang", "rất", "cũng", "còn", "bị", "được", "trên", "dưới", "giữa", "sau", "trước", "qua", "bằng",
})


def _last_word(text: str) -> str:
    words = text.rstrip(" ,;:").split()
    return words[-1].lower() if words else ""


def _break_cuts(head: str, following: str | None, first: bool) -> bool:
    """Xuống dòng của model sau `head` có phải chỗ cắt câu không?

    `following` là phần text ngay sau chỗ xuống dòng (None = chưa tới, model còn
    đang viết): chỉ dùng khi đã có sẵn, không bao giờ chờ nó — chờ là trễ tiếng đầu.
    """
    if not head:
        return False
    if head[-1] in ".!?…":
        return first or len(head) >= _MIN_SENTENCE_CHARS  # "Ngoài trời." đầu lượt đọc ngay; "1." đứng giữa thì nối
    if head[-1] in ",;:":
        return first or len(head) >= _MIN_SENTENCE_CHARS // 2
    if _last_word(head) in _DANGLING:
        return False
    if following and following[0].islower():
        return False  # dòng sau viết thường = câu vẫn đang tiếp
    return len(head) >= _MIN_SENTENCE_CHARS


def _split_long(text: str) -> tuple[str, str]:
    """Cắt `text` quá dài, ưu tiên sau dấu phẩy; không để câu kết thúc ở từ chức năng.

    Trả (phần đọc, phần dư). Không bao giờ cắt giữa từ.
    """
    window = text[:_MAX_SENTENCE_CHARS + 1]
    cut = -1
    for match in _CLAUSE_END.finditer(window):
        if match.start() >= _MAX_SENTENCE_CHARS // 3:
            cut = match.end() - 1
    if cut == -1:
        cut = window.rfind(" ", 0, _MAX_SENTENCE_CHARS)
        # Lùi qua từ chức năng cuối ("... của | nhà"): đọc hết cụm rồi mới ngắt.
        for _ in range(3):
            if cut > _MAX_SENTENCE_CHARS // 3 and _last_word(text[:cut]) in _DANGLING:
                cut = text.rfind(" ", 0, cut)
            else:
                break
    if cut == -1:  # một từ dài hơn cả ngưỡng: cắt cứng, thà đọc còn hơn treo
        cut = _MAX_SENTENCE_CHARS
    return text[:cut], text[cut:]


def _sentences(text_chunks: Iterable[str]) -> Iterator[str]:
    """Gom mảnh text rời rạc thành từng câu đủ nghĩa để đưa sang TTS.

    Trả câu ngay khi đủ, không đợi đọc hết nguồn — đó là điều kiện để giọng
    nói bắt đầu trong lúc Gemini còn đang viết.
    """
    buffer = ""
    emitted = 0
    cut_midphrase = False  # câu vừa trả ra bị cắt giữa cụm: mảnh kế là phần đuôi của nó, đọc ngay khi tới dấu chấm

    for chunk in text_chunks:
        buffer += chunk
        while True:
            match = _SENTENCE_END.search(buffer)
            soft = buffer.find(SOFT_BREAK)
            if soft != -1 and (match is None or soft < match.end()):
                # Xuống dòng do model viết (có thể liền sau dấu chấm). Cắt hay nối tuỳ ngữ cảnh.
                rest_at = soft
                while rest_at < len(buffer) and buffer[rest_at].isspace():
                    rest_at += 1
                paragraph = sum(1 for c in buffer[soft:rest_at] if c in (SOFT_BREAK, "\n")) >= 2
                head = buffer[:soft].strip()
                following = buffer[rest_at:] if rest_at < len(buffer) else None
                if not head or not (paragraph or _break_cuts(head, following, emitted == 0)):
                    buffer = (buffer[:soft].rstrip() + " " + buffer[rest_at:]) if head else buffer[rest_at:]
                    if following is None:
                        break  # nối rồi mà chưa có chữ nào theo sau: chờ chunk kế
                    continue
                sentence, buffer = head, buffer[rest_at:]
            elif match:
                if match.end() < _MIN_SENTENCE_CHARS and "\n" not in match.group() and not cut_midphrase:
                    # Mảnh quá ngắn để đọc riêng ("1.", "Xong."): chờ thêm text
                    # rồi gộp. Tìm dấu kết câu tiếp theo đủ xa để cắt.
                    #
                    # Trừ khi cắt ở xuống dòng: đó là dấu người GỌI chủ động đặt
                    # để báo "câu này trọn vẹn rồi, đọc đi" (xem `_UTTERANCE_END`
                    # ở `handlers/action_flow.py`). Chờ gộp một câu như vậy là
                    # giữ nó lại tới lúc có mảnh sau — mà mảnh sau của handler
                    # điều khiển điện thoại là cả chục giây nữa.
                    later = _SENTENCE_END.search(buffer, _MIN_SENTENCE_CHARS)
                    if later is None:
                        break
                    match = later
                sentence, buffer = buffer[: match.end()], buffer[match.end():]
            elif (emitted == 0 and len(buffer) >= _FIRST_CLAUSE_TRIGGER_CHARS
                  and (clause := _CLAUSE_END.search(buffer, _FIRST_CLAUSE_MIN_CHARS - 1))):
                # Câu đầu còn dài mà đã có dấu phẩy: đọc mệnh đề đầu ngay.
                sentence, buffer = buffer[: clause.end()], buffer[clause.end():]
            elif len(buffer) > _MAX_SENTENCE_CHARS:
                sentence, buffer = _split_long(buffer)
            else:
                break
            if sentence.strip():
                emitted += 1
                cut_midphrase = sentence.strip()[-1] not in ".!?…,;:"
                yield sentence.strip()

    # Phần đuôi luôn phải đọc, kể cả khi ngắn hơn ngưỡng gộp — không được nuốt.
    tail = buffer.replace(SOFT_BREAK, " ").strip()
    if tail:
        yield tail


# Câu nói cố định đã tổng hợp sẵn: khoá text -> PCM 16 kHz trọn câu.
#
# Toàn bộ câu trấn an, câu báo lỗi, câu nhắc bấm thông báo đều viết cứng trong
# code — chữ không đổi giữa các lần chạy, nhưng trước đây vẫn gọi model mỗi lần
# và bắt người dùng chờ đúng lúc họ đang cần nghe ngay. Xem `pipeline/phrases.py`
# để biết chỗ nạp.
_PHRASE_PCM: dict[str, bytes] = {}

# Kích thước mảnh khi phát lại câu dựng sẵn: ~0.5s audio, cùng cỡ với mảnh model
# sinh ra, để phần đệm preroll và hàng đợi phía dưới không phải xử lý khác đi.
_PHRASE_CHUNK_BYTES = OUTPUT_SAMPLE_RATE * 2 // 2


# Ước lượng thời gian đọc cho câu KHÔNG dựng sẵn, khớp tuyến tính trên 96 câu
# đã dựng sẵn (giọng Ngọc Linh, style tự nhiên). Đổi giọng thì đo lại bằng
# `speaking_seconds` trên chính cache mới.
_SECONDS_PER_CHAR = 0.0466
_SPEECH_FIXED_SECONDS = 0.324

_PCM_BYTES_PER_SAMPLE = 2


def phrase_key(text: str) -> str:
    """Khoá tra câu dựng sẵn.

    Gộp mọi khoảng trắng về một dấu cách: câu đi tới đây đã qua `_sentences()`
    (ghép từ nhiều mảnh, cắt ở dấu câu) nên có thể lệch khoảng trắng so với
    hằng số gốc, mà lệch một dấu cách là trượt cache và mất hết cái lợi.
    """
    return " ".join(text.split())


def register_phrase(text: str, pcm: bytes) -> None:
    """Nạp sẵn PCM cho một câu cố định."""
    _PHRASE_PCM[phrase_key(text)] = pcm


def prerendered(text: str) -> bytes | None:
    """PCM đã dựng sẵn cho `text`, hoặc None nếu phải tổng hợp lúc chạy."""
    return _PHRASE_PCM.get(phrase_key(text))


def speaking_seconds(text: str) -> float:
    """Đọc `text` lên mất bao nhiêu giây.

    Câu đã dựng sẵn thì đo chính xác từ chính PCM sắp phát. Câu động (có tên
    bài, tên người, địa chỉ) chưa tồn tại lúc hỏi, nên ước lượng theo độ dài —
    khớp tuyến tính trên 96 câu dựng sẵn cho sai số trung bình 0.17s, tối đa
    1.30s. Đủ chính xác cho việc duy nhất nó phục vụ: giãn câu trấn an kế tiếp
    ra sau câu đang nói, thay vì đếm từ lúc bắt đầu nói.
    """
    pcm = prerendered(text)
    if pcm is not None:
        return len(pcm) / (OUTPUT_SAMPLE_RATE * _PCM_BYTES_PER_SAMPLE)
    return _SECONDS_PER_CHAR * len(text) + _SPEECH_FIXED_SECONDS


def clear_phrases() -> None:
    _PHRASE_PCM.clear()


def phrase_count() -> int:
    return len(_PHRASE_PCM)


def _materialized_fetch(original):
    """Bọc `OnnxV3LiteEngine._fetch`: trả thư mục THẬT thay vì snapshot HF toàn symlink.

    onnxruntime >= 1.31 từ chối file `.data` (external data) nằm ngoài thư mục
    thật của file `.onnx` ("External data path escapes model directory"). Cache
    HF lưu mỗi file là symlink vào `blobs/<hash>`, nên `.onnx` và `.data` của
    cùng một model trỏ về hai thư mục khác nhau -> server chết lúc khởi động.
    Hard link vào một thư mục chung: không tốn thêm đĩa, không còn symlink.
    """
    import os
    from pathlib import Path

    def fetch(repo, files, subfolder):
        snapshot = Path(original(repo, files, subfolder))
        target = Path(config._CACHE_ROOT) / "onnx_materialized" / repo.replace("/", "__") / (subfolder or "_root")
        target.mkdir(parents=True, exist_ok=True)
        for entry in snapshot.iterdir():
            if not entry.is_file():
                continue
            real, dest = entry.resolve(), target / entry.name
            if dest.exists() and dest.stat().st_ino == real.stat().st_ino:
                continue
            dest.unlink(missing_ok=True)
            try:
                os.link(real, dest)
            except OSError:
                import shutil
                shutil.copyfile(real, dest)
        return target

    return staticmethod(fetch)


@lru_cache(maxsize=1)
def _load_tts():
    from vieneu import Vieneu
    from vieneu._v3_turbo_engine.onnx_runtime_lite import OnnxV3LiteEngine

    if not getattr(OnnxV3LiteEngine, "_seevia_materialized", False):
        OnnxV3LiteEngine._fetch = _materialized_fetch(OnnxV3LiteEngine._fetch)
        OnnxV3LiteEngine._seevia_materialized = True

    # GPU khi có. Đo 2026-10-19 (RTX 2080 Ti, máy chủ dùng chung), 4 câu x 2 lượt:
    #   ONNX/CPU      chunk đầu p50 1220 ms, RTF 3,20 (chậm hơn thời gian thực)
    #   PyTorch/CUDA  chunk đầu p50  126 ms, RTF 0,76
    # TTS nằm thẳng trên đường "nhả nút -> tiếng đầu", nên chênh 1,1 s này là
    # chênh người dùng nghe thấy. vieneu chỉ có đồ thị ONNX cho đường CPU, nên
    # GPU đi backend PyTorch. `TTS_DEVICE=cpu` để quay về ONNX/CPU.
    device = config.TTS_DEVICE
    if device == "auto":
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
    return Vieneu(device=device)


def synthesize(text: str) -> bytes:
    """Text tiếng Việt -> WAV bytes (VieNeu-TTS v3 Turbo, giọng nữ)."""
    tts = _load_tts()
    audio = tts.infer(text, voice=config.TTS_VOICE, style=config.TTS_STYLE)

    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (pcm * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def synthesize_stream(text: str) -> Iterator[bytes]:
    """Text tiếng Việt -> từng mảnh PCM 16-bit little-endian mono 16 kHz.

    Khác `synthesize()` ở chỗ mảnh đầu tiên sẵn sàng ngay khi model giải mã
    xong nhóm frame đầu, không phải chờ tổng hợp hết câu. Người nghe bắt đầu
    nghe được sau vài trăm ms thay vì vài giây.

    Không có header WAV: MCU biết trước định dạng nên đẩy thẳng byte vào I2S.
    """
    tts = _load_tts()
    # Số mẫu mỗi mảnh không chắc chia hết cho _DECIMATION. Phần dư được giữ lại
    # để ghép vào đầu mảnh sau, nên không mẫu nào rơi mất ở biên giữa hai mảnh.
    carry = np.empty(0, dtype=np.float32)

    for chunk in tts.infer_stream(text, voice=config.TTS_VOICE, style=config.TTS_STYLE):
        samples = np.clip(np.asarray(chunk, dtype=np.float32), -1.0, 1.0)
        buf = np.concatenate((carry, samples))
        usable = len(buf) - len(buf) % _DECIMATION
        carry = buf[usable:]
        if usable == 0:
            continue

        # Trung bình từng nhóm 3 mẫu, không phải buf[::3]: decimate trần bỏ qua
        # bước lọc nên mọi tần số trên 8 kHz gập ngược vào dải giọng nói.
        downsampled = buf[:usable].reshape(-1, _DECIMATION).mean(axis=1)
        yield (downsampled * 32767).astype("<i2").tobytes()


def _sentence_pcm(sentence: str) -> Iterator[bytes]:
    """PCM của một câu: lấy bản dựng sẵn nếu có, không thì gọi model.

    Đây là chỗ ăn tiền của cache. Câu như "Hệ thống đang xử lý yêu cầu, bạn chờ
    một chút." đo được 1.4s tổng hợp — mà nó tồn tại chính là để lấp quãng chờ,
    nên trả nó chậm là tự tạo thêm đúng quãng chờ cần lấp.
    """
    cached = prerendered(sentence)
    if cached is None:
        yield from synthesize_stream(sentence)
        return

    for start in range(0, len(cached), _PHRASE_CHUNK_BYTES):
        yield cached[start:start + _PHRASE_CHUNK_BYTES]


def _pause_pcm(seconds: float | None = None) -> bytes:
    """Khoảng lặng chèn giữa hai câu liền nhau.

    Đọc `config` mỗi lần chứ không tính sẵn ở module: test cần chỉnh được độ dài
    này, mà hằng số tính lúc import thì monkeypatch không với tới.
    """
    if seconds is None:
        seconds = config.SPEECH_SENTENCE_PAUSE_SECONDS
    samples = int(seconds * OUTPUT_SAMPLE_RATE)
    return b"\x00\x00" * samples


def _spoken_pcm(sentence: str, first: bool) -> Iterator[bytes]:
    """PCM của một câu, kèm chỗ thở trước nó nếu không phải câu đầu (đường cũ, `SPEECH_SMOOTH_JOINS=0`).

    Model chỉ tổng hợp trong phạm vi từng câu, nên nối thẳng đầu câu sau vào
    đuôi câu trước là một tràng tiếng liên tục không có chỗ ngắt. Người khiếm
    thị chỉ có kênh âm thanh để theo dõi: câu chào, câu trấn an, câu báo kết quả
    và câu trả lời của model dính liền nhau thì không nghe ra được đâu là ranh
    giới giữa chúng.

    Chèn ở đây, giữa MỌI cặp câu liền nhau, chứ không riêng chỗ giáp ranh giữa
    hai loại câu: `_sentences()` cắt xong thì không còn biết câu nào từ nguồn
    nào, và một đoạn model viết dài cũng cần ngắt hơi y như vậy.
    """
    if not first:
        pause = _pause_pcm()
        if pause:
            yield pause
    yield from _sentence_pcm(sentence)


# ── nối mượt giữa các câu ────────────────────────────────────────────────────
#
# Đo (2026-10-22, 6 câu x 2 lượt): VieNeu để 0-124 ms lặng đầu và 30-180 ms lặng
# cuối mỗi câu, và có câu bắt đầu ở biên độ 3675/32768 (11%) — nối sau một khoảng
# `\x00` là một cái "tách". Trước đây mọi chỗ nối đều chèn 0,45 s, kể cả chỗ cắt
# giữa cụm ("... đang hiển | thị thông báo"), nghe như khựng giữa câu.
#
# Giờ chỗ nối có 4 loại, theo chữ cuối câu trước và chữ đầu câu sau:
#   end     sau . ! ? …                     nghỉ đủ nghe sang câu khác (giữ như cũ)
#   clause  sau , ; :                       nghỉ ngắn
#   line    hết dòng, dòng sau viết hoa     nghỉ vừa (mục tiếp theo của biển/menu)
#   soft    câu cắt giữa cụm                gần như liền, chỉ vừa đủ thở
# Quãng lặng tự nhiên của model được cắt bớt ở các chỗ clause/line/soft để tổng
# quãng nghỉ do ta quyết định, không cộng dồn với quãng của model.
_VOICE_THRESHOLD = 200       # |mẫu| > ngưỡng này là có tiếng (~ -44 dBFS)
_FADE_SAMPLES = OUTPUT_SAMPLE_RATE * 6 // 1000   # 6 ms vào/ra, chống "tách" ở chỗ nối
_INF = float("inf")
_FINAL_TAIL_KEEP_S = 0.08    # lặng cuối của câu CUỐI giữ lại tối đa chừng này

# kind -> (lặng cuối câu trước giữ tối đa, lặng đầu câu sau giữ tối đa); quãng nghỉ chèn riêng ở `_gap_pause`.
_GAP_TRIM = {
    "end": (_INF, _INF),
    "clause": (0.04, 0.03),
    "line": (0.06, 0.04),
    "soft": (0.03, 0.02),
}
_FIRST_LEAD_KEEP_S = 0.03    # lặng đầu của câu ĐẦU: bớt luôn cho tiếng đầu tới sớm


def _gap_kind(prev: str, nxt: str) -> str:
    """Loại chỗ nối giữa câu `prev` và câu `nxt` (xem bảng ở trên)."""
    last = prev.rstrip()[-1:]
    if last and last in ".!?…":
        return "end"
    if last and last in ",;:":
        return "clause"
    first = next((c for c in nxt if c.isalpha()), "")
    if first and first.islower():
        return "soft"
    return "line"


def _gap_pause(kind: str) -> float:
    return {
        "end": config.SPEECH_SENTENCE_PAUSE_SECONDS,
        "clause": config.SPEECH_CLAUSE_PAUSE_SECONDS,
        "line": config.SPEECH_LINE_PAUSE_SECONDS,
        "soft": config.SPEECH_SOFT_PAUSE_SECONDS,
    }[kind]


def _samples(seconds: float) -> int:
    return int(seconds * OUTPUT_SAMPLE_RATE) if seconds != _INF else 1 << 60


def _zeros(n: int) -> bytes:
    return b"\x00\x00" * max(0, n)


class _Joiner:
    """Ghép PCM nhiều câu thành một luồng liền: cắt lặng thừa, vuốt hai đầu mỗi câu, chèn quãng nghỉ theo loại chỗ nối.

    Chạy streaming: phần có tiếng của mỗi chunk ra ngay; chỉ giữ lại ~6 ms cuối (để vuốt nhỏ) và quãng
    lặng cuối câu (chưa biết nên giữ bao nhiêu cho tới khi biết câu sau). Lặng đó chỉ là lặng nên chậm phát
    nó không làm trễ tiếng.
    """

    def __init__(self) -> None:
        self._tail_silence = 0       # mẫu lặng cuối câu trước, chưa phát
        self._started = False        # đã phát chữ nào chưa

    @staticmethod
    def _voiced(arr: np.ndarray) -> np.ndarray:
        return np.flatnonzero(np.abs(arr.astype(np.int32)) > _VOICE_THRESHOLD)

    def sentence(self, chunks: Iterable[bytes], kind: str | None) -> Iterator[bytes]:
        """PCM của một câu. `kind` None = câu đầu của luồng."""
        if kind is None:
            trail_keep, lead_keep, pause = 0, _samples(_FIRST_LEAD_KEEP_S), 0.0
        else:
            trail_s, lead_s = _GAP_TRIM[kind]
            trail_keep, lead_keep, pause = _samples(trail_s), _samples(lead_s), _gap_pause(kind)
        gap = min(self._tail_silence, trail_keep) + int(pause * OUTPUT_SAMPLE_RATE)
        self._tail_silence = 0
        if gap > 0 and self._started:
            yield _zeros(gap)

        lead = 0              # mẫu lặng đầu đã gặp (chưa gặp tiếng)
        seeking = True
        hold = np.empty(0, dtype=np.int16)
        for chunk in chunks:
            arr = np.frombuffer(chunk, dtype="<i2")
            if seeking:
                voiced = self._voiced(arr)
                if not len(voiced):
                    lead += len(arr)
                    continue
                first = int(voiced[0])
                keep = min(lead + first, lead_keep)
                if keep > 0:
                    yield _zeros(keep)
                seeking = False
                arr = arr[first:].copy()
                fade = min(_FADE_SAMPLES, len(arr))
                arr[:fade] = (arr[:fade].astype(np.float32) * np.linspace(0.0, 1.0, fade, endpoint=False)).astype(np.int16)
            hold = np.concatenate((hold, arr)) if len(hold) else arr
            voiced = self._voiced(hold)
            if not len(voiced):
                continue
            boundary = max(0, int(voiced[-1]) + 1 - _FADE_SAMPLES)
            if boundary:
                self._started = True
                yield hold[:boundary].astype("<i2").tobytes()
                hold = hold[boundary:]

        if seeking:
            self._tail_silence = lead   # câu không có tiếng nào: coi cả câu là lặng
            return
        voiced = self._voiced(hold)
        end = int(voiced[-1]) + 1 if len(voiced) else 0
        body = hold[:end].astype(np.float32)
        fade = min(_FADE_SAMPLES, len(body))
        if fade:
            body[len(body) - fade:] *= np.linspace(1.0, 0.0, fade, endpoint=False)
        self._tail_silence = len(hold) - end
        if len(body):
            self._started = True
            yield body.astype("<i2").tobytes()

    def finish(self) -> Iterator[bytes]:
        """Lặng cuối câu cuối cùng: giữ một ít cho kính có chỗ kết, bỏ phần dư."""
        keep = min(self._tail_silence, _samples(_FINAL_TAIL_KEEP_S))
        self._tail_silence = 0
        if keep > 0 and self._started:
            yield _zeros(keep)


def synthesize_text_stream(text_chunks: Iterable[str]) -> Iterator[bytes]:
    """Mảnh text (từ Gemini) -> mảnh PCM, không đợi text đầy đủ.

    Tổng hợp chạy ở thread nền và luôn đi trước người nghe. Làm tuần tự thì
    giữa hai câu có khoảng lặng đúng bằng thời gian phonemize + prefill của câu
    sau (đo được 0.91s) — nghe rõ là ngắt quãng. Chạy nền thì câu sau đã sẵn
    sàng trước khi câu trước phát hết, khoảng lặng biến mất.

    An toàn về thread: chỉ thread nền gọi model, luồng chính chỉ lấy từ hàng
    đợi — không có hai thread nào cùng đụng vào ONNX session.
    """
    queue: Queue = Queue(maxsize=_PIPELINE_QUEUE_CHUNKS)
    done = object()
    failure: list[BaseException] = []
    stop = threading.Event()
    smooth = config.SPEECH_SMOOTH_JOINS

    def put(pcm: bytes) -> bool:
        while not stop.is_set():
            try:
                queue.put(pcm, timeout=0.1)
                return True
            except Full:
                continue
        return False

    def worker() -> None:
        try:
            joiner = _Joiner() if smooth else None
            previous = None
            for index, sentence in enumerate(_sentences(text_chunks)):
                device_ctx.announce(sentence)  # khung debug `say`: chỗ DUY NHẤT text vào TTS (mọi đường WS)
                if joiner is None:
                    pieces = _spoken_pcm(sentence, first=index == 0)
                else:
                    kind = None if previous is None else _gap_kind(previous, sentence)
                    pieces = joiner.sentence(_sentence_pcm(sentence), kind)
                previous = sentence
                for pcm in pieces:
                    if not put(pcm):
                        return
            if joiner is not None:
                for pcm in joiner.finish():
                    if not put(pcm):
                        return
        except BaseException as exc:  # noqa: BLE001 - chuyển nguyên vẹn sang consumer
            failure.append(exc)
        finally:
            queue.put(done)

    thread = device_ctx.run_in_thread(worker, name="tts-pipeline")

    try:
        while True:
            item = queue.get()
            if item is done:
                break
            yield item
    finally:
        # Consumer bỏ đi giữa chừng (client ngắt kết nối): báo thread nền dừng
        # thay vì để nó tổng hợp nốt cả bài rồi mới chết.
        stop.set()

    if failure:
        raise failure[0]


def encode_adpcm(pcm_stream: Iterator[bytes]) -> Iterator[bytes]:
    """PCM 16-bit/16kHz mono -> khối IMA ADPCM 256 byte, định dạng board ưu tiên.

    1/4 dung lượng PCM trần mà board giải mã thẳng bằng phần cứng sẵn có, không
    cần decoder MP3. Chi phí nén đo được ~114 lần thời gian thực, tức khoảng 9 ms
    cho mỗi giây audio — không đáng kể so với chính TTS (~2,5 lần thời gian
    thực), nên đổi sang đây không làm người dùng chờ thêm.

    Mảnh PCM vào không cần trùng biên khối: `adpcm.Encoder` giữ phần dư lại và
    ghép vào mảnh sau.
    """
    encoder = adpcm.Encoder()
    for pcm in pcm_stream:
        blocks = encoder.encode(pcm)
        if blocks:
            yield blocks

    # Khối cuối chưa đủ 505 mẫu vẫn phải ra, nếu không câu bị cụt tới 31 ms.
    tail = encoder.flush()
    if tail:
        yield tail


def encode_mp3(pcm_stream: Iterator[bytes]) -> Iterator[bytes]:
    """PCM 16-bit/16kHz mono -> mảnh MP3 CBR, để giảm băng thông gửi MCU.

    32 kbps thay vì PCM trần 256 kbps (32 KB/s) — 1/8 dung lượng, chấp nhận
    được cho thoại. `encoder.encode()` chỉ trả bytes khi tích luỹ đủ một
    frame MPEG, nên nhiều lần gọi liên tiếp có thể ra rỗng; bỏ qua thay vì
    yield mảnh rỗng.

    `lameenc` trả `bytearray`, không phải `bytes` — ép kiểu trước khi yield vì
    Starlette's `StreamingResponse` chỉ chấp nhận `bytes`/`memoryview`.
    """
    import lameenc

    encoder = lameenc.Encoder()
    encoder.set_in_sample_rate(OUTPUT_SAMPLE_RATE)
    encoder.set_channels(1)
    encoder.set_bit_rate(MP3_BITRATE_KBPS)

    for pcm in pcm_stream:
        mp3 = encoder.encode(pcm)
        if mp3:
            yield bytes(mp3)

    tail = encoder.flush()
    if tail:
        yield bytes(tail)
