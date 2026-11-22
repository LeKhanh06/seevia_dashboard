# Nhánh Gemini dự phòng + chọn theo kiểu gọi — kế hoạch

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thêm nhánh LLM phụ (mặc định `ag/gemini-3.5-flash-lite` qua 9router) vào `models/llm/client.py`: dự phòng khi Gemma hỏng và ép từng mục đích sang nhánh phụ, đo bằng `llm_call`.

**Architecture:** Một dataclass `_Branch` (tên, đường, khoá, model, client, cờ khả năng). `primary` bọc đúng `_order`/`_client`/`_body` hiện có; `alt` dựng từ `LLM_ALT_*`. `complete_raw` và `_stream_chat` chạy theo `_plan(purpose)` = nhánh theo route + nhánh kia nếu được dự phòng; mỗi lần thử ghi một `llm_call`.

**Tech Stack:** Python 3, httpx, pytest (giả lập `httpx.MockTransport` như `tests/test_llm_usage.py`).

**Spec:** `docs/superpowers/specs/2026-11-22-llm-du-phong-gemini-design.md`

## Global Constraints
- Nhánh phụ tắt (`LLM_ALT_BASE_URLS` rỗng) → request gửi Gemma và hành vi y như trước (test khoá tập khoá body hiện có vẫn pass).
- Mặc định: `LLM_ALT_MODEL=ag/gemini-3.5-flash-lite`, `LLM_ALT_LOGPROBS=0`, `LLM_ALT_SCHEMA=0`, `LLM_ROUTE=""`, `LLM_FALLBACK=1`, `LLM_FALLBACK_PURPOSES=answer,ocr,extract,other`.
- Stream đã ra chữ rồi mới đứt → `LLMError` như cũ, không chuyển nhánh.
- `prewarm` chỉ gửi Gemma; route `prewarm=alt` → không gọi gì.
- Không đổi schema `llm_call` (AB cấm khoá lạ).

## Review Focus
- Route sang alt nhưng alt chưa cấu hình → dùng Gemma, không lỗi (test ở Task 1).
- 429 từ Gemma ở `classify` (không nằm trong `LLM_FALLBACK_PURPOSES`) → không gọi alt, lỗi lên như cũ để intent rơi về Clef (Task 1).
- Alt trả nhãn kèm nháy/dấu chấm/hoa (`"OCR."`) → chuẩn hoá thành `ocr`; nhãn ngoài tập → `LLMError` (Task 1).
- Gemma hỏng cả hai lần thử stream → mỗi lần ghi `llm_call` riêng: `uit/error` rồi `9router/ok` với `model` của alt (Task 2).
- Alt cũng hỏng → lỗi cuối nêu cả hai nhánh, không treo (Task 2).

---

### Task 1: Cấu hình, nhánh, kế hoạch gọi, `complete_raw` + `generate_choice` trên alt

**Files:**
- Modify: `config.py` (cạnh khối `LLM_*`), `models/llm/client.py`
- Test: `tests/test_llm_branches.py` (mới)

**Interfaces:**
- Produces: `client._Branch` (`name: str`, `order: list[str]`, `model: str`, `key: str`, `get_client()`, `logprobs: bool`, `schema: bool`, `is_alt: bool`); `client._branches() -> tuple[_Branch, _Branch | None]`; `client._plan(purpose: str) -> list[_Branch]`; `client._body_for(branch, messages, max_tokens, stream, **extra) -> dict`; `client._normalize_label(text: str, choices: list[str]) -> str | None`.

- [ ] Test `test_alt_off_keeps_gemma_only`: không cấu hình alt → `_plan("answer") == [primary]`; route `answer=alt` vẫn `[primary]`.
- [ ] Test `test_plan_route_and_fallback`: alt bật, `LLM_ROUTE="answer=alt"` → `_plan("answer")` = `[alt, primary]`, `_plan("classify")` = `[primary]` (classify ngoài `LLM_FALLBACK_PURPOSES`), `_plan("ocr")` = `[primary, alt]`; `LLM_FALLBACK=0` → `_plan("ocr") == [primary]`.
- [ ] Test `test_alt_body_drops_vllm_keys`: `_body_for(alt, …, stream=False, logprobs=True, top_logprobs=1, structured_outputs={...}, response_format={...})` không có `chat_template_kwargs/structured_outputs/logprobs/top_logprobs/response_format`, `model == "ag/gemini-3.5-flash-lite"`; bật `LLM_ALT_LOGPROBS`/`LLM_ALT_SCHEMA` thì giữ khoá tương ứng; stream giữ `stream_options`.
- [ ] Test `test_complete_falls_back_on_429`: Gemma trả 429, alt trả 200 → kết quả của alt; hai `llm_call`: `("uit","error")` rồi `("9router","ok")`, sự kiện alt có `model == "ag/gemini-3.5-flash-lite"`.
- [ ] Test `test_classify_429_not_rerouted`: `generate_choice` khi Gemma 429 → `LLMError`, alt không bị gọi.
- [ ] Test `test_choice_on_alt_normalizes`: route `classify=alt`, alt trả `'"OCR."'` → `("ocr", None)`; trả `"xin chào"` → `LLMError`.
- [ ] Chạy `CUDA_VISIBLE_DEVICES= ~/.venvs/seevia/bin/python -m pytest tests/test_llm_branches.py -q` → FAIL.
- [ ] Cài đặt: `config.py` thêm 8 biến (Global Constraints; `LLM_ROUTE` parse `"a=alt,b=primary"` thành dict ở client, giá trị lạ bỏ qua). Client: `_Branch`, `_branches()` (đọc config lúc gọi để test monkeypatch được), `_plan`, `_body_for` (primary = `_body` cũ nguyên vẹn), `complete_raw` lặp theo `_plan(current_purpose("other"))`, ghi `llm_usage.record(provider=provider_of(base), model=branch.model, …)` mỗi lần thử; lỗi được dự phòng: `LLMError`, `httpx.HTTPError`, `KeyError`, `ValueError`. `generate_choice`: nếu nhánh trả lời là alt và không có structured → `_normalize_label`. `complete_raw` cần trả kèm nhánh đã chạy cho `generate_choice` → hàm nội bộ `_complete_with_branch(...) -> tuple[dict, _Branch]`, `complete_raw` gọi nó.
- [ ] Chạy test mới + `tests/test_llm_usage.py tests/test_llm_stats.py tests/test_v2_units.py` → PASS.

### Task 2: `stream_chat` theo kế hoạch + dự phòng trước chữ đầu

**Files:**
- Modify: `models/llm/client.py` (`_stream_chat`)
- Test: `tests/test_llm_branches.py`

**Interfaces:**
- Consumes: `_plan`, `_body_for`, `_Branch` (Task 1).

- [ ] Test `test_stream_falls_back_before_first_chunk`: Gemma 503 → chữ của alt; `llm_call` `uit/error` rồi `9router/ok`.
- [ ] Test `test_stream_no_fallback_after_first_chunk`: Gemma ra 1 mảnh rồi đứt (`httpx.ReadError`) → `LLMError`, alt không bị gọi.
- [ ] Test `test_stream_route_answer_alt`: route `answer=alt` → chỉ alt được gọi; body không có `chat_template_kwargs`.
- [ ] Test `test_stream_both_fail`: cả hai 503 → `LLMError` có tên hai nhánh trong thông điệp.
- [ ] Chạy → FAIL; cài: tách vòng hiện tại thành `_stream_once(branch, body, on_logprobs, purpose, ledger, produced: list[bool])`, `_stream_chat` lặp `_plan(purpose)`; giữ nguyên ghi `cancelled` khi bị huỷ. Log `"LLM %s lỗi (%r), chuyển sang %s"`.
- [ ] Chạy file test mới + `tests/test_llm_usage.py` → PASS.

### Task 3: prewarm, tài liệu, thử thật, toàn bộ test

**Files:**
- Modify: `models/llm/client.py` (`prewarm`), `docs/LLM_PROVIDER.md`
- Test: `tests/test_llm_branches.py`

- [ ] Test `test_prewarm_skips_alt_route`: route `prewarm=alt` → không request nào; bình thường → chỉ Gemma.
- [ ] Cài; thêm mục "Nhánh phụ (Gemini) — dự phòng và chọn theo mục đích" vào `docs/LLM_PROVIDER.md` (biến, số đo 3.5 Flash-Lite vs 3.8, giới hạn: không logprobs → đọc tiền từ chối, phân loại trần 0,8 s).
- [ ] Thử thật qua 9router (khoá đọc từ `SeeVia_system/.env` trong script scratchpad, không in): `stream_chat` + `complete` với route `answer=alt`, và Gemma URL hỏng → dự phòng; in `llm_call`.
- [ ] Toàn bộ: `CUDA_VISIBLE_DEVICES=0 ~/.venvs/seevia/bin/python -m pytest -q` → pass như trước (1217 pass, 2 skip) + test mới.
- [ ] Cập nhật file bàn giao `SeeVia_system/docs/2026-11-22-do-token-va-chi-phi.md`. Commit chỉ khi người dùng bảo.
