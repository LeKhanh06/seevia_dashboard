"""Bàn đo STT hai nhánh (xem docs/2026-10-22-stt-hai-nhanh.md). Dùng cùng manifest/cache với bench_stt_cmd.

Ba bước, mỗi bước một biến:

  1. giải mã MỘT nhánh bằng đúng code production (kèm độ tin cậy từng từ), ghi `DIR/results/<name>.jsonl`:
       python -m tools.bench_stt_dual decode --cache DIR --branch b1 --name d_b1
       STT_DUAL_DECODING=modified_beam_search python -m tools.bench_stt_dual decode --cache DIR --branch b2 --name d_b2beam
  2. hợp hai kết quả theo một chính sách, ghi `DIR/results/<name>.jsonl` (cùng định dạng bench_stt_report):
       python -m tools.bench_stt_dual vote --cache DIR --b1 d_b1 --b2 d_b2 --mode conf|llm|none --name v_conf
     `llm`: gọi Gemma thật (concurrency <= 6), nhớ xác suất + độ trễ vào `llm_probs_cache.json`; chạy lại với
     timeout/ngưỡng khác thì phát lại từ cache (trễ > timeout = coi như quá hạn), không gọi lại.
  3. đo trễ song song thật: python -m tools.bench_stt_dual latency --cache DIR
  4. báo cáo: python -m tools.bench_stt_report --cache DIR --base d_b1 --cfgs d_b1 d_b2 v_conf v_llm
     và `python -m tools.bench_stt_dual summary --cache DIR --names ...` (tỉ lệ mâu thuẫn, nguồn quyết định, trễ Gemma).
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from pipeline import stt_dual as sd  # noqa: E402
from tools.bench_stt_cmd import load_manifest, read_wav  # noqa: E402

SR = 16000


def _rows(cache: Path, sets):
    return load_manifest(cache, tuple(sets))


def cmd_decode(args) -> None:
    cache = Path(args.cache)
    rows = _rows(cache, args.sets)
    if args.limit:
        rows = rows[:args.limit]
    fn = sd.decode_branch1 if args.branch == "b1" else sd.decode_branch2
    audio = {r["id"]: read_wav(cache / r["wav"]) for r in rows}
    for r in rows[:5]:
        fn(audio[r["id"]])
    out, t0 = [], time.time()
    for n, r in enumerate(rows, 1):
        best = None
        for _ in range(args.reps):
            h = fn(audio[r["id"]])
            best = h if best is None or h.ms < best.ms else best
        out.append({"id": r["id"], "hyp": best.text, "ms": best.ms,
                    "words": [[w.text, round(w.logp, 4)] for w in best.words]})
        if n % 500 == 0:
            print(f"{args.name}: {n}/{len(rows)} ({time.time() - t0:.0f}s)", flush=True)
    res = cache / "results"
    res.mkdir(exist_ok=True)
    (res / f"{args.name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out),
                                            encoding="utf-8")
    print(f"{args.name}: xong {len(out)} clip trong {time.time() - t0:.0f}s")


def _load(cache: Path, name: str) -> dict[str, dict]:
    p = cache / "results" / f"{name}.jsonl"
    return {r["id"]: r for r in map(json.loads, p.read_text(encoding="utf-8").splitlines()) if r}


def _hyp(rec: dict) -> sd.Hyp:
    words = [sd.Word(w, lp) for w, lp in rec["words"]]
    return sd.Hyp(rec["hyp"], words, rec["ms"])


class CachedProbs:
    """Bọc `llm_probs`: gọi Gemma thật 1 lần cho mỗi (thứ tự ứng viên, ngữ cảnh), lưu (xác suất, ms).

    Phát lại từ cache thì trễ ghi nhận > timeout coi như quá hạn (mô phỏng `STT_ADJUDICATE_TIMEOUT_S`)."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}
        self.sem = asyncio.Semaphore(6)
        self.dirty = 0

    def key(self, texts, device):
        return json.dumps(["p", texts, {k: bool(v) for k, v in (device or {}).items()}], ensure_ascii=False)

    async def __call__(self, texts, device):
        key = self.key(texts, device)
        rec = self.data.get(key)
        if rec is None:
            async with self.sem:
                for attempt in range(3):
                    t0 = time.perf_counter()
                    try:
                        pr, ms = await sd.llm_probs(texts, device)
                        rec = {"p": pr, "ms": ms}
                        break
                    except Exception as exc:  # noqa: BLE001
                        rec = {"err": type(exc).__name__, "ms": (time.perf_counter() - t0) * 1000}
                        await asyncio.sleep(1 + attempt)
            self.data[key] = rec
            self.dirty += 1
            if self.dirty % 100 == 0:
                self.save()
        if "err" in rec:
            raise RuntimeError(rec["err"])
        if rec["ms"] / 1000 > config.STT_ADJUDICATE_TIMEOUT_S:
            raise asyncio.TimeoutError()
        return rec["p"], rec["ms"]

    def save(self):
        self.path.write_text(json.dumps(self.data, ensure_ascii=False))


async def _vote(args) -> None:
    cache = Path(args.cache)
    rows = _rows(cache, args.sets)
    r1, r2 = _load(cache, args.b1), _load(cache, args.b2)
    for k, v in (("STT_ADJUDICATE_TIMEOUT_S", args.timeout), ("STT_ADJUDICATE_MIN_PROB", args.minprob),
                 ("STT_ADJUDICATE_SCOPE", args.scope), ("STT_VOTE_BIAS", args.bias), ("STT_VOTE_EMPTY", args.empty),
                 ("STT_ADJUDICATE_MAX_CONFLICTS", args.maxc)):
        if v is not None:
            setattr(config, k, v)
    if args.keephead is not None:
        config.STT_ADJUDICATE_KEEP_HEAD = bool(args.keephead)
    if args.keepstop is not None:
        config.STT_ADJUDICATE_KEEP_STOP = bool(args.keepstop)
    if args.debias is not None:
        config.STT_ADJUDICATE_DEBIAS = bool(args.debias)
    pick = CachedProbs(cache / "llm_probs_cache.json")
    sem = asyncio.Semaphore(8)

    async def one(r):
        async with sem:
            d = await sd.decide(_hyp(r1[r["id"]]), _hyp(r2[r["id"]]), r.get("device"), args.mode, probs=pick)
        return r["id"], d

    t0 = time.time()
    done = await asyncio.gather(*(one(r) for r in rows))
    pick.save()
    out = []
    for rid, d in done:
        out.append({"id": rid, "hyp": d.text, "ms": r1[rid]["ms"], "source": d.source, "conflicts": d.conflicts,
                    "n_cands": d.n_cands, "prob": d.prob, "llm_ms": d.llm_ms, "b1": r1[rid]["hyp"],
                    "b2": r2[rid]["hyp"]})
    (cache / "results" / f"{args.name}.jsonl").write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(f"{args.name}: {len(out)} lượt trong {time.time() - t0:.0f}s")


def cmd_vote(args) -> None:
    asyncio.run(_vote(args))


def cmd_latency(args) -> None:
    """Trễ lượt cuối: nhánh 1 một mình vs hai nhánh song song, xen kẽ cùng clip, cùng lúc (cùng tải máy)."""
    cache = Path(args.cache)
    rows = _rows(cache, ["cmd"])
    rows = [r for r in rows if r["cond"] == "snr10"][::max(1, len(rows) // 2 // args.n)][:args.n]
    audio = [read_wav(cache / r["wav"]) for r in rows]
    for a in audio[:4]:
        sd.decode_both(a)
        sd.decode_branch1(a)
    t_one, t_both, t_b2, t_seq, t_merge = [], [], [], [], []
    for rep in range(args.reps):
        for a in audio:
            t = time.perf_counter()
            h1 = sd.decode_branch1(a)
            t_one.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            h1, h2 = sd.decode_both(a)
            t_both.append((time.perf_counter() - t) * 1000)
            t_b2.append(h2.ms)
            t = time.perf_counter()
            sd.decode_branch1(a)
            sd.decode_branch2(a)
            t_seq.append((time.perf_counter() - t) * 1000)
            t = time.perf_counter()
            segs = sd.align(h1.words, h2.words)
            sd.vote_conf(segs)
            sd.build_candidates(segs)
            t_merge.append((time.perf_counter() - t) * 1000)

    def p(x, q):
        x = sorted(x)
        return x[min(len(x) - 1, int(q * len(x)))]

    print(f"n={len(t_one)} lần giải (xen kẽ), STT_DUAL_NUM_THREADS={config.STT_DUAL_NUM_THREADS} "
          f"STT_NUM_THREADS={config.STT_NUM_THREADS}")
    for name, x in (("nhánh 1 một mình", t_one), ("nhánh 2 một mình", t_b2), ("hai nhánh song song", t_both),
                    ("hai nhánh tuần tự", t_seq), ("căn chỉnh+ứng viên", t_merge)):
        print(f"{name:22s} p50 {p(x, .5):6.1f}  p90 {p(x, .9):6.1f}  mean {statistics.mean(x):6.1f} ms")


def _wav_bytes(samples) -> bytes:
    import io
    import wave

    import numpy as np

    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    return buf.getvalue()


async def _replay_one(row, samples, dual: bool, device: dict, pace: float):
    """Một lượt như turn.py: audio tới theo mảnh 100 ms theo thời gian thật, vòng partial 300 ms, chốt sớm khi VAD
    báo hết câu, rồi 'nhả nút' (hết audio). Trả (chữ cuối, trễ final_stt tính từ lúc nhả, transcriber)."""
    from pipeline import vad
    from pipeline.stt_stream import StreamingTranscriber

    data = _wav_bytes(samples)
    seg = vad.Segmenter() if vad.available() else None
    tr = StreamingTranscriber("audio/wav", seg, dual=dual, device_state=device)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    early: dict = {}

    async def partials():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), config.STT_PARTIAL_INTERVAL_S)
                return
            except asyncio.TimeoutError:
                pass
            if tr.has_new_audio():
                await tr.partial_async()
            if config.VAD_EARLY_FINAL and tr.speech_ended and "task" not in early:
                early["task"] = loop.create_task(tr.final_async())

    pt = loop.create_task(partials())
    step = 3200 + 0  # 100 ms PCM16
    for i in range(0, len(data), step):
        tr.feed(data[i:i + step])
        await asyncio.sleep(0.1 / pace)
    released = time.perf_counter()
    stop.set()
    await pt
    if "task" in early:
        text = await early["task"]
        if tr.span_key() != tr.final_key:  # tiếng mới chen vào sau chốt sớm: làm lại như turn.py
            text = await tr.final_async()
    else:
        text = await tr.final_async()
    return text, (time.perf_counter() - released) * 1000, tr


async def _replay(args) -> None:
    from pipeline import stt
    from tools.bench_stt_report import edit_distance, norm_words

    cache = Path(args.cache)
    rows = [r for r in _rows(cache, ["cmd"]) if r["cond"] == "snr10"]
    rows = rows[::max(1, len(rows) // args.n)][:args.n]
    audio = {r["id"]: read_wav(cache / r["wav"]) for r in rows}
    modes = ["single", "dual"] if args.mode == "both" else [args.mode]
    if "dual" in modes:
        await asyncio.to_thread(sd.preload)
    stt._load_asr()
    for r in rows[:3]:  # làm nóng
        for m in modes:
            await _replay_one(r, audio[r["id"]], m == "dual", r.get("device") or {}, 8.0)
    acc = {m: dict(lat=[], errs=0, nref=0, exact=0, conflict=0, prefetch=0, llm=0, late=0, llm_ms=[],
                   fin={True: [], False: []}) for m in modes}
    for rep in range(args.reps):
        for r in rows:
            for m in modes:  # xen kẽ từng clip: cùng tải máy
                text, ms, tr = await _replay_one(r, audio[r["id"]], m == "dual", r.get("device") or {}, args.pace)
                a = acc[m]
                a["lat"].append(ms)
                ref, hyp = norm_words(r["text"]), norm_words(text)
                a["errs"] += edit_distance(ref, hyp)
                a["nref"] += len(ref)
                a["exact"] += ref == hyp
                info = tr.dual_info()
                a["late"] += bool(tr.b2_late)
                conflict = bool(info and info["conflicts"])
                a["fin"][conflict].append(ms)
                if conflict:
                    a["conflict"] += 1
                    a["prefetch"] += bool(info["prefetch"])
                    if info["source"] == "llm":
                        a["llm"] += 1
                        a["llm_ms"].append(info["llm_ms"])

    def p(x, q):
        x = sorted(x)
        return x[min(len(x) - 1, int(q * len(x)))] if x else float("nan")

    for m in modes:
        a = acc[m]
        n = len(a["lat"])
        print(f"[{args.name}] mode={m} adjudicate={config.STT_ADJUDICATE} prefetch={config.STT_ADJUDICATE_PREFETCH} "
              f"partial={config.STT_DUAL_PARTIAL} b2_wait={config.STT_DUAL_B2_WAIT_MS}ms n={n}")
        print(f"  final_stt (từ lúc nhả nút): p50 {p(a['lat'], .5):.0f}  p90 {p(a['lat'], .9):.0f}  "
              f"mean {statistics.mean(a['lat']):.0f} ms")
        print(f"  WER {100 * a['errs'] / a['nref']:.2f}%  đúng nguyên câu {100 * a['exact'] / n:.1f}%")
        if m == "dual":
            print(f"  mâu thuẫn {a['conflict']}/{n} ({100 * a['conflict'] / n:.0f}%); chấm trước trúng {a['prefetch']}; "
                  f"Gemma chọn {a['llm']} lần" + (f", p50 {p(a['llm_ms'], .5):.0f} p90 {p(a['llm_ms'], .9):.0f} ms"
                                                  if a["llm_ms"] else "") + f"; nhánh 2 quá hạn {a['late']} lượt")
            print(f"  final_stt khi KHÔNG mâu thuẫn: p50 {p(a['fin'][False], .5):.0f} p90 {p(a['fin'][False], .9):.0f}; "
                  f"khi mâu thuẫn: p50 {p(a['fin'][True], .5):.0f} p90 {p(a['fin'][True], .9):.0f}")


def cmd_replay(args) -> None:
    for k, v in (("STT_ADJUDICATE", args.adjudicate), ("STT_ADJUDICATE_PREFETCH", args.prefetch),
                 ("STT_DUAL_PARTIAL", args.partial), ("STT_ADJUDICATE_SCOPE", args.scope)):
        if v is not None:
            setattr(config, k, v if k.endswith(("ADJUDICATE", "SCOPE")) else bool(int(v)))
    if args.b2wait is not None:
        config.STT_DUAL_B2_WAIT_MS = args.b2wait
    asyncio.run(_replay(args))


def cmd_summary(args) -> None:
    cache = Path(args.cache)
    rows = {r["id"]: r for r in _rows(cache, args.sets)}
    for name in args.names:
        res = _load(cache, name)
        src, lat = {}, []
        n = len(res)
        for rid, r in res.items():
            s = r.get("source", "-")
            src[s] = src.get(s, 0) + 1
            if r.get("source") == "llm" or str(r.get("source", "")).startswith("fallback"):
                lat.append(r["llm_ms"])
        conflict = sum(1 for r in res.values() if r.get("conflicts", 0) > 0)
        print(f"{name}: n={n} mâu thuẫn {conflict} ({100 * conflict / n:.1f}%) nguồn {src}")
        if lat:
            lat.sort()
            print(f"    Gemma: {len(lat)} lượt gọi, p50 {lat[len(lat) // 2]:.0f} ms, p90 {lat[int(.9 * len(lat))]:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode")
    d.add_argument("--cache", required=True)
    d.add_argument("--branch", choices=["b1", "b2"], required=True)
    d.add_argument("--name", required=True)
    d.add_argument("--reps", type=int, default=2)
    d.add_argument("--sets", nargs="*", default=["cmd", "vietmed"])
    d.add_argument("--limit", type=int)
    v = sub.add_parser("vote")
    v.add_argument("--cache", required=True)
    v.add_argument("--b1", required=True)
    v.add_argument("--b2", required=True)
    v.add_argument("--mode", choices=["none", "conf", "llm"], required=True)
    v.add_argument("--name", required=True)
    v.add_argument("--sets", nargs="*", default=["cmd", "vietmed"])
    v.add_argument("--debias", type=int)
    v.add_argument("--keephead", type=int)
    v.add_argument("--keepstop", type=int)
    v.add_argument("--timeout", type=float)
    v.add_argument("--minprob", type=float)
    v.add_argument("--scope")
    v.add_argument("--bias", type=float)
    v.add_argument("--empty", type=float)
    v.add_argument("--maxc", type=int)
    lt = sub.add_parser("latency")
    lt.add_argument("--cache", required=True)
    lt.add_argument("--n", type=int, default=120)
    lt.add_argument("--reps", type=int, default=3)
    rp = sub.add_parser("replay")
    rp.add_argument("--cache", required=True)
    rp.add_argument("--mode", choices=["single", "dual", "both"], required=True)
    rp.add_argument("--name", default="")
    rp.add_argument("--n", type=int, default=60)
    rp.add_argument("--reps", type=int, default=1)
    rp.add_argument("--pace", type=float, default=1.0)
    rp.add_argument("--adjudicate")
    rp.add_argument("--prefetch")
    rp.add_argument("--partial")
    rp.add_argument("--b2wait", type=float)
    rp.add_argument("--scope")
    sm = sub.add_parser("summary")
    sm.add_argument("--cache", required=True)
    sm.add_argument("--names", nargs="+", required=True)
    sm.add_argument("--sets", nargs="*", default=["cmd", "vietmed"])
    args = ap.parse_args()
    {"decode": cmd_decode, "vote": cmd_vote, "latency": cmd_latency, "replay": cmd_replay, "summary": cmd_summary}[args.cmd](args)


if __name__ == "__main__":
    main()
