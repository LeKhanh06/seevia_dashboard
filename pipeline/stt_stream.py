"""Pseudo-streaming STT (§7.1) trên Zipformer offline của `pipeline/stt.py`.

Zipformer giải ~0,03 s/câu nên cứ giải lại TOÀN BỘ buffer mỗi 300 ms trong lúc
audio còn đang tới — không cần model streaming riêng. Bản ghi cuối chỉ còn là
một lượt giải nữa sau khi nhả nút, và bỏ hẳn được nếu từ lần giải trước tới giờ
không có mẫu mới nào.

Khối ADPCM tự mang predictor/step index riêng nên giải từng khối ngay khi đủ
256 byte là đúng y như giải cả file một lần.
"""

import asyncio
import concurrent.futures
import logging
import struct
import threading

import numpy as np

import config
from pipeline import adpcm, stt, stt_dual

logger = logging.getLogger("blind_assist")

_FORMAT_PCM = 0x0001
# Tham số audio do client khai (Content-Type / header RIFF) chỉ được nằm trong khoảng hợp lý: trần audio và
# trần byte tính theo rate x channels, nên con số tuỳ ý = vô hiệu hoá trần.
_MIN_RATE, _MAX_RATE = 8000, 48000
_MIN_CHANNELS, _MAX_CHANNELS = 1, 2


def _sane_rate(value) -> int:
    try:
        rate = int(value)
    except (TypeError, ValueError):
        return adpcm.SAMPLE_RATE
    return rate if _MIN_RATE <= rate <= _MAX_RATE else adpcm.SAMPLE_RATE


def _sane_block(value) -> int:
    try:
        block = int(value)
    except (TypeError, ValueError):
        return adpcm.BLOCK_ALIGN
    return block if 0 < block <= 8192 else adpcm.BLOCK_ALIGN


def _content_params(content_type: str | None) -> dict[str, str]:
    params = {}
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.partition("=")
        params[key.strip().lower()] = value.strip()
    return params


class StreamingTranscriber:
    """Nhận body audio theo mảnh, cho partial/final transcript bất kỳ lúc nào.

    `feed()` gọi từ event loop, `partial()`/`final()` gọi từ thread worker: mọi
    trạng thái dùng chung đi qua `_lock`. Recognizer chỉ đụng tới trong
    `_decode()`, mà mỗi lượt `/v2/turn` chỉ có một lượt giải chạy cùng lúc.
    """

    def __init__(self, content_type: str | None = None, vad=None, hotwords=None, dual: bool | None = None,
                 device_state: dict | None = None) -> None:
        # `vad`: pipeline.vad.Segmenter (hoặc None = giải cả buffer như trước).
        self._vad = vad
        # Hai nhánh STT song song + chấm chốt (pipeline/stt_dual.py); None = theo config.STT_DUAL.
        self._dual = config.STT_DUAL if dual is None else dual
        self._device_state = device_state
        self._h1: stt_dual.Hyp | None = None  # kết quả thô của từng nhánh cho khoá `_decoded_at`
        self._h2: stt_dual.Hyp | None = None
        self._decoded2_at = -1  # khoá của lần giải nhánh 2 gần nhất
        self._b2: tuple | None = None  # (khoá, Future) nhánh 2 đang/đã chạy cho khoá đó
        self.b2_late = 0  # số lần nhánh 2 quá hạn chờ
        self._merged: tuple | None = None  # (khoá, văn bản đã chấm chốt)
        self._pre: tuple | None = None  # ((chữ nhánh 1, chữ nhánh 2), task chấm trước trên partial)
        self.prefetch_hit = False
        self._final_key = None
        self.decision: stt_dual.Decision | None = None
        # Tên người thân ưu tiên cho lượt này; mặc định lấy danh sách đã đặt bằng `stt.set_user_hotwords`.
        self._hotwords = tuple(hotwords) if hotwords is not None else stt.user_hotwords()
        params = _content_params(content_type)
        self._block_align = _sane_block(params.get("block"))
        self._rate = _sane_rate(params.get("rate"))
        self._format = adpcm.FORMAT_TAG
        self._channels = 1
        # Còn đang dò header RIFF thì `_header_done` False; ADPCM thô thì xong luôn.
        self._header_done = False
        self._pending = b""
        self._chunks: list[np.ndarray] = []
        self._n_samples = 0
        self._lock = threading.Lock()
        self._decode_lock = threading.Lock()
        self._decoded_at = -1  # khoá của lần giải gần nhất (số mẫu, hoặc đoạn tiếng khi có VAD)
        self._text = ""
        self.bytes_in = 0
        self.capped = False

    # ── nhận audio ──────────────────────────────────────────────────────

    def _parse_header(self) -> bool:
        """Dò header WAV ở đầu luồng. True khi đã biết chỗ bắt đầu dữ liệu."""
        data = self._pending
        if len(data) < 4:
            return False
        if data[:4] != b"RIFF":
            self._header_done = True  # ADPCM thô, tham số lấy từ Content-Type
            return True
        if len(data) < 12:
            return False
        pos = 12
        while pos + 8 <= len(data):
            chunk_id = data[pos:pos + 4]
            size = struct.unpack_from("<I", data, pos + 4)[0]
            if chunk_id == b"data":
                self._pending = data[pos + 8:]
                self._header_done = True
                return True
            if pos + 8 + size > len(data):
                return False  # chunk fmt/fact chưa tới đủ
            body = data[pos + 8:pos + 8 + size]
            if chunk_id == b"fmt ":
                tag, channels, rate, _, block_align = struct.unpack_from("<HHIIH", body)
                self._format = tag
                # Header do client khai: kẹp vào khoảng hợp lý, không thì trần audio (tính theo rate/channels)
                # phồng theo con số tuỳ ý (rate=1e9 -> ~60 GB).
                self._channels = channels if _MIN_CHANNELS <= channels <= _MAX_CHANNELS else 1
                self._rate = _sane_rate(rate)
                self._block_align = _sane_block(block_align)
            pos += 8 + size + (size & 1)
        return False

    def feed(self, data: bytes) -> None:
        if not data or self.capped:
            return
        # Trần audio (Task 9): quá DEVICE_MAX_AUDIO_S thì ngừng nhận, lượt kết
        # thúc với phần đã có — chặn buffer phình vô hạn từ kẻ giữ nút mãi.
        # Chặn thêm theo BYTE: header RIFF không bao giờ hoàn tất thì _n_samples
        # đứng ở 0 và _pending phình mãi. PCM 16-bit là dạng tốn byte nhất.
        byte_cap = config.DEVICE_MAX_AUDIO_S * (self._rate or 1) * 2 * max(1, self._channels) + 65536
        if self._n_samples >= config.DEVICE_MAX_AUDIO_S * (self._rate or 1) or self.bytes_in > byte_cap:
            self.capped = True
            logger.warning("audio vượt %.0fs: bỏ phần còn lại", config.DEVICE_MAX_AUDIO_S)
            return
        self.bytes_in += len(data)
        with self._lock:
            self._pending += data
            if not self._header_done and not self._parse_header():
                return
            if self._format == _FORMAT_PCM:
                frame = 2 * self._channels
                usable = len(self._pending) // frame * frame
                raw = np.frombuffer(self._pending[:usable], dtype="<i2")
            else:
                usable = len(self._pending) // self._block_align * self._block_align
                raw = adpcm.decode_blocks(self._pending[:usable], self._block_align)
            self._pending = self._pending[usable:]
            if raw.size == 0:
                return
            samples = raw.astype(np.float32) / 32768.0
            if self._channels > 1:
                samples = samples.reshape(-1, self._channels).mean(axis=1)
            self._chunks.append(samples)
            self._n_samples += samples.size
            if self._vad is not None:
                self._vad.feed(samples if self._rate == 16000 else stt._resample(samples, self._rate))

    @property
    def seconds(self) -> float:
        return self._n_samples / float(self._rate or 1)

    def _key(self):
        """Khoá "nội dung cần giải": số mẫu, hoặc (đầu, cuối) đoạn tiếng khi có VAD.

        Có VAD thì im lặng tới thêm không đổi khoá -> không giải lại; không có
        tiếng nào -> None (không có gì để giải).
        """
        if self._vad is None:
            return self._n_samples
        return self._vad.span()

    def has_new_audio(self) -> bool:
        key = self._key()
        return key is not None and key != self._decoded_at

    @property
    def speech_ended(self) -> bool:
        """VAD báo hết câu (im lặng >= VAD_END_SILENCE_S sau tiếng) trong lúc nút còn giữ."""
        return self._vad is not None and self._vad.speech_ended_at is not None

    @property
    def has_speech(self) -> bool:
        return self._vad is None or self._vad.speech_started

    @property
    def decoded_key(self):
        return self._decoded_at

    def span_key(self):
        with self._lock:
            return self._key()

    # ── giải mã ─────────────────────────────────────────────────────────

    def _snapshot(self):
        with self._lock:
            if len(self._chunks) > 1:
                self._chunks = [np.concatenate(self._chunks)]
            samples = self._chunks[0] if self._chunks else np.zeros(0, np.float32)
            return samples, self._n_samples, self._key()

    def _decode(self, want2: bool = False) -> str:
        """Giải (các) nhánh cho đoạn audio hiện có. Trả bản ghi nhánh 1 (partial/đoán sớm dùng nhánh 1).

        `want2` (chỉ có nghĩa khi `_dual`): giải cả nhánh 2 cho cùng đoạn, SONG SONG với nhánh 1.
        """
        with self._decode_lock:
            samples, count, key = self._snapshot()
            if self._vad is not None and key is None:
                return ""  # cả lượt không có tiếng: khỏi giải
            want2 = want2 and self._dual
            need1 = key != self._decoded_at
            need2 = want2 and key != self._decoded2_at
            if not need1 and not need2:
                return self._text  # không có tiếng/mẫu mới: kết quả cũ vẫn đúng
            if count == 0:
                self._decoded_at, self._text = 0, ""
                self._h1 = self._h2 = None
                return ""
            samples = stt._resample(samples, self._rate)
            if self._vad is not None:
                samples = samples[key[0]:key[1]]  # chỉ giải đoạn có tiếng
            if self._dual:
                if need2 and (self._b2 is None or self._b2[0] != key):
                    self._b2 = (key, stt_dual.submit_branch2(samples, self._hotwords))  # chạy song song nhánh 1
                if need1:
                    self._h1 = stt_dual.decode_branch1(samples, self._hotwords)
                    self._decoded_at, self._text = key, stt._normalize(self._h1.text)
                if need2:
                    # Nhánh 2 chậm hơn nhánh 1 quá `STT_DUAL_B2_WAIT_MS` thì bỏ phiếu trắng (dùng nhánh 1) thay vì
                    # kéo dài bản ghi cuối; nó vẫn chạy tiếp và lần hỏi sau (cùng đoạn) lấy được nếu đã xong.
                    # Không giải nhánh 1 trong lượt này (partial=0): chưa có gì chạy trước, đợi lâu hơn.
                    wait = config.STT_DUAL_B2_WAIT_MS / 1000 if need1 else 2.0
                    try:
                        self._h2 = self._b2[1].result(timeout=wait)
                        self._decoded2_at = key
                    except concurrent.futures.TimeoutError:
                        self.b2_late += 1
                    except Exception:  # noqa: BLE001 - nhánh 2 hỏng giữa chừng: lượt này chỉ còn nhánh 1
                        logger.exception("STT dual: nhánh 2 lỗi -> dùng nhánh 1")
                        self._h2, self._decoded2_at, self._dual = None, -1, False
            else:
                self._decoded_at, self._text = key, stt._normalize(stt.decode_samples(samples, self._hotwords))
            return self._text

    def partial(self) -> str:
        return self._decode(config.STT_DUAL_PARTIAL)

    async def partial_async(self) -> str:
        """`partial()` + chấm trước: có mâu thuẫn thì gọi Gemma NGAY trên partial, để bản ghi cuối (thường trùng
        partial cuối) đã có sẵn đáp án, khỏi chờ thêm một lượt Gemma sau khi nhả nút."""
        text = await asyncio.to_thread(self.partial)
        if self._dual and config.STT_ADJUDICATE == "llm" and config.STT_ADJUDICATE_PREFETCH:
            self._prefetch()
        return text

    def _prefetch(self) -> None:
        h1, h2 = self._h1, self._h2
        if h1 is None or h2 is None or self._decoded2_at != self._decoded_at:
            return
        pair = (h1.text, h2.text)
        if self._pre is not None and self._pre[0] == pair:
            return
        if stt_dual.conflicts(stt_dual.align(h1.words, h2.words)) == 0:
            return
        if self._pre is not None and not self._pre[1].done():
            self._pre[1].cancel()
        self._pre = (pair, asyncio.get_running_loop().create_task(
            stt_dual.decide(h1, h2, self._device_state)))

    def cancel_prefetch(self) -> None:
        if self._pre is not None and not self._pre[1].done():
            self._pre[1].cancel()

    def final(self) -> str:
        """Bản ghi cuối (nhánh 1). Rẻ (0 ms) nếu partial gần nhất đã thấy hết audio."""
        text = self._decode()
        self._final_key = self._decoded_at
        return text

    @property
    def final_key(self):
        """Khoá của audio mà bản ghi cuối vừa trả (khác `decoded_key` nếu partial chen vào lúc đang chấm)."""
        return self._final_key if self._final_key is not None else self._decoded_at

    async def final_async(self) -> str:
        """Bản ghi cuối: hai nhánh -> căn chỉnh -> (Gemma chấm nếu mâu thuẫn). Không dual = như `final()`."""
        if not self._dual:
            return await asyncio.to_thread(self.final)
        text1 = await asyncio.to_thread(self._decode, True)
        key = self._decoded_at
        self._final_key = key
        if self._merged is not None and self._merged[0] == key:
            return self._merged[1]
        h1, h2 = self._h1, self._h2
        if h1 is None or self._decoded2_at != key:
            return text1  # không có gì để chấm (không tiếng / nhánh 2 lỗi)
        try:
            decision = None
            if self._pre is not None and self._pre[0] == (h1.text, h2.text):
                try:
                    decision = await self._pre[1]
                    self.prefetch_hit = True
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    decision = None  # task chấm trước bị huỷ (partial mới hơn chen vào): chấm lại
            if decision is None:
                decision = await stt_dual.decide(h1, h2, self._device_state)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - chấm hỏng thì giữ nhánh 1, không làm hỏng lượt
            logger.exception("STT dual: hợp hai nhánh lỗi -> dùng nhánh 1")
            return text1
        self.decision = decision
        text = stt._normalize(decision.text) if decision.text.strip() else text1
        if decision.source != "same":
            logger.info("STT dual: b1='%s' b2='%s' -> '%s' (%s, %d mâu thuẫn, Gemma %.0f ms)",
                        h1.text, h2.text if h2 else "", text, decision.source, decision.conflicts, decision.llm_ms)
        self._merged = (key, text)
        return text

    def dual_info(self) -> dict | None:
        d = self.decision
        if not self._dual or d is None:
            return None
        return {"source": d.source, "conflicts": d.conflicts, "cands": d.n_cands, "prob": d.prob,
                "llm_ms": round(d.llm_ms), "b2_ms": round(self._h2.ms) if self._h2 else None,
                "prefetch": self.prefetch_hit, "b2_late": self.b2_late}
