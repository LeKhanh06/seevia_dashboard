# Nhánh Gemini song song trong VH — dự phòng tự động + chọn theo kiểu gọi (thiết kế ngắn, đã chốt 2026-11-22)

## Ý định
Gemma UIT (vLLM) là nhánh chính và giữ nguyên, nhưng hay chập chờn (429, lỗi đường). Thêm một nhánh LLM thứ hai
(Gemini) để: (1) Gemma lỗi thì lượt vẫn có câu trả lời; (2) thử từng kiểu gọi trên Gemini qua cấu hình và đo bằng
`llm_call`. Bước chuẩn bị cho lộ trình 9router → AI Studio → Vertex.

## Số đo làm căn cứ (9router, 2026-11-22)
| Model | Chữ đầu | Thinking | logprobs | JSON enum |
|---|---|---|---|---|
| Gemma UIT | 0,3–1 s | tắt | có | có |
| `ag/gemini-3.8-flash` (mọi mức) | 4,9–5,4 s | 260–425 token, không tắt được (MINIMAL → 400) | không | bỏ qua |
| **`ag/gemini-3.5-flash-lite`** | **1,25 s** (1,22–1,28) | 0 | không | bỏ qua |
| `ag/gemini-3.5-flash` | — | — | — | "no longer available" |

Người dùng chốt: dùng **3.5 Flash(-Lite)**, không dùng 3.8. Qua 9router mỗi lời gọi phồng ~2.000 token prompt ẩn.

## Mục tiêu / không làm / phản mục tiêu
- Mục tiêu: một nhánh phụ cấu hình được (URL, khoá, model); dự phòng tự động; ép từng mục đích sang nhánh phụ; mọi
  lần thử ghi `llm_call` riêng (lần Gemma hỏng = `error` ở `uit`, lần Gemini = `ok` ở `9router`), không đổi schema AB.
- Không làm: chạy ngầm cả hai để so (tốn hạn mức, người dùng không được gì); đổi prompt; tự chọn model theo độ trễ.
- Phản mục tiêu: request gửi Gemma đổi khi nhánh phụ tắt; đọc tiền nói mệnh giá khi không có xác suất; lượt chậm hơn
  khi Gemma vẫn chạy tốt.

## Cách làm (đã chọn: A + B)
- A. Chọn nhánh theo mục đích gọi: `LLM_ROUTE="answer=alt,extract=alt"` (mặc định rỗng = mọi thứ qua Gemma).
- B. Dự phòng: `LLM_FALLBACK=1` → nhánh được chọn hỏng (mọi đường lỗi, 429/5xx, hết giờ kết nối) **trước khi ra chữ
  nào** thì thử nhánh còn lại, chỉ cho mục đích trong `LLM_FALLBACK_PURPOSES` (mặc định `answer,ocr,extract,other`).
- Đã loại: C (chạy ngầm để so) — tốn hạn mức, không giúp người dùng.

## Thiết kế
- `config.py`: `LLM_ALT_BASE_URLS`, `LLM_ALT_API_KEY`, `LLM_ALT_MODEL` (mặc định `ag/gemini-3.5-flash-lite`),
  `LLM_ALT_LOGPROBS` (0; bật khi đường hỗ trợ, vd AI Studio), `LLM_ALT_SCHEMA` (0; gửi `response_format` khi đường
  tôn trọng), `LLM_ROUTE`, `LLM_FALLBACK` (1), `LLM_FALLBACK_PURPOSES`. Nhánh phụ trống URL = tắt hẳn (như hôm nay).
- `models/llm/client.py`: khái niệm nhánh (`primary` = Gemma dùng đúng `_order`/`_client`/body hiện có; `alt`).
  `stream_chat`, `complete_raw` (và `generate_choice`/`generate_json`/`complete` đi qua đó) chạy theo kế hoạch
  `[nhánh theo route] + [nhánh kia nếu dự phòng được]`. Stream đã ra chữ rồi mà đứt thì báo lỗi như cũ (không ghép hai model).
- Body gửi nhánh phụ: bỏ `chat_template_kwargs`, `structured_outputs`; bỏ `logprobs/top_logprobs` trừ khi
  `LLM_ALT_LOGPROBS`; bỏ `response_format` trừ khi `LLM_ALT_SCHEMA`; giữ `stream_options` khi stream.
- Không có `structured_outputs`: `generate_choice` trên nhánh phụ nhận nhãn dạng chữ, chuẩn hoá (bỏ dấu câu/nháy,
  chữ thường) rồi bắt buộc nằm trong tập; xác suất = `None`.
- An toàn khi không có xác suất (giữ hành vi hiện có): đọc tiền `prob=None` → từ chối (`read_money.combine`); phân
  loại `None` vẫn qua luồng hiện tại (và có trần 0,8 s → quá giờ rơi về Clef); STT chọn bản nghe không có top → theo
  nhãn đơn. `prewarm` chỉ gửi Gemma (KV cache của vLLM); route `prewarm=alt` thì bỏ qua.
- `llm_call`: `provider` theo URL của nhánh thực chạy, `model` = model của nhánh đó (Gemma: `LLM_MODEL`).
- Log một dòng khi chuyển nhánh: "LLM uit lỗi (…), chuyển sang 9router".

## Để người làm quyết
- Tên hàm/biến nội bộ; dạng chuẩn hoá nhãn; test cụ thể (giả lập httpx như `tests/test_llm_usage.py`).

## Việc sau (không trong đợt này)
- AB: thêm giá Gemini 3.5 Flash-Lite ($0,30 / $2,50 / cache $0,03) vào bảng giá + `LLM_COMPARE_MODELS`.
- Đường AI Studio có logprobs (`responseLogprobs`) cho đọc tiền/phân loại khi có khoá; đo bộ đo tiền trước khi bật.
- Dự phòng khi Gemma treo lâu chưa ra chữ (hiện chỉ dự phòng khi lỗi).
