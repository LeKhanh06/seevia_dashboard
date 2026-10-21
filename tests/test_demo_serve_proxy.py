"""`demo-web/serve.py` chỉ chuyển tiếp danh sách trắng: `/internal/*` không bao giờ tới server kính (I-3)."""

import importlib.util
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest

_SERVE = os.path.join(os.path.dirname(__file__), "..", "demo-web", "serve.py")


@pytest.fixture
def proxy():
    spec = importlib.util.spec_from_file_location("demo_serve", _SERVE)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    seen: list[str] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_POST = do_GET

        def log_message(self, *a):
            pass

    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    serve.Handler.upstream = urlsplit(f"http://127.0.0.1:{up.server_port}")
    front = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    for srv in (up, front):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{front.server_port}", seen
    for srv in (up, front):
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("path", [
    "/api/internal/calls", "/api/internal/messages", "/api/internal/action-results",
    "/api/v2/session/../internal/calls", "/api/%2e%2e/internal/calls", "/api//internal/calls",
    "/api/v2/debug/stats", "/api/",
])
def test_internal_and_unknown_paths_are_not_forwarded(proxy, path):
    base, seen = proxy
    for method in ("GET", "POST"):
        resp = httpx.request(method, base + path, headers={"Authorization": "Bearer x"}, content=b"{}")
        assert resp.status_code == 404
    assert seen == []


@pytest.mark.parametrize("path, upstream", [
    ("/api/health", "/health"), ("/api/process?x=1", "/process?x=1"), ("/api/v2/turn", "/v2/turn"),
    ("/api/v2/image", "/v2/image"), ("/api/v2/observe", "/v2/observe"),
])
def test_allowed_paths_are_forwarded(proxy, path, upstream):
    base, seen = proxy
    resp = httpx.post(base + path, content=b"{}")
    assert resp.status_code == 200 and resp.text == "ok"
    assert seen == [upstream]


# --- cấu hình sẵn: proxy tự gắn token vào WS của trang, token không bao giờ xuống trình duyệt ---

TOKEN = "tok-bi-mat-cua-server"


@pytest.fixture
def ws_proxy():
    """Proxy + upstream TCP thô ghi lại nguyên request bắt tay WS mà proxy chuyển lên."""
    spec = importlib.util.spec_from_file_location("demo_serve_ws", _SERVE)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    captured: list[str] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)

    def accept_loop():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            captured.append(data.decode("latin-1"))
            conn.close()

    threading.Thread(target=accept_loop, daemon=True).start()
    serve.Handler.upstream = urlsplit(f"http://127.0.0.1:{listener.getsockname()[1]}")
    serve.Handler.device_token = TOKEN
    serve.Handler.device_id = "kinh-hien-tai"
    front = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=front.serve_forever, daemon=True).start()
    yield serve, front.server_port, captured
    front.shutdown()
    front.server_close()
    listener.close()


def _upgrade(port, path, **headers):
    """Gửi yêu cầu nâng cấp WS thô tới proxy, trả request upstream nhận được (chuỗi header)."""
    host = headers.pop("Host", f"127.0.0.1:{port}")
    lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Upgrade: websocket", "Connection: Upgrade",
             "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13"]
    lines += [f"{k.replace('_', '-')}: {v}" for k, v in headers.items()]
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        sock.settimeout(5)
        try:
            while sock.recv(4096):
                pass
        except (socket.timeout, OSError):
            pass


def _wait_captured(captured, n=1):
    import time

    deadline = time.monotonic() + 3
    while len(captured) < n and time.monotonic() < deadline:
        time.sleep(0.02)
    return captured


def test_config_json_gives_device_and_never_the_token(ws_proxy):
    _, port, _ = ws_proxy
    resp = httpx.get(f"http://127.0.0.1:{port}/config.json")
    assert resp.status_code == 200 and resp.json() == {"device": "kinh-hien-tai"}
    assert TOKEN not in resp.text and resp.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api/v2/observe", "/api/v2/session"])
def test_proxy_injects_token_into_same_origin_ws(ws_proxy, path):
    _, port, captured = ws_proxy
    _upgrade(port, path, Origin=f"http://127.0.0.1:{port}")
    assert f"Authorization: Bearer {TOKEN}" in _wait_captured(captured)[0]


def test_proxy_injects_token_when_no_origin_header(ws_proxy):
    _, port, captured = ws_proxy
    _upgrade(port, "/api/v2/observe")  # client không phải trình duyệt (realcheck...) không có Origin
    assert f"Authorization: Bearer {TOKEN}" in _wait_captured(captured)[0]


def test_proxy_does_not_lend_token_to_foreign_origin(ws_proxy):
    _, port, captured = ws_proxy
    _upgrade(port, "/api/v2/observe", Origin="https://trang-la.example")
    assert TOKEN not in _wait_captured(captured)[0]


def test_proxy_keeps_token_the_client_brought(ws_proxy):
    _, port, captured = ws_proxy
    _upgrade(port, "/api/v2/observe", Authorization="Bearer cua-client")
    sent = _wait_captured(captured)[0]
    assert "Bearer cua-client" in sent and TOKEN not in sent


def test_proxy_injects_only_on_observe_and_session(ws_proxy):
    _, port, captured = ws_proxy
    _upgrade(port, "/api/v2/live")
    assert TOKEN not in _wait_captured(captured)[0]


def test_no_token_configured_injects_nothing(ws_proxy):
    serve, port, captured = ws_proxy
    serve.Handler.device_token = ""
    _upgrade(port, "/api/v2/observe")
    assert "Authorization" not in _wait_captured(captured)[0]


def test_demo_credentials_read_env_file_and_cli_wins(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_serve_env", _SERVE)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    env = tmp_path / ".env"
    env.write_text("# ghi chú\nOTHER=1\nVISIONCARE_DEVICE_CLIENT_TOKEN='tok-env'\nVISIONCARE_DEVICE_ID=glasses-env\n")
    for key in ("VISIONCARE_DEVICE_CLIENT_TOKEN", "VISIONCARE_DEVICE_ID"):
        monkeypatch.delenv(key, raising=False)
    assert serve.demo_credentials(env_file=str(env)) == ("tok-env", "glasses-env")
    assert serve.demo_credentials("tok-cli", "kinh-cli", env_file=str(env)) == ("tok-cli", "kinh-cli")
    monkeypatch.setenv("VISIONCARE_DEVICE_ID", "glasses-shell")  # biến môi trường thắng .env
    assert serve.demo_credentials(env_file=str(env)) == ("tok-env", "glasses-shell")
    assert serve.demo_credentials(env_file=str(tmp_path / "khong-co")) == ("", "glasses-shell")
    monkeypatch.delenv("VISIONCARE_DEVICE_ID")
    assert serve.demo_credentials(env_file=str(tmp_path / "khong-co")) == ("", "glasses-123")  # như config.py


def test_demo_credentials_token_file(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("demo_serve_tokfile", _SERVE)
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    env = tmp_path / ".env"
    env.write_text("VISIONCARE_DEVICE_CLIENT_TOKEN=tok-env\nVISIONCARE_DEVICE_ID=glasses-env\n")
    tf = tmp_path / "kinh.token"
    tf.write_text("tok-file\n")
    for key in ("VISIONCARE_DEVICE_CLIENT_TOKEN", "VISIONCARE_DEVICE_ID"):
        monkeypatch.delenv(key, raising=False)
    assert serve.demo_credentials(env_file=str(env), token_file=str(tf)) == ("tok-file", "glasses-env")  # thắng .env
    assert serve.demo_credentials("tok-cli", env_file=str(env), token_file=str(tf))[0] == "tok-cli"  # --token thắng
    with pytest.raises(SystemExit):
        serve.demo_credentials(env_file=str(env), token_file=str(tmp_path / "khong-co"))
