"""Nhánh LLM phụ (Gemini qua 9router/AI Studio): chọn theo mục đích (LLM_ROUTE) + dự phòng khi Gemma hỏng."""

import asyncio
import json

import httpx
import pytest

from models.llm import client
from services import usage

GEMMA = "https://llm.uit.edu.vn/gemma/v1"
ALT = "http://localhost:20128/v1"
ALT_MODEL = "ag/gemini-3.5-flash-lite"


@pytest.fixture
def calls(monkeypatch):
    got: list[dict] = []
    monkeypatch.setattr(usage, "emit", lambda etype, **f: got.append({"type": etype, **f}) or "e")
    return got


@pytest.fixture
def alt(monkeypatch):
    """Bật nhánh phụ (mặc định route rỗng, dự phòng bật)."""
    monkeypatch.setattr(client, "_alt_order", [ALT])
    monkeypatch.setattr(client.config, "LLM_ALT_MODEL", ALT_MODEL)
    monkeypatch.setattr(client.config, "LLM_ROUTE", "")
    monkeypatch.setattr(client.config, "LLM_FALLBACK", True)
    monkeypatch.setattr(client.config, "LLM_FALLBACK_PURPOSES", ("answer", "ocr", "extract", "other"))
    monkeypatch.setattr(client.config, "LLM_ALT_LOGPROBS", False)
    monkeypatch.setattr(client.config, "LLM_ALT_SCHEMA", False)


def _mock(monkeypatch, handler):
    """Một handler cho cả hai nhánh; phân biệt theo host của request."""
    fake = type("F", (), {"get": lambda self: httpx.AsyncClient(transport=httpx.MockTransport(handler))})()
    monkeypatch.setattr(client, "_client", fake)
    monkeypatch.setattr(client, "_alt_client", fake)
    monkeypatch.setattr(client, "_order", [GEMMA])


def _sse(*objs) -> bytes:
    return b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs) + b"data: [DONE]\n\n"


def _is_alt(req) -> bool:
    return req.url.host == "localhost"


def _names(plan) -> list[str]:
    return [b.name for b in plan]


# ---- Task 1: kế hoạch, body, complete_raw, generate_choice ----------------------------------

def test_alt_off_keeps_gemma_only(monkeypatch):
    monkeypatch.setattr(client, "_alt_order", [])
    monkeypatch.setattr(client.config, "LLM_ROUTE", "answer=alt")
    assert _names(client._plan("answer")) == ["primary"]
    assert _names(client._plan("ocr")) == ["primary"]


def test_plan_route_and_fallback(monkeypatch, alt):
    monkeypatch.setattr(client.config, "LLM_ROUTE", "answer=alt, money=bogus")
    assert _names(client._plan("answer")) == ["alt", "primary"]
    assert _names(client._plan("classify")) == ["primary"]   # ngoài LLM_FALLBACK_PURPOSES
    assert _names(client._plan("money")) == ["primary"]      # giá trị route lạ bị bỏ qua
    assert _names(client._plan("ocr")) == ["primary", "alt"]
    monkeypatch.setattr(client.config, "LLM_FALLBACK", False)
    assert _names(client._plan("ocr")) == ["primary"]
    assert _names(client._plan("answer")) == ["alt"]


def test_alt_body_drops_vllm_keys(monkeypatch, alt):
    _, branch = client._branches()
    extra = {"logprobs": True, "top_logprobs": 1, "structured_outputs": {"choice": ["a"]},
             "response_format": {"type": "json_schema"}}
    body = client._body_for(branch, [client.user_message("hi")], 10, False, **extra)
    assert body["model"] == ALT_MODEL
    for key in ("chat_template_kwargs", "structured_outputs", "logprobs", "top_logprobs", "response_format"):
        assert key not in body
    monkeypatch.setattr(client.config, "LLM_ALT_LOGPROBS", True)
    monkeypatch.setattr(client.config, "LLM_ALT_SCHEMA", True)
    body = client._body_for(branch, [client.user_message("hi")], 10, True, **extra)
    assert body["logprobs"] is True and body["top_logprobs"] == 1
    assert body["response_format"] == {"type": "json_schema"}
    assert "structured_outputs" not in body
    assert body["stream_options"] == {"include_usage": True}


def test_primary_body_unchanged(alt):
    primary, _ = client._branches()
    msgs = [client.user_message("hi")]
    assert client._body_for(primary, msgs, 10, False, logprobs=True) == client._body(msgs, 10, False, logprobs=True)


def test_complete_falls_back_on_429(monkeypatch, alt, calls):
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if not _is_alt(req):
            return httpx.Response(429)
        return httpx.Response(200, json={"choices": [{"message": {"content": "xin chào"}}],
                                         "usage": {"prompt_tokens": 2010, "completion_tokens": 3}})

    _mock(monkeypatch, handler)
    assert asyncio.run(client.complete([client.user_message("chào")], max_tokens=5)) == "xin chào"
    assert seen == ["llm.uit.edu.vn", "localhost"]
    assert [(c["provider"], c["status"]) for c in calls] == [("uit", "error"), ("9router", "ok")]
    assert calls[1]["model"] == ALT_MODEL
    assert calls[0]["model"] == client.config.LLM_MODEL


def test_classify_429_not_rerouted(monkeypatch, alt, calls):
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(429)

    _mock(monkeypatch, handler)
    with pytest.raises(client.LLMError):
        asyncio.run(client.generate_choice("phân loại", ["ocr", "chat"]))
    assert seen == ["llm.uit.edu.vn"]


def test_choice_on_alt_normalizes(monkeypatch, alt, calls):
    monkeypatch.setattr(client.config, "LLM_ROUTE", "classify=alt")
    reply = {"text": '"OCR."'}

    def handler(req):
        assert _is_alt(req)
        assert "structured_outputs" not in json.loads(req.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": reply["text"]}}]})

    _mock(monkeypatch, handler)
    assert asyncio.run(client.generate_choice("phân loại", ["ocr", "chat"])) == ("ocr", None)
    reply["text"] = "xin chào"
    with pytest.raises(client.LLMError):
        asyncio.run(client.generate_choice("phân loại", ["ocr", "chat"]))


def test_normalize_label():
    assert client._normalize_label(" 'Money' ", ["money", "ocr"]) == "money"
    assert client._normalize_label("ocr hoặc money", ["money", "ocr"]) is None
    assert client._normalize_label("music/play", ["music/play", "music"]) == "music/play"
    # Chỉ khớp nguyên văn: câu có nhãn bên trong (kể cả phủ định) không được đoán thành nhãn đó
    assert client._normalize_label("no web", ["web", "no_web"]) == "no_web"
    assert client._normalize_label("no-web.", ["web", "no_web"]) == "no_web"
    assert client._normalize_label("không phải ocr", ["money", "ocr"]) is None
    assert client._normalize_label("nhãn: ocr", ["money", "ocr"]) is None


# ---- Task 2: stream_chat ---------------------------------------------------------------------

def _collect(**kw):
    async def main():
        return "".join([p async for p in client.stream_chat([client.user_message("chào")], **kw)])
    return asyncio.run(main())


def _chunk(text):
    return {"choices": [{"delta": {"content": text}}]}


def test_stream_falls_back_before_first_chunk(monkeypatch, alt, calls):
    def handler(req):
        if not _is_alt(req):
            return httpx.Response(503)
        return httpx.Response(200, content=_sse(_chunk("từ Gemini"),
                                                 {"choices": [], "usage": {"prompt_tokens": 2010, "completion_tokens": 4}}),
                              headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)
    assert _collect() == "từ Gemini"
    assert [(c["provider"], c["status"]) for c in calls] == [("uit", "error"), ("9router", "ok")]
    assert calls[1]["model"] == ALT_MODEL
    assert calls[1]["in_tokens"] == 2010


def test_stream_no_fallback_after_first_chunk(monkeypatch, alt, calls):
    seen = []

    class ABroken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"data: " + json.dumps(_chunk("nửa câu ")).encode() + b"\n\n"
            raise httpx.ReadError("đứt")

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(200, stream=ABroken(), headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)
    got = []

    async def main():
        async for p in client.stream_chat([client.user_message("chào")]):
            got.append(p)

    with pytest.raises(client.LLMError):
        asyncio.run(main())
    assert got == ["nửa câu "]
    assert seen == ["llm.uit.edu.vn"]


def test_stream_route_answer_alt(monkeypatch, alt, calls):
    monkeypatch.setattr(client.config, "LLM_ROUTE", "answer=alt")
    seen = []

    def handler(req):
        seen.append(req.url.host)
        body = json.loads(req.content)
        assert "chat_template_kwargs" not in body
        assert body["model"] == ALT_MODEL
        return httpx.Response(200, content=_sse(_chunk("ok")), headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)
    assert _collect() == "ok"
    assert seen == ["localhost"]


def test_stream_both_fail(monkeypatch, alt, calls):
    _mock(monkeypatch, lambda req: httpx.Response(503))
    with pytest.raises(client.LLMError) as info:
        _collect()
    assert "primary" in str(info.value)
    assert "alt" in str(info.value)
    assert [c["status"] for c in calls] == ["error", "error"]


def test_stream_cancel_closes_branch_immediately(monkeypatch, alt, calls):
    """Speculation sai làn bỏ stream: nhánh con đóng NGAY (httpx đóng, llm_call `cancelled` ghi trước khi aclose trả về)."""
    def handler(req):
        return httpx.Response(200, content=_sse(*[_chunk("chữ ")] * 5), headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)

    async def main():
        stream = client.stream_chat([client.user_message("chào")])
        await stream.__anext__()
        await stream.aclose()
        return [c["status"] for c in calls]

    assert asyncio.run(main()) == ["cancelled"]


# ---- Task 3: prewarm -------------------------------------------------------------------------

def test_prewarm_skips_alt_route(monkeypatch, alt, calls):
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(200, json={"choices": [{"message": {"content": "o"}}]})

    _mock(monkeypatch, handler)
    asyncio.run(client.prewarm([client.user_message("ok")]))
    assert seen == ["llm.uit.edu.vn"]
    monkeypatch.setattr(client.config, "LLM_ROUTE", "prewarm=alt")
    seen.clear()
    asyncio.run(client.prewarm([client.user_message("ok")]))
    assert seen == []


def test_prewarm_never_falls_back_to_alt(monkeypatch, alt, calls):
    monkeypatch.setattr(client.config, "LLM_FALLBACK_PURPOSES", ("answer", "prewarm"))
    seen = []

    def handler(req):
        seen.append(req.url.host)
        return httpx.Response(503)

    _mock(monkeypatch, handler)
    asyncio.run(client.prewarm([client.user_message("ok")]))
    assert seen == ["llm.uit.edu.vn"]


def test_money_stays_on_gemma_without_alt_logprobs(monkeypatch, alt):
    """Không có xác suất thì đọc tiền không chốt được mệnh giá và có thể nói nhầm "không thấy tờ tiền":
    route money=alt chỉ có hiệu lực khi nhánh phụ trả logprobs."""
    monkeypatch.setattr(client.config, "LLM_ROUTE", "money=alt")
    assert _names(client._plan("money")) == ["primary"]
    monkeypatch.setattr(client.config, "LLM_ALT_LOGPROBS", True)
    assert _names(client._plan("money")) == ["alt"]
