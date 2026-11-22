# LLM Provider

Hai model đang dùng: **Gemma** (đọc chữ + ảnh, sinh JSON) và **Clef** (phân loại nhanh trước khi gọi model lớn). Cấu hình trong `.env` ở root repo (đã gitignore). Không hardcode key trong source, test hay tài liệu.

## Gemma 4 26B-A4B

Tương thích OpenAI, nhận ảnh, dùng client SDK `openai`, chỉ đổi `base_url`. Hai đường kết nối, cùng model `gemma-4-26b`:

| Đường | Base URL | Ghi chú |
|---|---|---|
| Host LAN | `http://192.168.20.150:8899/v1` 
| UIT API | `https://llm.uit.edu.vn/gemma/v1`

Header xác thực: `Authorization: Bearer <LLM_API_KEY>`.

- UIT: tối đa 32.768 token mỗi request (prompt + completion), 20 phút mỗi request, **40 request đồng thời mỗi key** (vượt → HTTP 429).
- Thinking (UIT): `extra_body={"chat_template_kwargs": {"enable_thinking": True}}`. Phần suy nghĩ nằm trong `content` trước câu trả lời, nên lấy khối JSON cuối cùng rồi mới validate.
- Gặp 429 hoặc lỗi mạng thì chờ rồi thử lại; không tắt retry của client.

## Clef

Cloudflare Workers AI, model `@cf/cloudflare/clef-flash`. Phân loại lượt gõ trước khi đến model lớn. Timeout 1 giây (đo p50 0,55 s, chậm nhất 1,3 s); quá hạn hoặc thiếu key thì Clef không quyết định gì.

### Cách gọi Clef (schema đầy đủ)

Clef **không sinh văn bản**: nhận một `state` kèm tối đa 64 câu hỏi có kiểu, trả xác suất cho từng câu trong một lượt (`output_tokens` luôn 0). JSON Schema gốc của Cloudflare lưu ở `data/clef/clef-flash-input.schema.json` và `data/clef/clef-flash-output.schema.json`; nguồn: <https://developers.cloudflare.com/workers-ai/models/clef-flash/>.

`POST https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/run/@cf/cloudflare/clef-flash`, header `Authorization: Bearer $CLOUDFLARE_API_TOKEN`.

**Request**

| Trường | Bắt buộc | Mô tả |
|---|---|---|
| `model` | có | Chỉ `"clef-flash"` hoặc `"clef"` (không dùng `@cf/...`, sẽ bị 400) |
| `state` | có | Chuỗi, hoặc object/array (log chat, bản ghi...). Văn bản dài bị cắt cho vừa context |
| `questions` | có | Object 1–64 câu; key là id (chữ, số, `_`, `.`, `-`, tối đa 100 ký tự), kết quả trả về đúng id đó |
| `images` | không | Tối đa 4 ảnh PNG/JPEG/WebP: chuỗi data-URL hoặc `{"content_type", "base64"}`. Không nhận URL từ xa. Mỗi ảnh ≤ 4 MiB và 16 MP, tổng ≤ 8 MiB, cả body ≤ 13 MiB |

Ba kiểu câu hỏi (đều cần `type` và `instructions`; `instructions` là chuỗi, hoặc object/array chứa câu hỏi ở một field và dữ liệu tham chiếu ở field khác):

| `type` | `criteria` | Trả về |
|---|---|---|
| `noul` (có/không) | tuỳ chọn: `{"true": ..., "false": ...}` mô tả ý nghĩa có/không | `noul`: xác suất "có" (0–1) |
| `choice` | bắt buộc: map `option_id → mô tả` (chuỗi/object/array/null), 2–255 option | `choice` (option cao nhất), `probabilities`, `confidence` |
| `score` | bắt buộc: mảng mô tả mức, thấp → cao, đánh số từ 0, 2–10 mức | `score` (trung bình theo xác suất, có thể nằm giữa các mức), `legend`, `probabilities`, `confidence` |

**Response** (REST bọc trong `result`; Workers binding trả thẳng phần bên trong)

```json
{
  "result": {
    "model": "clef-flash",
    "answers": {
      "eye":      { "type": "noul",   "noul": 0.9507 },
      "team":     { "type": "choice", "choice": "symptom",
                    "probabilities": { "symptom": 0.9782, "chitchat": 0.0091, "other": 0.0127 },
                    "confidence": 0.9357 },
      "severity": { "type": "score",  "score": 1.9054,
                    "legend": { "0": "Không", "1": "Nhẹ", "2": "Vừa", "3": "Nghiêm trọng" },
                    "probabilities": { "0": 0.1094, "1": 0.1423, "2": 0.4819, "3": 0.2664 },
                    "confidence": 0.1139 }
    },
    "usage": { "input_tokens": 353, "output_tokens": 0 }
  },
  "success": true, "errors": [], "messages": []
}
```

**Ví dụ curl** (đã chạy thật, HTTP 200)

```bash
curl -sS "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/run/@cf/cloudflare/clef-flash" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H 'Content-Type: application/json' -d '{
  "model": "clef-flash",
  "state": "tôi bị mờ mắt trái hai ngày, hơi đau",
  "questions": {
    "eye":      {"type": "noul",   "instructions": "Người dùng có đang mô tả triệu chứng về mắt không?"},
    "team":     {"type": "choice", "instructions": "Yêu cầu thuộc loại nào?",
                 "criteria": {"symptom": "mô tả triệu chứng", "chitchat": "trò chuyện xã giao", "other": "khác"}},
    "severity": {"type": "score",  "instructions": "Mức độ khẩn cấp?",
                 "criteria": ["Không", "Nhẹ", "Vừa", "Nghiêm trọng"]}
  }}'
```

Lưu ý:
- Context 24.576 token; giá khoảng 0,038 USD / triệu token đầu vào, token đầu ra không tính phí. Mỗi ảnh tối đa 1.024 token.
- `confidence` thấp nghĩa là xác suất dàn trải (ví dụ `severity` ở trên chỉ 0,11); đừng tin `score`/`choice` khi confidence thấp.
- Lỗi `required properties at '/' are 'model,state,questions'` xuất hiện cả khi sai cấu trúc `questions` (ví dụ mảng, hay thiếu `type`/`instructions`), không chỉ khi thiếu trường.

## Nhánh phụ (Gemini) — dự phòng và chọn theo mục đích

Gemma vẫn là nhánh chính. Nhánh phụ (OpenAI-compatible, `models/llm/client.py` `_Branch("alt")`) dùng cho:
- **Dự phòng:** nhánh được chọn hỏng TRƯỚC chữ đầu (mọi đường lỗi, 429/5xx, hết giờ kết nối) → thử nhánh kia, chỉ với
  mục đích trong `LLM_FALLBACK_PURPOSES` (mặc định `answer,ocr,extract,other`). Đã ra chữ rồi mà đứt → lỗi như cũ.
- **Ép theo mục đích:** `LLM_ROUTE="answer=alt,extract=alt"`. Mục đích: `answer classify extract money ocr stt_pick
  prewarm other` (đặt bằng `llm_usage.purpose(...)` ở chỗ gọi).

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LLM_ALT_BASE_URLS` | rỗng (= tắt) | Đường nhánh phụ, phẩy ngăn cách. 9router: `http://localhost:20128/v1` |
| `LLM_ALT_API_KEY` | rỗng | Khoá Bearer |
| `LLM_ALT_MODEL` | `ag/gemini-3.5-flash-lite` | Model nhánh phụ |
| `LLM_ALT_CONNECT_TIMEOUT_S` | `2.0` | Bắt tay TCP |
| `LLM_ALT_LOGPROBS` / `LLM_ALT_SCHEMA` | `0` / `0` | Đường có trả logprobs / tôn trọng `response_format` json_schema |
| `LLM_ROUTE` | rỗng | Ép mục đích sang `alt` |
| `LLM_FALLBACK` / `LLM_FALLBACK_PURPOSES` | `1` / `answer,ocr,extract,other` | Dự phòng |

Body gửi nhánh phụ bỏ `chat_template_kwargs`, `structured_outputs` (chỉ vLLM hiểu), bỏ `logprobs`/`response_format`
khi đường không hỗ trợ; giữ `stream_options`. `prewarm` chỉ gửi Gemma (`prewarm=alt` → bỏ hẳn). Mỗi nhánh thử ghi một
`llm_call` (`provider` theo URL, `model` của nhánh): một lần dự phòng = `uit/error` + `9router/ok`.

**Khi không có xác suất (9router):** `generate_choice` nhận nhãn dạng chữ, chuẩn hoá (nháy, dấu câu, hoa thường) và
bắt buộc nằm trong tập, xác suất `None`. `money=alt` **chỉ có hiệu lực khi `LLM_ALT_LOGPROBS=1`** (không xác suất thì
không chốt được mệnh giá, và nhãn "không có tiền" không xác suất sẽ nói nhầm "Không thấy tờ tiền nào"); khi bật, ngưỡng
`MONEY_MIN_PROB` (0,999, đo trên Gemma) phải đo lại cho Gemini (`evaluations/bench/money_vnd.py`). OCR qua nhánh không
logprobs: có chữ số là luôn kèm câu cảnh báo chữ số (`lanes/ocr_live.py`). Phân loại vẫn chịu
trần `INTENT_LLM_TIMEOUT_S` (0,8 s): quá giờ → Clef.

**Số đo 2026-11-22 (9router, câu ngắn):**

| Model | Chữ đầu p50 | Thinking | logprobs / JSON enum |
|---|---|---|---|
| Gemma UIT | 0,3–1 s | tắt | có / có |
| `ag/gemini-3.5-flash-lite` | **1,25 s** (1,22–1,28) | 0 | không / bỏ qua |
| `ag/gemini-3.8-flash` (low/medium/mặc định) | 4,9–5,4 s | 260–425 token, không tắt được (MINIMAL → 400) | không / bỏ qua |
| `cx/gpt-6.1-sol` | 1,6 s | 0 | không / bỏ qua |

`ag/gemini-3.5-flash` trả "no longer available"; `3.6/3.7-flash`, `4-carbon` 404. Qua 9router mỗi lời gọi bị cộng
~2.000 token prompt ẩn (Antigravity) — số token vào phồng so với AI Studio thật. 9router chỉ để thử nghiệm.

## Biến môi trường

| Biến | Dùng cho |
|---|---|
| `LLM_API_KEY` | Key của endpoint Gemma |
| `EXTRACTOR_BASE_URL`, `EXTRACTOR_MODEL` | Endpoint và model Gemma |
| `EXTRACTOR_PARALLEL` | Số call đồng thời (host LAN 38, UIT 36) |
| `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` | Truy cập Workers AI cho Clef |
| `CLEF_MODEL`, `CLEF_MODEL_ID`, `CLEF_TIMEOUT_S` | Tên, model id và timeout của Clef (mặc định `clef-flash`, `@cf/cloudflare/clef-flash`, 1 giây) |

## Chứng chỉ TLS (UIT)

`llm.uit.edu.vn` dùng root **Sectigo Public Server Authentication Root E46**; CA store cũ (Ubuntu 20.04) không có nên lỗi `unable to get local issuer certificate`. Không tắt verify, thêm bản cross-sign làm intermediate:

```bash
cat /etc/ssl/certs/ca-certificates.crt certs/sectigo-root-e46-cross-usertrust-ecc.pem > certs/uit-ca-bundle.pem
```

Rồi trỏ client vào bundle trong `.env`:

```bash
SSL_CERT_FILE=certs/uit-ca-bundle.pem
CURL_CA_BUNDLE=certs/uit-ca-bundle.pem
```
