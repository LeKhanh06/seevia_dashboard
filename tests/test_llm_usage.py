"""Đo token LLM (services/llm_usage.py): đọc `usage` của phản hồi, ước lượng khi thiếu, gắn lượt; stream xin `usage` qua stream_options."""

import asyncio
import json

import httpx
import pytest

from models.llm import client
from services import llm_usage, usage


@pytest.fixture
def calls(monkeypatch):
    got: list[dict] = []
    monkeypatch.setattr(usage, "emit", lambda etype, **f: got.append({"type": etype, **f}) or "e")
    return got


def _mock(monkeypatch, handler):
    monkeypatch.setattr(client, "_client", type("F", (), {"get": lambda self: httpx.AsyncClient(
        transport=httpx.MockTransport(handler))})())
    monkeypatch.setattr(client, "_order", ["https://llm.uit.edu.vn/gemma/v1"])


def _sse(*objs) -> bytes:
    return b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs) + b"data: [DONE]\n\n"


def test_normalize_openai_reasoning_inclusive_and_exclusive():
    inc = llm_usage.normalize_openai({"prompt_tokens": 2010, "completion_tokens": 221,
                                      "completion_tokens_details": {"reasoning_tokens": 191}})
    assert inc == {"in": 2010, "out": 221, "reasoning": 191, "cached": 0}
    # 9router/Antigravity: completion KHÔNG gồm reasoning -> cộng thêm để tính đủ tiền
    exc = llm_usage.normalize_openai({"prompt_tokens": 2301, "completion_tokens": 3,
                                      "completion_tokens_details": {"reasoning_tokens": 290},
                                      "prompt_tokens_details": {"cached_tokens": 2000}})
    assert exc == {"in": 2301, "out": 293, "reasoning": 290, "cached": 2000}
    assert llm_usage.normalize_openai(None) is None and llm_usage.normalize_openai({}) is None


def test_normalize_gemini_counts_thoughts_as_output():
    meta = {"prompt_token_count": 1200, "candidates_token_count": 40, "thoughts_token_count": 100,
            "cached_content_token_count": 800}
    assert llm_usage.normalize_gemini(meta) == {"in": 1200, "out": 140, "reasoning": 100, "cached": 800}
    assert llm_usage.normalize_gemini(None) is None


def test_provider_labels():
    assert llm_usage.provider_of("https://llm.uit.edu.vn/gemma/v1") == "uit"
    assert llm_usage.provider_of("http://localhost:20128/v1") == "9router"
    assert llm_usage.provider_of("https://generativelanguage.googleapis.com/v1beta/openai") == "aistudio"
    assert llm_usage.provider_of("https://asia-southeast1-aiplatform.googleapis.com/v1") == "vertex"


def test_complete_reads_usage_and_keeps_gemma_body(monkeypatch, calls):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "web"}}],
                                         "usage": {"prompt_tokens": 120, "completion_tokens": 2}})

    _mock(monkeypatch, handler)
    label, _ = asyncio.run(client.generate_choice("phân loại", ["web", "no_web"]))
    assert label == "web"
    # Không stream: body không có khoá đo đếm nào (usage luôn có sẵn trong phản hồi)
    assert set(bodies[0]) == {"model", "messages", "max_tokens", "temperature", "stream",
                              "chat_template_kwargs", "logprobs", "top_logprobs", "structured_outputs"}
    (ev,) = calls
    assert ev["type"] == "llm_call" and ev["attempt_id"] is None
    assert (ev["provider"], ev["purpose"], ev["status"]) == ("uit", "classify", "ok")
    assert (ev["in_tokens"], ev["out_tokens"], ev["estimated"]) == (120, 2, False)


def test_stream_requests_usage(monkeypatch, calls):
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "ok"}}]}),
                              headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)

    async def main():
        return [p async for p in client.stream_chat([client.user_message("hi")])]

    assert asyncio.run(main()) == ["ok"]
    assert bodies[0]["stream_options"] == {"include_usage": True}
    monkeypatch.setattr(client.config, "LLM_STREAM_USAGE", False)
    asyncio.run(main())
    assert "stream_options" not in bodies[1]


def test_stream_without_usage_is_estimated(monkeypatch, calls):
    def handler(req):
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "Xin chào bạn"}}]}),
                              headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)

    async def main():
        return "".join([p async for p in client.stream_chat(
            [client.user_message("a" * 300, image=b"\xff\xd8jpeg")])])

    assert asyncio.run(main()) == "Xin chào bạn"
    (ev,) = calls
    assert ev["estimated"] is True and ev["status"] == "ok" and ev["purpose"] == "answer" and ev["images"] == 1
    assert ev["in_tokens"] == round(300 / 3.5) + 260 and ev["out_tokens"] == round(12 / 3.5)


def test_stream_usage_used_when_server_sends_it(monkeypatch, calls):
    def handler(req):
        return httpx.Response(200, content=_sse(
            {"choices": [{"delta": {"content": "ok"}}]},
            {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 7}}),
            headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)

    async def main():
        return [p async for p in client.stream_chat([client.user_message("hi")])]

    assert asyncio.run(main()) == ["ok"]
    assert (calls[0]["in_tokens"], calls[0]["out_tokens"], calls[0]["estimated"]) == (50, 7, False)


def test_cancelled_stream_recorded_as_cancelled(monkeypatch, calls):
    def handler(req):
        return httpx.Response(200, content=_sse(*[{"choices": [{"delta": {"content": "chữ "}}]}] * 5),
                              headers={"content-type": "text/event-stream"})

    _mock(monkeypatch, handler)

    async def main():
        stream = client.stream_chat([client.user_message("hi")])
        await stream.__anext__()
        await stream.aclose()  # speculation đoán sai làn: bỏ giữa chừng

    asyncio.run(main())
    (ev,) = calls
    assert ev["status"] == "cancelled" and ev["estimated"] is True and ev["out_tokens"] > 0


def test_http_error_costs_nothing(monkeypatch, calls):
    _mock(monkeypatch, lambda req: httpx.Response(429))
    with pytest.raises(client.LLMError):
        asyncio.run(client.complete([client.user_message("hi")]))
    (ev,) = calls
    assert ev["status"] == "error" and ev["in_tokens"] == 0 and ev["out_tokens"] == 0


def test_purpose_and_ledger_binding(monkeypatch, calls):
    _mock(monkeypatch, lambda req: httpx.Response(200, json={
        "choices": [{"message": {"content": "x"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 1}}))

    async def main():
        llm_usage.start_turn()
        await client.prewarm([client.user_message("p")])  # trước khi lượt có attempt_id: giữ trong sổ
        assert calls == []
        llm_usage.bind_attempt("att-1")
        with llm_usage.purpose("money"):
            await client.complete([client.user_message("m")])  # sau khi gắn: gửi ngay

    asyncio.run(main())
    assert [(c["purpose"], c["attempt_id"]) for c in calls] == [("prewarm", "att-1"), ("money", "att-1")]


def test_stream_keeps_ledger_of_turn_it_started_in(monkeypatch, calls):
    """Stream lượt trước kết thúc muộn trong cùng task không bị tính sang lượt sau."""
    _mock(monkeypatch, lambda req: httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "a"}}]}),
                                                  headers={"content-type": "text/event-stream"}))

    async def main():
        llm_usage.start_turn()
        llm_usage.bind_attempt("old")
        stream = client.stream_chat([client.user_message("hi")])
        llm_usage.start_turn()  # lượt mới mở trước khi stream cũ được đọc
        llm_usage.bind_attempt("new")
        return [p async for p in stream]

    asyncio.run(main())
    assert calls[0]["attempt_id"] == "old"


def test_event_type_allowed_and_no_text_fields():
    allowed = usage.ALLOWED_FIELDS["llm_call"]
    assert {"in_tokens", "out_tokens", "provider", "model", "purpose"} <= allowed
    assert not allowed & {"text", "prompt", "content", "reply"}
