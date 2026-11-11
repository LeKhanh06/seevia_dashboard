"""Bàn đo làn A (hỏi ảnh / tả cảnh) cho chống ảo giác + cỡ ảnh gửi Gemma (2026-10-22).

Dùng lại phần chấm của `bench.vqa_vizwiz` (LLM chấm: từ chối / khớp đáp án người; câu hỏi dịch sẵn): VizWiz-VQA val,
`--split dev` = 100 câu riêng, `--split test` = 300 câu của bàn đo chính. Ảnh mô phỏng kính: cắt giữa 4:3 -> 800x600 (ảnh
lượt SVGA) -> thu về cạnh dài `--side` (mặc định 800 = không thu) -> JPEG.

    cd <worktree> && python tools/bench_lane_a.py --intent ask --split dev --variant base --out x.json
Biến thể (`--variant`): `base` = prompt/nhiệt độ đang chạy; `temp0` = nhiệt độ 0; `honest` = thêm luật "chỉ nói điều thấy rõ" vào
system prompt làn A. Mỗi lần chạy đúng MỘT thay đổi so với `base` (quy tắc: một biến mỗi thí nghiệm).
"""

import argparse
import asyncio
import json
import os
import sys
import time

import cv2
import numpy as np

EVAL = os.environ.get("EVAL_DIR", "/data/work/luanhh/local/SeeVia_system/evaluations")
sys.path.insert(0, EVAL)
os.environ.setdefault("SEEVIA_VH_DIR", os.getcwd())
from bench import common  # noqa: E402

common.setup(gpu=False)
from bench import llm_common as L  # noqa: E402
from bench import vqa_vizwiz as V  # noqa: E402

VZ = V.VZ


def cam_jpeg(path: str, side: int, q: int = 70) -> bytes:
    img = cv2.imread(path)
    H, W = img.shape[:2]
    if W / H > 4 / 3:
        nw = int(H * 4 / 3)
        img = img[:, (W - nw) // 2:(W - nw) // 2 + nw]
    else:
        nh = int(W * 3 / 4)
        img = img[(H - nh) // 2:(H - nh) // 2 + nh]
    img = cv2.resize(img, (800, 600), interpolation=cv2.INTER_AREA)
    if side < 800:
        img = cv2.resize(img, (side, side * 3 // 4), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()


HONEST = (
    "Người nghe không kiểm lại được ảnh nên chỉ nói điều THẤY RÕ trong ảnh. Không đoán chữ, số, nhãn hiệu, màu, kích thước "
    "hay khoảng cách khi không chắc; phần nào không thấy rõ thì nói thẳng \"không thấy rõ ...\" rồi dừng, kèm một cách sửa "
    "ngắn (đưa lại gần, giữ yên, bật đèn)."
)


HONEST_TEXT = (
    "Chữ, số, tên thương hiệu, tên sách, mã: chỉ đọc khi nhìn rõ từng ký tự; chữ mờ, nhỏ, bị che hoặc bị cắt thì nói "
    "\"không đọc rõ ...\" thay vì đoán. Còn vật, màu, vị trí thì cứ nói theo điều đang thấy."
)


import contextvars  # noqa: E402

CUR: contextvars.ContextVar = contextvars.ContextVar("cur", default="")
LP: dict[str, list] = {}


def apply_variant(name: str):
    """Patch tại chỗ; trả hàm khôi phục."""
    from lanes import general
    from models import llm
    saved = (general._VISION_SYSTEM, llm.stream_chat)
    if os.environ.get("BENCH_LOGPROBS"):
        orig0 = llm.stream_chat

        def with_lp(messages, max_tokens=None, on_logprobs=None, **extra):
            def cb(items):
                LP.setdefault(CUR.get(), []).extend((it.get("token", ""), it["logprob"]) for it in items if it.get("logprob") is not None)
                if on_logprobs:
                    on_logprobs(items)

            return orig0(messages, max_tokens, cb, **extra)

        llm.stream_chat = with_lp
    if name == "temp0":
        orig = llm.stream_chat

        def temp0(messages, max_tokens=None, on_logprobs=None, **extra):
            extra.setdefault("temperature", 0.0)
            return orig(messages, max_tokens, on_logprobs, **extra)

        llm.stream_chat = temp0
    elif name == "honest":
        general._VISION_SYSTEM = general._VISION_SYSTEM + " " + HONEST
    elif name == "honest_text":
        general._VISION_SYSTEM = general._VISION_SYSTEM + " " + HONEST_TEXT
    elif name != "base":
        raise SystemExit(f"biến thể lạ: {name}")

    def restore():
        general._VISION_SYSTEM, llm.stream_chat = saved

    return restore


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", default="ask")
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--variant", default="base")
    ap.add_argument("--side", type=int, default=800)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    import config
    from lanes import general
    from models import llm

    restore = apply_variant(a.variant)
    allv = json.load(open(os.path.join(VZ, "vqa", "val.json"), encoding="utf-8"))
    items = common.sample(allv, 300, seed=0)
    if a.split == "dev":
        test_imgs = {it["image"] for it in items}
        items = common.sample([it for it in allv if it["image"] not in test_imgs], 100, seed=1)
    if a.limit:
        items = items[: a.limit]
    tr = json.load(open(os.path.join(V.OUT_DIR, "questions_vi.json" if a.split == "test" else "questions_vi_dev.json"), encoding="utf-8"))
    items = [it for it in items if tr.get(it["image"])]

    sem = asyncio.Semaphore(a.conc)
    out: dict[str, dict] = {}

    async def one(it):
        q = tr[it["image"]]
        jpeg = await asyncio.to_thread(cam_jpeg, os.path.join(VZ, "val", it["image"]), a.side)
        async with sem:
            CUR.set(it["image"])
            LP.pop(it["image"], None)
            r = await L.timed_stream(lambda: general.answer(a.intent, q, jpeg))
        out[it["image"]] = {**r, "bytes": len(jpeg), "lp": LP.get(it["image"])}

    await asyncio.gather(*(one(it) for it in items))
    jcache = L.Cache("vqa_vizwiz_anh", L.cfg_hash({"judge": V.JUDGE_PROMPT, "schema": V.JUDGE_SCHEMA, "variant": a.variant,
                                                  "intent": a.intent, "side": a.side, "sys": general._VISION_SYSTEM}),
                     name="judge_anh.jsonl")
    per = []
    jsem = asyncio.Semaphore(a.conc)

    async def jone(it):
        r = out[it["image"]]
        if "error" in r:
            return None
        async with jsem:
            j = await V.judge(it, tr[it["image"]], r["text"], jcache)
        if j["refused"] is None:
            return None
        acc, _ = V.vqa_acc(it, j)
        return {"image": it["image"], "answerable": int(it["answerable"]), "refused": bool(j["refused"]), "vqa_acc": acc,
                "text": r["text"], "words": len(r["text"].split()), "ttft_ms": r["ttft_ms"], "total_ms": r["total_ms"],
                "bytes": r["bytes"], "q": tr[it["image"]], "lp": r.get("lp")}

    per = [x for x in await asyncio.gather(*(jone(it) for it in items)) if x]
    una = [x for x in per if x["answerable"] == 0]
    ans = [x for x in per if x["answerable"] == 1]
    A = lambda rows, f: np.array([f(x) for x in rows], dtype=float)  # noqa: E731
    m = {
        "n": len(per), "n_unans": len(una), "n_ans": len(ans),
        "vqa_acc_all": L.ci_entry(A(per, lambda x: x["vqa_acc"])),
        "vqa_acc_answerable": L.ci_entry(A(ans, lambda x: x["vqa_acc"])),
        "fabricate_unans": L.ci_entry(A(una, lambda x: not x["refused"])),
        "refuse_answerable": L.ci_entry(A(ans, lambda x: x["refused"])),
        "words_mean": float(np.mean([x["words"] for x in per])),
        "bytes_mean": float(np.mean([x["bytes"] for x in per])),
        "ttft": common.latency_summary([x["ttft_ms"] for x in per]),
        "total": common.latency_summary([x["total_ms"] for x in per]),
    }
    json.dump({"args": vars(a), "metrics": m, "per": per, "time": time.strftime("%F %T")}, open(a.out, "w"), ensure_ascii=False, indent=1)
    for k, v in m.items():
        print(k, v if not isinstance(v, dict) or "ci" not in v else f"{v['value']:.3f} [{v['ci'][0]:.3f},{v['ci'][1]:.3f}] n={v['n']}")
    restore()
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
