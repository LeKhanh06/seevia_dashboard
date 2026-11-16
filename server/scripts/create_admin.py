"""Create a dashboard admin account (role ``admin_readonly``).

The password is read from stdin (hidden prompt on a terminal), never from argv,
so it does not land in shell history or ``ps``. Uses ``DATABASE_URL``.

    DATABASE_URL=postgresql+asyncpg://... uv run python scripts/create_admin.py \
        --email admin@example.com --name "Khánh Hà"
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import TextIO

from app.database import build_async_engine, build_session_factory
from app.errors import AdminApiError
from app.services.admin_auth import AdminAuthService, LoginThrottle, admin_public_id


def read_password(stream: TextIO) -> str:
    """Read the password: hidden prompt on a TTY, else the first line of ``stream``."""
    if stream.isatty():
        first = getpass.getpass("Mật khẩu: ")
        if getpass.getpass("Nhập lại: ") != first:
            msg = "Hai lần nhập không khớp."
            raise ValueError(msg)
        return first
    return stream.readline().rstrip("\r\n")


async def create(database_url: str, email: str, name: str, password: str) -> str:
    """Insert the admin and return its display id (``AD-0001``)."""
    engine = build_async_engine(database_url)
    try:
        async with build_session_factory(engine)() as session:
            service = AdminAuthService(session, ttl_s=3600, throttle=LoginThrottle())
            admin = await service.create_admin(email=email, display_name=name, password=password)
            return admin_public_id(admin)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description="Tạo tài khoản quản trị dashboard.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="Tên hiển thị")
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.stderr.write("Thiếu DATABASE_URL.\n")
        return 2
    try:
        password = read_password(stdin or sys.stdin)
        admin_id = asyncio.run(create(database_url, args.email, args.name, password))
    except (ValueError, AdminApiError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(f"Đã tạo {admin_id}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
