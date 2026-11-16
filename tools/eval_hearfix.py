"""Đo ghép đôi TRƯỚC/SAU của `pipeline/hearfix.py` trên bàn đo STT (`stt-bench-cache`).

    ~/.venvs/seevia/bin/python -m tools.eval_hearfix --cache ../stt-bench-cache [--cfg bothhw2.0_4] [--sweep]

Cùng bộ clip, cùng bản ghi STT (`results/<cfg>.jsonl`, mặc định cấu hình đang chạy: beam + hotword), cùng nhãn Gemma đã cache
(`intent_cache.json`, cùng khoá bản ghi + bối cảnh thiết bị như `bench_stt_report`):
- TRƯỚC = nhãn Gemma; SAU = nhãn Gemma, trừ khi Gemma cho `chat` mà hearfix có gợi ý (act/confirm/options).
- Công bằng: mô hình cho split dev được huấn luyện KHÔNG có câu dev của bàn đo (gold_dev bỏ, gold_test giữ); split test thì ngược lại.
  Câu của bàn đo trùng danh mục (theo dạng đã bỏ dấu) cũng bị loại. Mô hình phát hành (`tools/train_hearfix`) dùng tất cả.
- Mỗi câu có 6 biến thể (3 giọng x sạch/nhiễu) nên số theo dòng bị nhân ~6 lần; báo thêm số theo câu gốc (duy nhất).

Chỉ số: intent cuối đúng (act/confirm đúng nhãn tính là đúng; options KHÔNG), số câu sửa đúng / làm sai (câu bất đồng:
sai -> đúng; đúng -> bị can thiệp), số câu chat thật bị cướp, số lần chạy nhầm lệnh, độ trễ thêm p50/p90, và vietmed (giọng thật,
không phải lệnh) bị can thiệp bao nhiêu.
"""

import argparse
import pickle
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from pipeline import hearfix  # noqa: E402
from tools import train_hearfix  # noqa: E402
from tools.bench_stt_cmd import load_manifest  # noqa: E402
from tools.bench_stt_report import IntentCache, load_results, pipeline_text  # noqa: E402


def pct(values, q):
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))] if s else 0.0


def build_models(rows: list[dict], cap: int | None) -> dict[str, hearfix.Model]:
    models = {}
    for split, other_gold in (("dev", "gold_test"), ("test", "gold_dev")):
        exclude = {hearfix.fold(r["text"]) for r in rows if r["split"] == split}
        cat = train_hearfix.catalog(["train", "eval", "params", other_gold], exclude, cap)
        print(f"[{split}] danh mục {len(cat)} câu (loại câu của bàn đo {split}: {len(exclude)})", file=sys.stderr)
        models[split] = train_hearfix.train(cat)
    return models


def outcome(row: dict, pred: str, sug: hearfix.Suggestion | None) -> dict:
    """Kết quả cuối của một dòng."""
    label = row["label"]
    if pred != "chat" or sug is None:
        return {"kind": "same", "intent": pred, "ok": pred == label}
    if sug.kind == "options":
        return {"kind": "options", "intent": sug.intent, "ok": False, "hit": label in sug.options}
    return {"kind": sug.kind, "intent": sug.intent, "ok": sug.intent == label}


def evaluate(rows, results, ic, models, strong, weak, option_min, max_options):
    config.HEARFIX_STRONG, config.HEARFIX_WEAK = strong, weak
    config.HEARFIX_OPTION_MIN, config.HEARFIX_MAX_OPTIONS = option_min, max_options
    out = []
    for r in rows:
        h = results.get(r["id"])
        if h is None:
            continue
        text = pipeline_text(h["hyp"])
        pred = ic.get(text, r.get("device") or {})
        sug = None
        if pred == "chat":
            m = models[r["split"]]
            sug = hearfix.decide(m.proba(text), m.classes, r.get("device") or {}, len(text.split()))
        out.append({"row": r, "hyp": text, "pred": pred, "sug": sug, **outcome(r, pred, sug)})
    return out


def summarize(res: list[dict], title: str) -> dict:
    n = len(res)
    base_ok = sum(x["pred"] == x["row"]["label"] for x in res)
    after_ok = sum(x["ok"] for x in res)
    changed = [x for x in res if x["kind"] != "same"]
    fixed = [x for x in changed if x["kind"] in ("act", "confirm") and x["ok"] and x["pred"] != x["row"]["label"]]
    hij = [x for x in changed if x["row"]["label"] == "chat"]
    bad_act = [x for x in changed if x["kind"] == "act" and x["row"]["label"] != "chat" and not x["ok"]]
    bad_conf = [x for x in changed if x["kind"] == "confirm" and x["row"]["label"] != "chat" and not x["ok"]]
    opt_hit = [x for x in changed if x["kind"] == "options" and x["row"]["label"] != "chat" and x["hit"]]
    opt_miss = [x for x in changed if x["kind"] == "options" and x["row"]["label"] != "chat" and not x["hit"]]
    n_chat = sum(x["row"]["label"] == "chat" and x["pred"] == "chat" for x in res)
    n_miss = sum(x["row"]["label"] != "chat" and x["pred"] == "chat" for x in res)
    uniq = lambda xs: len({x["row"]["text"] for x in xs})  # noqa: E731
    print(f"\n### {title} (n={n} clip; Gemma cho chat: {n_chat} đúng chat + {n_miss} lệnh rơi vào chat)")
    print(f"- intent cuối đúng: TRƯỚC {100 * base_ok / n:.2f}% ({base_ok}) -> SAU {100 * after_ok / n:.2f}% ({after_ok})")
    print(f"- sửa đúng (sai -> đúng, qua act/confirm): {len(fixed)} clip / {uniq(fixed)} câu "
          f"[act {sum(x['kind'] == 'act' for x in fixed)}, confirm {sum(x['kind'] == 'confirm' for x in fixed)}]")
    print(f"- làm sai: chat thật bị cướp {len(hij)} clip / {uniq(hij)} câu "
          f"[act {sum(x['kind'] == 'act' for x in hij)}, confirm {sum(x['kind'] == 'confirm' for x in hij)}, "
          f"options {sum(x['kind'] == 'options' for x in hij)}]")
    print(f"- lệnh thật nhưng đoán nhầm: chạy nhầm lệnh (act) {len(bad_act)}, hỏi nhầm (confirm) {len(bad_conf)}")
    print(f"- options cho lệnh thật: có đúng nhãn {len(opt_hit)} / không {len(opt_miss)}")
    return {"base_ok": base_ok, "after_ok": after_ok, "fixed": len(fixed), "hij": len(hij), "bad_act": len(bad_act),
            "bad_conf": len(bad_conf), "opt_hit": len(opt_hit), "opt_miss": len(opt_miss), "n": n}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True)
    ap.add_argument("--cfg", default="bothhw2.0_4")
    ap.add_argument("--strong", type=float, default=config.HEARFIX_STRONG)
    ap.add_argument("--weak", type=float, default=config.HEARFIX_WEAK)
    ap.add_argument("--option-min", type=float, default=config.HEARFIX_OPTION_MIN)
    ap.add_argument("--max-options", type=int, default=config.HEARFIX_MAX_OPTIONS)
    ap.add_argument("--sweep", action="store_true", help="in bảng nhiều (strong, weak) rồi thoát")
    ap.add_argument("--model-cache", help="tệp pickle giữ hai mô hình công bằng (dev/test) để khỏi huấn luyện lại")
    ap.add_argument("--cap", type=int, default=train_hearfix.CAP, help="tối đa câu mỗi nhãn khi huấn luyện (0 = không cắt)")
    ap.add_argument("--show", action="store_true", help="in từng ca bị can thiệp")
    args = ap.parse_args()
    cache = Path(args.cache)
    rows = [r for r in load_manifest(cache, ("cmd",))]
    results = load_results(cache, args.cfg)
    ic = IntentCache(cache / "intent_cache.json")
    models = None
    if args.model_cache and Path(args.model_cache).exists():
        models = pickle.loads(Path(args.model_cache).read_bytes())
    if models is None:
        models = build_models(rows, args.cap)
        if args.model_cache:
            Path(args.model_cache).write_bytes(pickle.dumps(models))

    if args.sweep:
        print("| strong | weak | split | đúng trước -> sau | sửa | chat bị cướp (act/confirm/options) | chạy nhầm | hỏi nhầm | opt có/không |")
        print("|---|---|---|---|---|---|---|---|---|")
        for strong in (0.6, 0.7, 0.8, 0.9):
            for weak in (0.35, 0.45, 0.55, 2.0):  # 2.0 = tắt options
                for split in ("dev", "test"):
                    res = evaluate([r for r in rows if r["split"] == split], results, ic, models, strong, weak,
                                   args.option_min, args.max_options)
                    chat_hij = [x for x in res if x["kind"] != "same" and x["row"]["label"] == "chat"]
                    n = len(res)
                    base = sum(x["pred"] == x["row"]["label"] for x in res)
                    aft = sum(x["ok"] for x in res)
                    fixed = sum(x["kind"] in ("act", "confirm") and x["ok"] and x["pred"] != x["row"]["label"] for x in res)
                    ba = sum(x["kind"] == "act" and x["row"]["label"] != "chat" and not x["ok"] for x in res)
                    bc = sum(x["kind"] == "confirm" and x["row"]["label"] != "chat" and not x["ok"] for x in res)
                    oh = sum(x["kind"] == "options" and x["row"]["label"] != "chat" and x["hit"] for x in res)
                    om = sum(x["kind"] == "options" and x["row"]["label"] != "chat" and not x["hit"] for x in res)
                    k = Counter(x["kind"] for x in chat_hij)
                    print(f"| {strong} | {weak} | {split} | {100 * base / n:.1f} -> {100 * aft / n:.1f} | {fixed} | "
                          f"{len(chat_hij)} ({k['act']}/{k['confirm']}/{k['options']}) | {ba} | {bc} | {oh}/{om} |")
        return

    allres = evaluate(rows, results, ic, models, args.strong, args.weak, args.option_min, args.max_options)
    print(f"## hearfix: strong={args.strong} weak={args.weak} option_min={args.option_min} max_options={args.max_options}"
          f" cfg={args.cfg}")
    for split in ("dev", "test"):
        for cond in ("clean", "snr10"):
            summarize([x for x in allres if x["row"]["split"] == split and x["row"]["cond"] == cond], f"{split}-{cond}")
    summarize([x for x in allres if x["row"]["split"] == "test"], "test (cả 2 điều kiện)")
    summarize([x for x in allres if x["row"]["split"] == "dev"], "dev (cả 2 điều kiện)")
    summarize(allres, "TẤT CẢ")

    if args.show:
        print("\n### Từng ca bị can thiệp (duy nhất theo bản ghi)")
        seen = set()
        for x in allres:
            if x["kind"] == "same" or (x["hyp"], x["kind"]) in seen:
                continue
            seen.add((x["hyp"], x["kind"]))
            tag = "CHAT-BỊ-CƯỚP" if x["row"]["label"] == "chat" else ("ĐÚNG" if x["ok"] or x.get("hit") else "SAI")
            s = x["sug"]
            print(f"{tag:12s} {x['kind']:8s} {s.prob:.2f} {s.intent:14s} ref={x['row']['label']:14s} "
                  f"'{x['row']['text']}' -> nghe '{x['hyp']}'" + (f"  options={s.options}" if x["kind"] == "options" else ""))

    # độ trễ thêm: mô hình + quyết định trên MỌI bản ghi (cả câu Gemma cho lệnh; thực tế chỉ chạy với chat)
    texts = [x["hyp"] for x in allres]
    m = models["test"]
    for t in texts[:50]:  # làm nóng
        hearfix.decide(m.proba(t), m.classes, {})
    times = []
    for t in texts:
        t0 = time.perf_counter()
        hearfix.decide(m.proba(t), m.classes, {})
        times.append((time.perf_counter() - t0) * 1000)
    print(f"\n### Độ trễ thêm (ms, {len(times)} bản ghi): p50 {statistics.median(times):.3f}  p90 {pct(times, 0.9):.3f}  "
          f"p99 {pct(times, 0.99):.3f}  max {max(times):.3f}")

    # vietmed: giọng thật, không phải lệnh -> mọi can thiệp là cướp
    vm = [r for r in load_manifest(cache, ("vietmed",))]
    vres = results
    hits = []
    for r in vm:
        h = vres.get(r["id"])
        if h is None:
            continue
        t = pipeline_text(h["hyp"])
        s = hearfix.decide(models["test"].proba(t), models["test"].classes, {}, len(t.split()))
        if s is not None:
            hits.append((s, t))
    print(f"\n### VietMed (giọng thật, {len(vm)} clip, không có lệnh): bị can thiệp {len(hits)} "
          f"[act {sum(s.kind == 'act' for s, _ in hits)}, confirm {sum(s.kind == 'confirm' for s, _ in hits)}, "
          f"options {sum(s.kind == 'options' for s, _ in hits)}]")
    for s, t in hits[:10]:
        print(f"  {s.kind} {s.intent} {s.prob:.2f}: {t}")


if __name__ == "__main__":
    main()
