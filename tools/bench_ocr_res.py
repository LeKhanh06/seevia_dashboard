"""Bàn đo độ phân giải ảnh cho đọc chữ (2026-10-22): mô phỏng ảnh kính (cắt 4:3 -> thu về cỡ OV2640 -> JPEG), đọc bằng ĐÚNG
đường production `ocr_live.read_image(..., "raw")`, chấm bằng `bench.ocr_vintext.score` (số sai / bỏ sót, BoW F1, CER).

Chỉ dùng ảnh VinText có cạnh 4:3 >= 1600 (nếu không UXGA chỉ là phóng to, không có thêm chi tiết). GT được lọc theo vùng cắt.

    cd <worktree> && SEEVIA_VH_DIR=$PWD EVAL_DIR=<SeeVia_system/evaluations> python tools/bench_ocr_res.py \
        --variants svga,xga,sxga,uxga --n 120 --conc 4 --out /tmp/res.jsonl [--prompt-file p.txt]

Biến thể: `<tên cỡ>` = ảnh nguyên khung thu về cỡ đó; `<tên cỡ>+crop` = cắt vùng chữ (PP-OCR det trên bản svga) từ ảnh uxga
rồi gửi vùng cắt ở độ phân giải gốc của sensor. Mỗi dòng ghi: byte JPEG, TTFT, tổng, đầu ra, điểm.
"""

import argparse
import asyncio
import glob
import json
import os
import re
import sys
import time

import cv2
import numpy as np

EVAL = os.environ.get("EVAL_DIR", "/workingspace_aiclub/WorkingSpace/Personal/vannk/SeeVia_system/evaluations")
sys.path.insert(0, EVAL)
os.environ.setdefault("SEEVIA_VH_DIR", os.getcwd())
from bench import common  # noqa: E402

common.setup(gpu=False)
from bench import llm_common as L  # noqa: E402
from bench.ocr_vintext import parse_gt, score  # noqa: E402

VT = os.path.join(common.DATAS, "vintext", "vietnamese")
SIZES = {"qvga": (320, 240), "vga": (640, 480), "svga": (800, 600), "xga": (1024, 768), "sxga": (1280, 960), "uxga": (1600, 1200)}


def crop43(img: np.ndarray) -> tuple[np.ndarray, int, int]:
    H, W = img.shape[:2]
    if W / H > 4 / 3:
        nw = int(H * 4 / 3)
        x0 = (W - nw) // 2
        return img[:, x0:x0 + nw], x0, 0
    nh = int(W * 3 / 4)
    y0 = (H - nh) // 2
    return img[y0:y0 + nh], 0, y0


def jpg(img, q=70) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


def candidates() -> list[tuple[str, str]]:
    from PIL import Image
    out = []
    for d in ("test_image", "unseen_test_images", "train_images"):
        for p in sorted(glob.glob(os.path.join(VT, d, "*.jpg"))):
            m = re.search(r"im(\d+)\.jpg", p)
            lab = os.path.join(VT, "labels", f"gt_{m.group(1)}.txt") if m else ""
            if not lab or not os.path.exists(lab):
                continue
            w, h = Image.open(p).size
            if min(w, h * 4 / 3) >= 1600:
                out.append((p, lab))
    return out


def gt_in_crop(lab: str, x0: int, y0: int, cw: int, ch: int, scale: float = 1.0) -> list[dict]:
    boxes = []
    for b in parse_gt(lab):
        if x0 <= b["cx"] < x0 + cw and y0 <= b["cy"] < y0 + ch:
            boxes.append({**b, "cx": (b["cx"] - x0) * scale, "cy": (b["cy"] - y0) * scale, "h": b["h"] * scale})
    return boxes


def make(path: str, variant: str, det=None) -> bytes:
    """JPEG gửi Gemma. `<cỡ>` = nguyên khung; `+crop` = cắt vùng chữ (dò trên bản svga, lề 4 %); `+cropup` = cắt rồi phóng
    to (bicubic) tới cạnh dài 1280 (tối đa x2,5) — không tốn thêm byte mạng nếu cắt từ ảnh lượt."""
    img = cv2.imread(path)
    full, _, _ = crop43(img)
    name, _, mod = variant.partition("+")
    name, _, blur = name.partition("~")  # `svga~2` = làm mờ Gauss sigma 2 px SAU khi thu về cỡ (rung tay / mất nét)
    w, h = SIZES[name]
    small = cv2.resize(full, (w, h), interpolation=cv2.INTER_AREA)
    if blur:
        small = cv2.GaussianBlur(small, (0, 0), float(blur))
    if not mod:
        return jpg(small)
    s8 = cv2.resize(full, SIZES["svga"], interpolation=cv2.INTER_AREA)
    boxes = det.detect(cv2.cvtColor(s8, cv2.COLOR_BGR2GRAY))
    if not boxes:
        return jpg(small)
    m = int(0.04 * 800)
    x0 = max(0, min(b[0] for b in boxes) - m); y0 = max(0, min(b[1] for b in boxes) - m)
    x1 = min(800, max(b[2] for b in boxes) + m); y1 = min(600, max(b[3] for b in boxes) + m)
    sx = w / 800
    roi = small[int(y0 * sx):int(y1 * sx), int(x0 * sx):int(x1 * sx)]
    if mod == "cropup":
        k = min(2.5, 1280 / max(roi.shape[:2]))
        if k > 1.05:
            roi = cv2.resize(roi, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    return jpg(roi)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="svga,uxga")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt-file", default=None, help="thay prompt raw (so sánh prompt)")
    ap.add_argument("--q", type=int, default=70)
    a = ap.parse_args()

    from lanes import ocr_live
    from live import text_align
    from models.ocr import prompts
    from models import llm
    if a.prompt_file:
        text = open(a.prompt_file, encoding="utf-8").read().strip()
        prompts._RAW = text
    det = text_align.detector() if any("crop" in v for v in a.variants.split(",")) else None
    items = candidates()[: a.n]
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["img"], r["variant"]))
    sem = asyncio.Semaphore(a.conc)
    fo = open(a.out, "a", encoding="utf-8")

    async def one(path, lab, variant):
        key = (os.path.basename(path), variant)
        if key in done:
            return
        img = cv2.imread(path)
        full, x0, y0 = crop43(img)
        jpeg = await asyncio.to_thread(make, path, variant, det)
        gt = gt_in_crop(lab, x0, y0, full.shape[1], full.shape[0], 1.0)  # luôn chấm với GT cả khung: đọc thiếu vùng bị cắt là mất điểm
        def factory():
            return ocr_live.read_image(jpeg, "raw", "bench")
        r = await L.timed_stream(factory)
        row = {"img": key[0], "variant": variant, "bytes": len(jpeg), "text": r["text"], "ttft_ms": r.get("ttft_ms"),
               "total_ms": r.get("total_ms"), "error": r.get("error"), "n_gt": len(gt)}
        if "error" not in r and gt:
            row.update({k: v for k, v in score(r["text"], gt).items() if k != "gt_text"})
        fo.write(json.dumps(row, ensure_ascii=False) + "\n")
        fo.flush()

    queue: asyncio.Queue = asyncio.Queue()
    for p, l in items:
        for v in a.variants.split(","):
            queue.put_nowait((p, l, v))

    async def worker():
        while True:
            try:
                p, l, v = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await one(p, l, v)

    await asyncio.gather(*(worker() for _ in range(a.conc)))
    fo.close()
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
