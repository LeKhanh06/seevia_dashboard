"""Phục vụ trang demo và chuyển tiếp `/api/*` tới server kính.

Cấu hình sẵn: mở trang là chạy, không phải nhập token hay mã kính. Proxy đọc `VISIONCARE_DEVICE_CLIENT_TOKEN` và
`VISIONCARE_DEVICE_ID` (biến môi trường, hoặc `.env` của repo, hoặc `--token`/`--device`) rồi tự gắn `Authorization:
Bearer <token>` vào kết nối WebSocket `/api/v2/observe` và `/api/v2/session` của trang CÙNG NGUỒN GỐC; token không bao
giờ xuống trình duyệt. Mã kính (không bí mật) trang lấy ở `/config.json`. Hệ quả: ai mở được trang này (`--lan` là cả
mạng) là được vào luôn với quyền kính, nên chỉ bật trong mạng demo.

Cần có file này vì hai lý do:

- `app.py` không bật CORS. Trang mở từ nguồn khác (file://, cổng khác) gọi
  thẳng `/process` là bị trình duyệt chặn. Chuyển tiếp qua cùng một nguồn thì
  trang không cần server sửa gì.
- `getUserMedia` (camera/micro của `client.html`) chỉ chạy trên https hoặc localhost. Phục vụ
  trang ở đây rồi mở bằng http://localhost:8080 là đủ điều kiện.

Ngoài HTTP còn chuyển tiếp WebSocket: `/api/v2/observe` (bảng giám sát `index.html`, chỉ xem) và
`/api/v2/session` (kính giả lập `client.html`) tới server kính. Proxy không đọc khung,
chỉ chuyển byte hai chiều sau cái bắt tay `Upgrade: websocket`.

Chỉ dùng thư viện chuẩn: máy trình bày không cần cài thêm gì.

    python demo-web/serve.py                       # server kính ở 127.0.0.1:8000
    python demo-web/serve.py --upstream http://192.168.1.20:8000

Client là máy khác (laptop BGK) thì chạy trên máy đặt server:

    python demo-web/serve.py --lan

`--lan` mở cổng cho cả mạng và bật https với chứng chỉ tự ký, vì camera/micro của
`client.html` bị trình duyệt từ chối chạy qua http://<địa-chỉ-IP> (bảng giám sát thì không cần).
"""
import argparse
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
API_PREFIX = "/api/"

# Danh sách trắng đường dẫn được chuyển tiếp (so khớp CHÍNH XÁC, sau khi bỏ query). `/internal/*` (callback AB ->
# VH) tuyệt đối không qua đây: proxy chạy cùng máy với VH nên IP nguồn luôn là 127.0.0.1 và vòng chặn IP của
# callback sẽ bị vượt khi chạy `--lan`; chỉ còn `VISIONCARE_CALLBACK_TOKEN` thì quá yếu.
ENV_FILE = os.path.join(ROOT, "..", ".env")
WS_AUTH_PATHS = frozenset({"/v2/observe", "/v2/session"})  # đường WS được tự gắn token (xem docstring đầu file)

ALLOWED_API_PATHS = frozenset({
    "/process", "/v2/turn", "/v2/image", "/v2/session", "/v2/observe", "/v2/live", "/v2/call", "/health",
})


def read_env_file(path: str = ENV_FILE) -> dict[str, str]:
    """`KEY=VALUE` từng dòng của `.env` (bỏ dòng trống, `#`, dấu nháy bao quanh). Thiếu file thì trả {}."""
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                    value = value[1:-1]
                out[key.strip()] = value
    except OSError:
        pass
    return out


def read_token_file(path: str) -> str:
    """Dòng đầu (bỏ khoảng trắng) của file token; file không đọc được thì thoát với thông báo (không in token)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().strip()
    except OSError as exc:
        sys.exit(f"--token-file không đọc được ({path}): {exc.strerror}")


def demo_credentials(token: str = "", device: str = "", env_file: str = ENV_FILE,
                     token_file: str = "") -> tuple[str, str]:
    """(token, mã kính) cho trang: `--token` > `--token-file` > biến môi trường > `.env`; mã kính mặc định như `config.py`.

    `--token-file` dành cho token RIÊNG của kính (`~/.config/seevia/<kính>.token`, quyền 600): không phải đưa token qua
    dòng lệnh (lộ trong `ps`) hay `.env`."""
    env = read_env_file(env_file)
    token = token or (read_token_file(token_file) if token_file else "")

    def pick(arg: str, key: str, default: str = "") -> str:
        return arg or os.environ.get(key) or env.get(key) or default

    return pick(token, "VISIONCARE_DEVICE_CLIENT_TOKEN"), pick(device, "VISIONCARE_DEVICE_ID", "glasses-123")


def api_path_allowed(request_path: str) -> bool:
    """`request_path` dạng `/api/<đường dẫn>[?query]`: True nếu đường dẫn đích nằm trong danh sách trắng."""
    return urlsplit(request_path[len(API_PREFIX) - 1:]).path in ALLOWED_API_PATHS

# Header theo từng chặng (RFC 9110 §7.6.1): không được chuyển tiếp nguyên.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
}


class Handler(SimpleHTTPRequestHandler):
    upstream = None  # urlsplit(...) của server kính, gán trong main()
    device_token = ""  # token tự gắn vào WS của trang (không bao giờ gửi xuống trình duyệt)
    device_id = ""  # mã kính hiện tại: trang lấy ở /config.json

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        if urlsplit(self.path).path == "/config.json":
            return self._send_config()
        if self.path.startswith(API_PREFIX):
            if not api_path_allowed(self.path):
                return self.send_error(404)
            if self.headers.get("Upgrade", "").lower() == "websocket":
                return self._proxy_ws()
            return self._proxy()
        super().do_GET()

    def do_POST(self):
        if self.path.startswith(API_PREFIX):
            if not api_path_allowed(self.path):
                return self.send_error(404)
            return self._proxy()
        self.send_error(405)

    def _send_config(self):
        body = json.dumps({"device": self.device_id}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ws_auth_header(self, path: str) -> str | None:
        """Dòng `Authorization` tự gắn cho WS của trang, hoặc None (không có token / đường khác / client tự mang token /
        trang nguồn gốc khác: trang web lạ không được mượn token của server)."""
        token = self.device_token
        if not token or "\r" in token or "\n" in token or urlsplit(path).path not in WS_AUTH_PATHS:
            return None
        if any(k.lower() in ("authorization", "x-device-token") for k in self.headers):
            return None
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return None
        return f"Authorization: Bearer {token}"

    def end_headers(self):
        # Trang dùng module ES: trình duyệt cache cứng thì sửa file xong vẫn
        # chạy bản cũ, và đó là thứ rất khó nhận ra giữa buổi demo.
        if not self.path.startswith(API_PREFIX) and urlsplit(self.path).path != "/config.json":  # config.json tự đặt no-store
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _proxy(self):
        up = self.upstream
        path = self.path[len(API_PREFIX) - 1:]  # "/api/process" -> "/process"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}

        conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
        # Không đặt timeout: STT + Gemini + TTS có thể mất vài chục giây, và
        # luồng SSE trace mở suốt lượt.
        conn = conn_cls(up.hostname, up.port or (443 if up.scheme == "https" else 80))
        try:
            conn.request(self.command, path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_error(502, f"Không nối được server kính: {exc}")
            conn.close()
            return

        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP_BY_HOP:
                self.send_header(k, v)
        # Không biết độ dài trước (audio đang được tổng hợp) nên đóng kết nối
        # để báo hết thân, thay vì dựng lại chunked encoding.
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while True:
                # read1 trả về ngay khi có byte, không đợi đủ cỡ khối: nếu
                # dùng read(n) thì gói audio đầu bị giữ lại chờ gói sau.
                chunk = resp.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # trình duyệt đóng tab giữa chừng
        finally:
            conn.close()

    def _proxy_ws(self):
        """Nối WebSocket của trình duyệt tới server kính bằng cách bơm byte hai chiều.

        Không làm lại bắt tay: chuyển nguyên dòng request và header (chỉ đổi `Host`) để
        server tự trả `101 Switching Protocols`; sau đó mọi byte đi qua như ống dẫn.
        """
        up = self.upstream
        path = self.path[len(API_PREFIX) - 1:]
        try:
            raw = socket.create_connection((up.hostname, up.port or (443 if up.scheme == "https" else 80)), timeout=10)
            if up.scheme == "https":
                raw = ssl.create_default_context().wrap_socket(raw, server_hostname=up.hostname)
            raw.settimeout(None)  # kết nối WS sống lâu: ping của trang giữ nhịp, không đặt hạn đọc
        except OSError as exc:
            self.send_error(502, f"Không nối được server kính: {exc}")
            return

        host = up.hostname + (f":{up.port}" if up.port else "")
        head = [f"GET {path} HTTP/1.1", f"Host: {host}"]
        head += [f"{k}: {v}" for k, v in self.headers.items() if k.lower() != "host"]
        auth = self._ws_auth_header(path)
        if auth:
            head.append(auth)
        raw.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

        client = self.connection

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass  # một đầu đóng đột ngột
            finally:
                # Đóng nửa còn lại để thread bên kia thoát khỏi recv().
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        back = threading.Thread(target=pump, args=(raw, client), daemon=True)
        back.start()
        pump(client, raw)
        back.join(timeout=2)
        raw.close()
        self.close_connection = True

    def log_message(self, fmt, *args):
        if not self.path.startswith(API_PREFIX + "health"):  # poll 10 giây/lần, bớt ồn
            super().log_message(fmt, *args)


def lan_ip():
    """IP của máy này trong mạng LAN, để in ra địa chỉ cho máy client mở."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP: không gửi gói nào, chỉ để hệ điều hành chọn card mạng
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def self_signed_cert(ip):
    """Tạo (một lần) chứng chỉ tự ký có SAN cho IP LAN; trả về (cert, key).

    Phải có SAN đúng IP: trình duyệt bỏ qua CN, nên thiếu SAN thì bấm "tiếp
    tục" xong vẫn bị coi là chứng chỉ sai tên. Đổi mạng (IP khác) thì xoá thư
    mục certs/ để dựng lại.
    """
    folder = os.path.join(ROOT, "certs")
    cert, key = os.path.join(folder, "demo.pem"), os.path.join(folder, "demo.key")
    if os.path.exists(cert) and os.path.exists(key):
        with open(os.path.join(folder, "demo.ip")) as f:
            if f.read().strip() == ip:
                return cert, key
    os.makedirs(folder, exist_ok=True)
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
             "-keyout", key, "-out", cert, "-subj", "/CN=seevia-demo",
             "-addext", f"subjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost"],
            check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Không tạo được chứng chỉ (cần lệnh openssl): {exc}\n"
                 "Tự cấp bằng --cert/--key, hoặc cài openssl rồi chạy lại.")
    with open(os.path.join(folder, "demo.ip"), "w") as f:
        f.write(ip)
    return cert, key


class TLSThreadingHTTPServer(ThreadingHTTPServer):
    """https mà bắt tay TLS nằm trong luồng của từng kết nối.

    Bọc luôn ổ cắm đang nghe thì bắt tay chạy ngay trong accept() của luồng chính: một client
    nói HTTP thường vào cổng https (gõ nhầm http://) hay kết nối rồi im làm treo cả server.
    """
    tls_context = None
    handshake_timeout = 10

    def finish_request(self, request, client_address):
        request.settimeout(self.handshake_timeout)
        try:
            request = self.tls_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return  # không phải TLS / treo bắt tay: bỏ kết nối này, server vẫn sống
        request.settimeout(None)
        super().finish_request(request, client_address)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--upstream", default=os.environ.get("GLASSES_SERVER_URL", "http://127.0.0.1:8000"))
    p.add_argument("--host", default="127.0.0.1", help="0.0.0.0 để máy khác trong mạng truy cập được")
    p.add_argument("--port", type=int, default=None, help="mặc định 8080 (http) hoặc 8443 (https)")
    p.add_argument("--lan", action="store_true", help="cho máy khác trong mạng truy cập: nghe 0.0.0.0 và bật https tự ký")
    p.add_argument("--cert", help="file chứng chỉ PEM, bật https")
    p.add_argument("--key", help="file khoá PEM đi kèm --cert")
    p.add_argument("--token", default="", help="token thiết bị tự gắn vào WS (mặc định: VISIONCARE_DEVICE_CLIENT_TOKEN / .env)")
    p.add_argument("--token-file", default="", help="file chứa token RIÊNG của kính (dòng đầu); thắng biến môi trường/.env, thua --token")
    p.add_argument("--device", default="", help="mã kính hiện tại (mặc định: VISIONCARE_DEVICE_ID / .env)")
    args = p.parse_args()

    Handler.device_token, Handler.device_id = demo_credentials(args.token, args.device, token_file=args.token_file)
    Handler.upstream = urlsplit(args.upstream)
    if not Handler.upstream.hostname:
        sys.exit(f"--upstream không hợp lệ: {args.upstream}")

    shown_host = "localhost"
    if args.lan:
        args.host = "0.0.0.0"
        shown_host = lan_ip()
        if not args.cert:
            args.cert, args.key = self_signed_cert(shown_host)
    elif args.host not in ("127.0.0.1", "0.0.0.0"):
        shown_host = args.host
    port = args.port or (8443 if args.cert else 8080)

    scheme = "http"
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)
        httpd = TLSThreadingHTTPServer((args.host, port), Handler)
        httpd.tls_context = ctx
        scheme = "https"
    else:
        httpd = ThreadingHTTPServer((args.host, port), Handler)
    print(f"Trang demo : {scheme}://{shown_host}:{port}")
    print(f"Server kính: {args.upstream}  (qua /api/*)")
    print(f"Mã kính    : {Handler.device_id}")
    print("Token      : đã đọc sẵn, proxy tự gắn vào WS (không gửi xuống trình duyệt)" if Handler.device_token
          else "Token      : CHƯA có (không thấy VISIONCARE_DEVICE_CLIENT_TOKEN), nhập ở Cài đặt của từng trang")
    if args.lan:
        print("\nTrên máy client: mở địa chỉ trên, trình duyệt sẽ cảnh báo chứng chỉ tự ký —")
        print("bấm 'Nâng cao' rồi 'Tiếp tục truy cập', sau đó cho phép camera/micro.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
