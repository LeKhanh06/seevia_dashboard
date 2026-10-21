# ruff: noqa: ANN401, ASYNC240, ASYNC110, C901, E501, S105, T201, PLR0913  # công cụ vận hành stack thật
"""Dựng dữ liệu gọi/nhắn cho stack THẬT (AB cổng 18001): chủ kính, ghép kính, mời người thân.

Idempotent, chạy lại bao nhiêu lần cũng được. Mật khẩu sinh ngẫu nhiên, lưu ở ``--accounts``
(mặc định ``~/.config/seevia/live_accounts.json``, quyền 600); KHÔNG in mật khẩu/token ra màn hình.

    # 1) chủ kính + ghép kính (--unlink-old: bỏ ghép user-100 cũ nếu kính còn gắn với nó)
    python scripts/live_calls_setup.py owner --ab-log ~/.local/state/seevia/ab.log --unlink-old user-100
    # 2) sau khi relative_sim đã chạy (nó tự đăng ký "em iu"): mời người thân
    python scripts/live_calls_setup.py invite
    # 3) xem trạng thái
    python scripts/live_calls_setup.py status

OTP (AB không có SMS, ghi vào log): ``grep "Demo OTP for" ~/.local/state/seevia/ab.log | tail -1``.
Token Device API đọc từ ``DEVICE_API_TOKEN`` (môi trường hoặc ``--env-file``), không in.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

import httpx

DEFAULT_ACCOUNTS = Path.home() / ".config" / "seevia" / "live_accounts.json"
DEFAULT_ENV = Path(__file__).resolve().parents[3] / ".env"
DEFAULTS = {
    "owner": {"phone": "0988000001", "display_name": "Chủ kính"},
    "relative": {"phone": "0988000002", "display_name": "em iu"},
}
ALIASES = ["em iu", "em yêu"]
_OTP_RE = re.compile(r"Demo OTP for (\S+): (\d+)")


def _digits(value: str) -> str:
    return "".join(c for c in value if c.isdigit())


def load_accounts(path: Path) -> dict:
    """Đọc file tài khoản; thiếu thì dựng mặc định với mật khẩu ngẫu nhiên (alnum, dễ gõ qua adb)."""
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for role, base in DEFAULTS.items():
        entry = data.setdefault(role, {})
        for key, value in base.items():
            entry.setdefault(key, value)
        entry.setdefault("password", "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(14)))
    save_accounts(path, data)
    return data


def save_accounts(path: Path, data: dict) -> None:
    """Ghi nguyên tử, quyền 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(path)


def device_token(env_file: Path) -> str:
    """``DEVICE_API_TOKEN`` từ môi trường hoặc ``.env`` (chỉ đọc đúng khoá này)."""
    if value := os.environ.get("DEVICE_API_TOKEN"):
        return value
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip().removeprefix("export ").lstrip()
            if stripped.startswith("DEVICE_API_TOKEN="):
                return stripped.split("=", 1)[1].strip().strip("\"'")
    raise SystemExit("Thiếu DEVICE_API_TOKEN (env hoặc --env-file)")


def otp_from_log(ab_log: Path, offset: int, phone: str) -> str:
    """Mã OTP mới nhất của ``phone`` ghi vào log AB sau ``offset``."""
    end = time.time() + 15
    while time.time() < end:
        if ab_log.exists():
            text = ab_log.read_bytes()[offset:].decode("utf-8", "replace")
            found = [c for p, c in _OTP_RE.findall(text) if _digits(p).endswith(_digits(phone)[-9:])]
            if found:
                return found[-1]
        time.sleep(0.2)
    raise SystemExit(f"không thấy OTP của {phone} trong {ab_log}")


def ensure_account(http: httpx.Client, acc: dict, ab_log: Path | None) -> dict:
    """Đăng nhập; chưa có thì đăng ký + OTP (đọc từ log AB). Trả dữ liệu phiên (có access_token)."""
    offset = ab_log.stat().st_size if ab_log and ab_log.exists() else 0
    resp = http.post("/api/v1/auth/login", json={"phone_number": acc["phone"], "password": acc["password"]})
    if resp.status_code == httpx.codes.OK:
        return resp.json()["data"]
    reg = http.post(
        "/api/v1/auth/register",
        json={"phone_number": acc["phone"], "password": acc["password"], "display_name": acc["display_name"]},
    )
    if reg.status_code == httpx.codes.CONFLICT:  # đã có nhưng mật khẩu khác / chưa xác minh
        raise SystemExit(f"{acc['phone']} đã tồn tại với mật khẩu khác: xoá tay hoặc sửa live_accounts.json")
    reg.raise_for_status()
    if ab_log is None:
        raise SystemExit("tài khoản mới cần --ab-log để đọc OTP")
    code = otp_from_log(ab_log, offset, acc["phone"])
    ver = http.post("/api/v1/auth/otp/verify", json={"phone_number": acc["phone"], "otp_code": code})
    ver.raise_for_status()
    return ver.json()["data"]


def cmd_owner(args: argparse.Namespace) -> None:
    accounts = load_accounts(args.accounts)
    owner = accounts["owner"]
    dev_hdr = {"Authorization": f"Bearer {device_token(args.env_file)}"}
    with httpx.Client(base_url=args.app_url, timeout=15) as http:
        session = ensure_account(http, owner, args.ab_log)
        owner["public_user_id"] = session["public_user_id"]
        save_accounts(args.accounts, accounts)
        link = {"user_id": owner["public_user_id"], "device_id": args.glasses_id}
        resp = http.post("/api/v1/device/glasses/link", json=link, headers=dev_hdr)
        if resp.status_code == httpx.codes.CONFLICT and args.unlink_old:
            un = http.post("/api/v1/device/glasses/unlink", json={"user_id": args.unlink_old}, headers=dev_hdr)
            print(f"bỏ ghép {args.unlink_old}: HTTP {un.status_code} {un.json().get('data')}")
            resp = http.post("/api/v1/device/glasses/link", json=link, headers=dev_hdr)
        print(f"ghép kính {args.glasses_id} -> chủ {owner['phone']} ({owner['public_user_id']}): HTTP {resp.status_code}")
        resp.raise_for_status()


def cmd_invite(args: argparse.Namespace) -> None:
    accounts = load_accounts(args.accounts)
    owner, rel = accounts["owner"], accounts["relative"]
    with httpx.Client(base_url=args.app_url, timeout=15) as http:
        session = ensure_account(http, owner, args.ab_log)
        hdr = {"Authorization": f"Bearer {session['access_token']}"}
        links = http.get("/api/v1/care/links", headers=hdr).json()["data"]["links"]
        mine = [x for x in links if x["role"] == "owner" and x["peer_phone_number"].endswith(_digits(rel["phone"])[-9:])]
        if not mine:
            inv = http.post(
                "/api/v1/care/invite",
                headers=hdr,
                json={
                    "phone_number": rel["phone"],
                    "display_name": rel["display_name"],
                    "aliases": ALIASES,
                    "emergency": True,
                    "priority": 0,
                },
            )
            print(f"mời {rel['display_name']} ({rel['phone']}): HTTP {inv.status_code}")
            inv.raise_for_status()
        end = time.time() + 20
        while time.time() < end:
            links = http.get("/api/v1/care/links", headers=hdr).json()["data"]["links"]
            if any(x["role"] == "owner" and x["status"] == "active" for x in links):
                break
            time.sleep(0.5)
        cmd_status(args)


def cmd_status(args: argparse.Namespace) -> None:
    accounts = load_accounts(args.accounts)
    owner = accounts["owner"]
    with httpx.Client(base_url=args.app_url, timeout=15) as http:
        session = ensure_account(http, owner, args.ab_log)
        hdr = {"Authorization": f"Bearer {session['access_token']}"}
        links = http.get("/api/v1/care/links", headers=hdr).json()["data"]["links"]
    print(f"chủ kính: {owner['display_name']} {owner['phone']} ({session['public_user_id']})")
    for x in links:
        print(
            f"  care_link {x['role']}: {x['display_name']} {x['peer_phone_number']} status={x['status']} "
            f"emergency={x['emergency']} priority={x['priority']} aliases={x['aliases']}"
        )
    if not links:
        print("  (chưa có care_link)")


def main() -> None:
    """Điểm vào CLI."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["owner", "invite", "status"])
    ap.add_argument("--app-url", default="http://127.0.0.1:18001")
    ap.add_argument("--ab-log", type=Path, default=Path.home() / ".local/state/seevia/ab.log")
    ap.add_argument("--accounts", type=Path, default=DEFAULT_ACCOUNTS)
    ap.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    ap.add_argument("--glasses-id", default="glasses-123")
    ap.add_argument("--unlink-old", default=None, help="user_id cũ đang giữ kính (vd user-100) để bỏ ghép khi bị xung đột")
    args = ap.parse_args()
    {"owner": cmd_owner, "invite": cmd_invite, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
