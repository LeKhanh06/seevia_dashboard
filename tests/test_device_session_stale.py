"""Phiên ma trong registry (loop đã đóng) không được chặn kính nối lại."""


def test_stale_session_on_closed_loop_does_not_block_new_hello():
    # Phiên của lần trước bị huỷ giữa lúc shutdown (TestClient đóng loop) để lại mục trong registry: kính nối lại
    # vẫn phải vào được, không ném "Event loop is closed".
    import asyncio
    import json

    from fastapi.testclient import TestClient

    from app import app
    from live import device_session
    from tests.test_v2_session import _hello

    loop = asyncio.new_event_loop()
    loop.close()
    stale = device_session.DeviceSession.__new__(device_session.DeviceSession)
    stale._evloop, stale._shut, stale.device = loop, True, "ws-stale"
    device_session._registry["ws-stale"] = stale
    try:
        with TestClient(app).websocket_connect("/v2/session") as ws:
            _hello(ws, device="ws-stale")
            assert json.loads(ws.receive_text())["t"] == "hello_ok"
    finally:
        if device_session._registry.get("ws-stale") is stale:
            del device_session._registry["ws-stale"]
