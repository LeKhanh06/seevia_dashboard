"""Kiểm báo vào/ra chế độ end-to-end (kính giả lập -> VH): cảnh báo vật cản bị đọc chữ tạm chiếm rồi dựng lại.

    TOUR_BASE=http://localhost:18081 python demo-web/e2e/mode_flow.py

In theo từng bước: các câu server nói (khung `say`) và chế độ sáng (khung `mode`) của người xem Python.
Thoát 0 nếu mọi kiểm tra đạt. Ảnh camera "trucks" không có chữ nên OCR live phải căn khung tới hết trần.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_e2e as B  # noqa: E402

PAUSED, RESUMED = "Tạm tắt cảnh báo.", "Bật lại cảnh báo."
EXIT_READ = "Thoát đọc chữ."
OUT = os.path.join(B.STORAGE, "mode-flow-" + time.strftime("%H%M%S"))
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"   {'ĐẠT ' if ok else 'HỎNG'} {name}  {detail}", flush=True)


class Tap:
    """Đọc phần log người xem phát sinh kể từ lần gọi trước."""

    def __init__(self, obs):
        self.obs, self.pos = obs, 0

    def new(self):
        frames = self.obs.log[self.pos:]
        self.pos = len(self.obs.log)
        said = [f[2]["text"] for f in frames if f[0] == "json" and f[1] == "say"]
        modes = [(f[2]["obstacle"], f[2]["ocr"]) for f in frames if f[0] == "json" and f[1] == "mode"]
        cues = [f[2].get("id") for f in frames if f[0] == "json" and f[1] == "cue"]
        return said, modes, cues


async def step(s, tap, clip, label, wait):
    print(f"\n▶ {label}", flush=True)
    await s.say(clip)
    await s.reply_done(30)
    await asyncio.sleep(wait)
    said, modes, cues = tap.new()
    print(f"   server nghe: {await s.heard()}", flush=True)
    print(f"   nói: {said}", flush=True)
    print(f"   cue: {cues}", flush=True)
    print(f"   chế độ (obstacle, ocr) theo thời gian: {modes}", flush=True)
    return said, modes


async def main():
    base = os.environ.get("TOUR_BASE", "http://localhost:18081")
    os.makedirs(OUT, exist_ok=True)
    r = B.Report(OUT)
    obs = B.PyObserver(B.observe_url(base))
    async with B.Session(base, r, B.cam_file("trucks"), clips=["alert_on", "alert_off", "ocr"]) as s:
        await s.start_devices()
        obs.start()
        await asyncio.sleep(2)
        tap = Tap(obs)
        tap.new()

        print("\n===== S1: bật cảnh báo -> đọc chữ (không có chữ) -> cảnh báo được dựng lại =====")
        said, modes = await step(s, tap, "alert_on", "Bật cảnh báo vật cản", 8)
        check("cảnh báo sáng", (True, False) in modes, modes)
        said, modes = await step(s, tap, "ocr", "Đọc lá thư tôi đang cầm (ảnh không chữ)", B.OCR_WAIT if hasattr(B, "OCR_WAIT") else 45)
        check("nói 'Tạm tắt cảnh báo.'", PAUSED in said, said)
        check("nói 'Thoát đọc chữ.'", EXIT_READ in said, said)
        check("nói 'Bật lại cảnh báo.'", RESUMED in said, said)
        if PAUSED in said and EXIT_READ in said and RESUMED in said:
            check("thứ tự: tạm tắt < thoát đọc < bật lại", said.index(PAUSED) < said.index(EXIT_READ) < said.index(RESUMED), said)
        check("OCR sáng rồi cảnh báo sáng lại", (False, True) in modes and modes and modes[-1] == (True, False), modes)

        print("\n===== S2: tắt cảnh báo (lúc không bị tạm dừng) =====")
        said, modes = await step(s, tap, "alert_off", "Tắt cảnh báo", 4)
        check("cảnh báo tắt hẳn", modes and modes[-1] == (False, False), modes)
        check("không nói 'Bật lại cảnh báo.'", RESUMED not in said, said)

        print("\n===== S3: không có cảnh báo trước đó -> đọc chữ không nhắc cảnh báo =====")
        said, modes = await step(s, tap, "ocr", "Đọc lá thư tôi đang cầm", 45)
        check("không nói 'Tạm tắt cảnh báo.'", PAUSED not in said, said)
        check("không nói 'Bật lại cảnh báo.'", RESUMED not in said, said)
        check("vẫn nói 'Thoát đọc chữ.'", EXIT_READ in said, said)
        check("kết thúc không còn chế độ nào", modes and modes[-1] == (False, False), modes)

        print("\n===== S4: bật cảnh báo -> đọc chữ -> TẮT cảnh báo giữa lúc đang bị tạm dừng =====")
        await step(s, tap, "alert_on", "Bật cảnh báo vật cản", 8)
        await step(s, tap, "ocr", "Đọc lá thư tôi đang cầm (không chờ xong)", 3)
        said, modes = await step(s, tap, "alert_off", "Tắt cảnh báo (OCR còn đang chạy)", 40)
        check("cuối cùng cảnh báo TẮT (không tự bật lại)", modes and modes[-1] == (False, False), modes)
        late_obs = [m for m in modes if m == (True, False)]
        print(f"   (số lần cảnh báo sáng lại sau lệnh tắt: {len(late_obs)}; nếu >0 là đua nhau giữa dựng lại và tắt)", flush=True)

        obs.stop()
        if s.errors:
            print("lỗi JS:", s.errors)
    ok = all(RESULTS)
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} đạt", flush=True)
    sys.exit(0 if ok else 1)


asyncio.run(main())
