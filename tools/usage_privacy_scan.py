"""Quét riêng tư cho thống kê sử dụng: spool sự kiện của VH + dòng log của bộ thống kê.

    python -m tools.usage_privacy_scan <spool_dir> <log_file> [--device glasses-123] [--pattern "em iu"] ...

Tìm trong MỌI trường của sự kiện (trừ `device_id`: AB cần mã kính thô để tra chủ kính rồi băm, không lưu thô) và
trong các dòng log của logger `blind_assist.usage`: mã kính thô, số điện thoại, số tiền, chuỗi mẫu riêng tư dùng
trong e2e (tên người thân, chữ OCR mẫu...). Log lượt thường (câu nghe được) không thuộc phạm vi thống kê nên bỏ qua.
In từng chỗ vi phạm `<vị trí>:<loại>:<chi tiết>`; mã thoát 1 khi có vi phạm.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)\d{9}(?!\d)")
_MONEY = re.compile(r"\d+\s*(?:nghìn|ngàn|triệu)?\s*(?:đồng|vnd|vnđ)|\d{1,3}(?:[.,]000)+(?!\d)", re.IGNORECASE)
_USAGE_LOG = "blind_assist.usage"


def _values(obj: object, skip_device: bool = True) -> list[str]:
    if isinstance(obj, dict):
        return [v for k, val in obj.items() if not (skip_device and k == "device_id") for v in _values(val, False)]
    if isinstance(obj, list):
        return [v for item in obj for v in _values(item, False)]
    return [] if obj is None else [str(obj)]


def _check(where: str, text: str, devices: list[str], patterns: list[str]) -> list[str]:
    hits = [f"{where}:device:{d}" for d in devices if d and d in text]
    hits += [f"{where}:pattern:{p}" for p in patterns if p and p.lower() in text.lower()]
    if _PHONE.search(text):
        hits.append(f"{where}:phone:{_PHONE.search(text).group(0)[:4]}…")
    if _MONEY.search(text):
        hits.append(f"{where}:money:{_MONEY.search(text).group(0)}")
    return hits


def scan(spool_dir: Path, log_file: Path | None, devices: list[str] = (), patterns: list[str] = ()) -> list[str]:
    """Danh sách vi phạm (rỗng = sạch)."""
    devices, patterns = list(devices), list(patterns)
    hits: list[str] = []
    for path in sorted(Path(spool_dir).glob("*.jsonl")):
        for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            for value in _values(event):
                hits += _check(f"{path.name}#{no}", value, devices, patterns)
    if log_file is not None and Path(log_file).exists():
        for no, line in enumerate(Path(log_file).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _USAGE_LOG in line:
                hits += _check(f"{Path(log_file).name}#{no}", line, devices, patterns)
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("spool_dir", type=Path)
    ap.add_argument("log_file", type=Path)
    ap.add_argument("--device", action="append", default=[], help="mã kính thô không được xuất hiện (lặp được)")
    ap.add_argument("--pattern", action="append", default=[], help="chuỗi riêng tư mẫu (lặp được)")
    args = ap.parse_args(argv)
    hits = scan(args.spool_dir, args.log_file, args.device, args.pattern)
    for hit in hits:
        print(hit)
    print(f"{len(hits)} vi phạm")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
