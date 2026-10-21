import asyncio
import logging
from collections.abc import AsyncIterator, Iterator

import config
from models import llm, vlm
from models.llm.client import prefix_prob

logger = logging.getLogger("blind_assist")

_PROMPT = (
    "Bạn đang hỗ trợ người khiếm thị nhận biết mệnh giá tiền mặt Việt Nam Đồng (VND) qua camera.\n"
    "Nhìn vào ảnh và trả lời ĐÚNG TRỌNG TÂM mệnh giá tiền mặt đọc được.\n\n"
    "QUY TẮC NGUYÊN TẮC:\n"
    "- Mở đầu ngay bằng mệnh giá, KHÔNG dùng các từ dẫn dắt hoặc từ thừa như: "
    "\"Đây là\", \"Ảnh chụp\", \"Trước mặt bạn là\", \"Trong hình có\", \"Tôi thấy\".\n"
    "- Nếu có 1 tờ: đọc trực tiếp mệnh giá (ví dụ: \"50 nghìn đồng\", \"500 nghìn đồng\", \"10 nghìn đồng\").\n"
    "- Nếu có nhiều tờ: liệt kê ngắn gọn từng mệnh giá (ví dụ: \"10 nghìn đồng và 50 nghìn đồng\").\n"
    "- KHÔNG mô tả màu sắc (người nghe không cảm nhận được màu).\n"
    "- Nếu ảnh quá mờ, bị che khuất, tối đen hoặc không thấy rõ tờ tiền nào, PHẢI nói thẳng: "
    "\"Không thấy rõ tiền, vui lòng chụp lại.\"\n"
    "- TUYỆT ĐỐI không đoán bừa khi không chắc chắn."
)


def handle(image: bytes, command_text: str) -> Iterator[str]:
    return vlm.generate_stream(_PROMPT, image=image)



# ── Chọn nhãn có xác suất (2026-10-21) ────────────────────────────────────────
# Đo trên vnd_kaggle: làn B (prompt tự do) đọc sai 10,9-11,2 %, gần như mọi ca sai là sai số chữ
# số 0 (5.000 <-> 50.000, 500.000 -> 50.000). Bản JSON "chép số + chữ rồi code đối chiếu" thua cả
# hai trục (trễ x3, model chép luôn dãy số khớp điều nó đoán). Ở đây Gemma CHỌN một nhãn trong
# tập cố định (structured choice, ~4 token, nhanh như cũ) và trả xác suất của nhãn; dưới ngưỡng
# MONEY_MIN_PROB thì từ chối. Lưới cấu hình + ngưỡng chọn trên dev: evaluations/bench/money_vnd.py --path choice.
DENOMS = (200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000)
UNSURE_LABEL, NO_MONEY_LABEL = "không rõ", "không có tiền"


def speak(value: int) -> str:
    return f"{value // 1000} nghìn đồng" if value >= 1000 else f"{value} đồng"


LABELS = {speak(d): d for d in DENOMS}
CHOICES = [*LABELS, UNSURE_LABEL, NO_MONEY_LABEL]

CHOICE_QUESTION = "Đây là tờ tiền bao nhiêu?"


def choice_messages(image: bytes) -> list[dict]:
    """Cấu hình thắng trên dev: system prompt làn A + prompt đọc tiền + câu hỏi, rồi chọn nhãn.
    (Bỏ system prompt: sai nhiều hơn; dặn "đếm kỹ chữ số 0": sai 43 % — đo 2026-10-21.)"""
    from lanes import general  # nhập muộn: lanes.general nhập module này
    text = f'{_PROMPT}\n\nNgười dùng nói: "{CHOICE_QUESTION}"\nTrả đúng một nhãn.'
    return [{"role": "system", "content": general._VISION_SYSTEM}, llm.user_message(text, image)]

NO_MONEY = "Không thấy tờ tiền nào. Bạn đưa tờ tiền ra trước kính, cách khoảng một gang tay."
UNSURE = "Chưa đọc rõ mệnh giá. Bạn lật mặt có số to về phía kính và giữ yên tay."


async def choose_raw(messages: list[dict], top_logprobs: int = 1) -> tuple[str, dict]:
    """(nhãn, choice gốc của server) — một lượt Gemma structured choice có logprobs."""
    choice = await llm.complete_raw(
        messages, max_tokens=12, temperature=0.0,
        logprobs=True, top_logprobs=top_logprobs, structured_outputs={"choice": CHOICES},
    )
    text = (choice["message"].get("content") or "").strip()
    if text not in CHOICES:
        raise llm.LLMError(f"Gemma không trả đúng nhãn: {text[:40]!r}")
    return text, choice


async def choose(messages: list[dict]) -> tuple[str, float | None]:
    """(nhãn, xác suất nhãn THÔ của server). Đã thử chuẩn hoá lại trên 13 nhãn hợp lệ (dev 2026-10-21):
    1 khung nói sai gấp đôi (3,6 -> 7,3 %) vì nhãn sai cũng "chắc" lên, 3 khung không lợi — giữ xác suất thô."""
    text, choice = await choose_raw(messages)
    return text, prefix_prob(choice, len(text))


def say(label: str, prob: float | None, min_prob: float) -> str:
    """Câu nói cho nhãn: mệnh giá chỉ khi xác suất >= ngưỡng (không có xác suất = không chắc)."""
    return combine([(label, prob)], min_prob)


def combine(reads: list[tuple[str, float | None]], min_prob: float) -> str:
    """Gộp nhãn của nhiều khung (khung đầu = khung chính): nói mệnh giá khi có >= 1 khung chắc
    (xác suất >= ngưỡng) và KHÔNG khung nào chắc một mệnh giá khác. Đo dev 2026-10-21 (3 khung,
    ngưỡng 0,98): đúng 59 -> 75 %, sai <= 3 %; trung bình xác suất hay đòi đa số thì từ chối quá nhiều."""
    sure = {label for label, prob in reads if label in LABELS and prob is not None and prob >= min_prob}
    if len(sure) == 1:
        return sure.pop()
    if reads and reads[0][0] == NO_MONEY_LABEL and not sure:
        return NO_MONEY
    return UNSURE


async def read_choice(images: list[bytes]) -> AsyncIterator[str]:
    """Một lượt Gemma cho MỖI khung, chạy song song; 1 khung dùng ngưỡng MONEY_MIN_PROB, nhiều khung
    dùng MONEY_MULTI_MIN_PROB (thấp hơn vì đã có các khung khác chặn mâu thuẫn)."""
    results = await asyncio.gather(*(choose(choice_messages(img)) for img in images), return_exceptions=True)
    reads = [r for r in results if not isinstance(r, BaseException)]
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("đọc tiền: lỗi %r", r)
    logger.info("đọc tiền: %s", reads)
    yield combine(reads, config.MONEY_MIN_PROB if len(images) == 1 else config.MONEY_MULTI_MIN_PROB)

