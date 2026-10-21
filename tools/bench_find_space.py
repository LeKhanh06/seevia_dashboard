"""Bàn đo chống ảo giác cho TÌM ĐỒ và TẢ CẢNH (2026-10-22), trên ảnh VizWiz val mô phỏng kính (cắt 4:3, 800x600).

find : mỗi ảnh hai lệnh — vật CÓ trong ảnh (lấy từ đáp án người của câu "cái này là gì") và vật KHÔNG có (vật của ảnh khác).
       Có mà từ chối = bỏ sót; không có mà vẫn chỉ hướng = bịa. Phát hiện từ chối bằng regex "không thấy rõ".
space: dùng LLM chấm MỖI mô tả với 5 chú thích người (không thấy ảnh): số khẳng định mâu thuẫn chú thích / vật-chữ-số cụ thể
       không có trong chú thích. Chỉ có ý nghĩa SO SÁNH GIỮA HAI PROMPT trên cùng ảnh (chú thích ngắn nên "không có trong
       chú thích" chưa chắc là bịa).

    python tools/bench_find_space.py find  --prompts-dir <thư mục chứa find_object.py/describe_space.py cũ> --out x.json
Không có --prompts-dir = prompt hiện tại của worktree; có = nạp file prompt cũ để so (một biến: chỉ đổi prompt).
"""

import argparse
import asyncio
import importlib
import importlib.util
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench_lane_a import EVAL, VZ, cam_jpeg  # noqa: E402,F401
from bench import common  # noqa: E402
from bench import llm_common as L  # noqa: E402
from bench import vqa_vizwiz as V  # noqa: E402

REFUSE = re.compile(r"không thấy rõ|không thấy|chưa thấy|không nhìn thấy|không có trong ảnh", re.I)


def load_prompt(prompts_dir: str | None, name: str):
    if not prompts_dir:
        return importlib.import_module(f"handlers.{name}")
    spec = importlib.util.spec_from_file_location(f"old_{name}", os.path.join(prompts_dir, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def translate_all(words: list[str], cache_path: str) -> dict[str, str]:
    from models import llm
    tr = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    for w in words:
        if w in tr:
            continue
        r = await llm.complete([llm.user_message(
            f'Dịch tên đồ vật sau sang tiếng Việt, chỉ trả về cụm danh từ ngắn (không giải thích): "{w}"')], max_tokens=20, temperature=0.0)
        tr[w] = r.strip().strip('".')
    json.dump(tr, open(cache_path, "w"), ensure_ascii=False, indent=1)
    return tr


def pick_objects(n: int):
    allv = json.load(open(os.path.join(VZ, "vqa", "val.json"), encoding="utf-8"))
    rows = []
    for it in allv:
        if it["answerable"] != 1 or it.get("answer_type") != "other":
            continue
        q = it["question"].lower()
        if not (q.startswith("what is this") or q.startswith("what is it") or q.startswith("what's this")):
            continue
        ans = [a["answer"].lower() for a in it["answers"] if a["answer"] not in ("unanswerable", "unsuitable")]
        if not ans:
            continue
        top = max(set(ans), key=ans.count)
        if ans.count(top) >= 5 and len(top.split()) <= 3:
            rows.append((it["image"], top))
    rng = np.random.default_rng(3)
    rng.shuffle(rows)
    return rows[:n]


async def run_find(a):
    from lanes import general
    mod = load_prompt(a.prompts_dir, "find_object")
    general.find_object._PROMPT = mod._PROMPT
    rows = pick_objects(a.n)
    tr = await translate_all([o for _, o in rows], os.path.join(os.path.dirname(a.out), "find_obj_vi.json"))
    absent = [(img, rows[(i + 7) % len(rows)][1]) for i, (img, _) in enumerate(rows)]
    jobs = [("present", img, tr[o]) for img, o in rows] + [("absent", img, tr[o]) for img, o in absent]
    sem = asyncio.Semaphore(a.conc)
    out = []

    async def one(kind, img, obj):
        jpeg = await asyncio.to_thread(cam_jpeg, os.path.join(VZ, "val", img), 800)
        text = f"{obj} ở đâu"
        async with sem:
            r = await L.timed_stream(lambda: general.answer("find", text, jpeg))
        out.append({"kind": kind, "img": img, "obj": obj, "text": r["text"], "refused": bool(REFUSE.search(r["text"])),
                    "ttft": r.get("ttft_ms"), "total": r.get("total_ms"), "words": len(r["text"].split())})

    await asyncio.gather(*(one(*j) for j in jobs))
    pres = [x for x in out if x["kind"] == "present"]
    absn = [x for x in out if x["kind"] == "absent"]
    m = {"n_present": len(pres), "present_refused": float(np.mean([x["refused"] for x in pres])),
         "n_absent": len(absn), "absent_located(bịa nếu vật thật sự vắng)": float(np.mean([not x["refused"] for x in absn])),
         "words": float(np.mean([x["words"] for x in out])),
         "ttft": common.latency_summary([x["ttft"] for x in out]), "total": common.latency_summary([x["total"] for x in out])}
    json.dump({"metrics": m, "per": out}, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(m, ensure_ascii=False, indent=1))


async def run_space(a):
    from lanes import general
    from models import llm
    mod = load_prompt(a.prompts_dir, "describe_space")
    general.describe_space._PROMPT = mod._PROMPT
    cap = json.load(open(os.path.join(VZ, "annotations", "val.json")))
    imgs = {i["id"]: i["file_name"] for i in cap["images"]}
    caps: dict[str, list[str]] = {}
    for x in cap["annotations"]:
        if not x.get("is_rejected") and not x.get("is_precanned"):
            caps.setdefault(imgs[x["image_id"]], []).append(x["caption"])
    names = sorted(k for k, v in caps.items() if len(v) >= 3)
    names = common.sample(names, a.n, seed=11)
    sem = asyncio.Semaphore(a.conc)
    out = []
    JP = ("Bạn chấm một MÔ TẢ ẢNH tiếng Việt dành cho người mù, đối chiếu với các CHÚ THÍCH ngắn tiếng Anh do người thật viết "
          "(người nhìn ảnh).\nChú thích:\n{caps}\n\nMô tả:\n{desc}\n\nTrả JSON: \"claims\": tổng số khẳng định cụ thể (vật, người, "
          "chữ, số, màu, vị trí) trong mô tả; \"contradict\": số khẳng định MÂU THUẪN trực tiếp với chú thích; "
          "\"unsupported\": số khẳng định cụ thể về vật/chữ/số/thương hiệu KHÔNG hề xuất hiện trong chú thích (kể cả khi không mâu thuẫn).")
    SCH = {"type": "object", "properties": {"claims": {"type": "integer"}, "contradict": {"type": "integer"}, "unsupported": {"type": "integer"}},
           "required": ["claims", "contradict", "unsupported"]}

    async def one(name):
        jpeg = await asyncio.to_thread(cam_jpeg, os.path.join(VZ, "val", name), 800)
        async with sem:
            r = await L.timed_stream(lambda: general.answer("space", "Trước mặt tôi có gì", jpeg))
            if "error" in r:
                return
            try:
                j = await llm.generate_json(JP.format(caps="\n".join(f"- {c}" for c in caps[name][:5]), desc=r["text"]), SCH, max_tokens=60)
            except Exception:  # noqa: BLE001
                return
        out.append({"img": name, "text": r["text"], "words": len(r["text"].split()), "ttft": r["ttft_ms"], "total": r["total_ms"], **{k: int(j.get(k, 0)) for k in ("claims", "contradict", "unsupported")}})

    await asyncio.gather(*(one(n) for n in names))
    cl = sum(x["claims"] for x in out)
    m = {"n": len(out), "words": float(np.mean([x["words"] for x in out])), "claims_per_desc": cl / len(out),
         "contradict_rate": sum(x["contradict"] for x in out) / max(cl, 1), "unsupported_rate": sum(x["unsupported"] for x in out) / max(cl, 1),
         "desc_with_contradiction": float(np.mean([x["contradict"] > 0 for x in out])),
         "ttft": common.latency_summary([x["ttft"] for x in out]), "total": common.latency_summary([x["total"] for x in out])}
    json.dump({"metrics": m, "per": out}, open(a.out, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(m, ensure_ascii=False, indent=1))


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task", choices=["find", "space"])
    ap.add_argument("--prompts-dir", default=None)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--conc", type=int, default=4)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from models import llm
    await (run_find if a.task == "find" else run_space)(a)
    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
