// Trang demo cho ban giám khảo: CHỈ HIỂN THỊ. Không bật camera/micro, không gửi lượt nào.
// Nó nối `WS /v2/observe` của server kính và vẽ lại những gì kính gửi lên và server làm.
//
//   server -> trang  hello_ok | glasses | turn_start | turn_end | 0x01 ảnh lúc bấm nút
//                    trace | say | mode | reply | reply_end | 0x02 ADPCM tiếng trả lời
//                    cam | capture | end | hazard         (lệnh server gửi kính)
//                    0x04 JPEG + det | ocr                 (khung server vừa xử lý + box của chính nó)
//   trang -> server  hello | ping
//
// Hợp đồng ở docs/FIRMWARE_ENDPOINTS.md (mục "Xem"). Khung `det`/`ocr` luôn đi ngay sau JPEG nó được tính trên,
// nên box vẽ đúng lên đúng khung dù server xử lý chậm hơn kính gửi.

import { decodeBlocks, BLOCK_ALIGN, SAMPLE_RATE } from "./adpcm.js";

const $ = (id) => document.getElementById(id);

/** Dựng phần tử nhanh. Mọi chữ từ server đi qua `append`/textContent, không innerHTML. */
function h(tag, cls, ...kids) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  for (const k of kids) if (k != null && k !== "") el.append(k);
  return el;
}

// ---------------------------------------------------------------------------
// Cấu hình
// ---------------------------------------------------------------------------

const DEFAULTS = { base: "/api", token: "", device: "", sound: true };
const PING_MS = 20000;
const STALE_MS = 3000; // quá chừng này không có khung mới thì coi là kính đã ngừng gửi

function loadSettings() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("seevia-demo") || "{}") };
  } catch {
    return { ...DEFAULTS };
  }
}
function saveSettings() {
  try {
    localStorage.setItem("seevia-demo", JSON.stringify(settings));
  } catch {
    /* chế độ riêng tư: dùng được, chỉ không nhớ lần sau */
  }
}
const settings = loadSettings();
// Mã kính hiện tại do serve.py đọc từ .env (không bí mật). Token thì proxy tự gắn, trang không bao giờ thấy.
let serverDevice = "";

const fmtNum = (x, d = 1) => Number(x).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtMs = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : `${fmtNum(ms / 1000)} s`);
const fmtSec = (s) => fmtMs(s * 1000);
const clock = () => new Date().toLocaleTimeString("vi-VN", { hour12: false });

function setNote(text) {
  $("note").textContent = text || "";
  $("note").hidden = !text;
}

// ---------------------------------------------------------------------------
// Phát âm thanh server trả về kính: ADPCM 0x02 -> WebAudio, nối liên tục không khe hở
// ---------------------------------------------------------------------------

const media = { ctx: null };
function audioCtx() {
  media.ctx ??= new (window.AudioContext || window.webkitAudioContext)();
  return media.ctx;
}

const speaker = (() => {
  let next = 0; // thời điểm (đồng hồ ctx) mà khối kế tiếp được bắt đầu
  let leftover = new Uint8Array(0);
  const live = new Set();
  const PREROLL = 0.2; // như firmware: dựng đệm 0,2 s trước khi phát khối đầu
  return {
    push(bytes) {
      if (!settings.sound) return;
      const ctx = audioCtx();
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      let data = bytes;
      if (leftover.length) { data = new Uint8Array(leftover.length + bytes.length); data.set(leftover); data.set(bytes, leftover.length); }
      const usable = data.length - (data.length % BLOCK_ALIGN);
      leftover = data.slice(usable);
      if (!usable) return;
      const pcm = decodeBlocks(data.subarray(0, usable));
      const buf = ctx.createBuffer(1, pcm.length, SAMPLE_RATE);
      buf.copyToChannel(pcm, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const now = ctx.currentTime;
      if (next < now + 0.02) next = now + PREROLL; // hết hàng đợi: câu mới, dựng lại đệm
      src.start(next);
      next += buf.duration;
      live.add(src);
      src.onended = () => live.delete(src);
    },
    /** Kính bấm nút = dừng mọi thứ đang phát. */
    stop() {
      for (const s of live) { try { s.stop(); } catch { /* đã dừng */ } }
      live.clear();
      leftover = new Uint8Array(0);
      next = 0;
    },
    /** Bíp cảnh báo thay cho clip cục bộ trên kính: số tiếng bíp = mức, nghiêng theo hướng. */
    beep(level, dir) {
      if (!settings.sound) return;
      const ctx = audioCtx();
      if (ctx.state === "suspended") return;
      const pan = ctx.createStereoPanner?.();
      if (pan) { pan.pan.value = dir === "left" ? -0.8 : dir === "right" ? 0.8 : 0; pan.connect(ctx.destination); }
      const freq = [0, 660, 880, 1100][Math.min(3, Math.max(1, level))];
      for (let i = 0; i < Math.min(3, level); i++) {
        const osc = ctx.createOscillator(), g = ctx.createGain();
        osc.frequency.value = freq;
        const t = ctx.currentTime + i * 0.16;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.18, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.11);
        osc.connect(g).connect(pan || ctx.destination);
        osc.start(t);
        osc.stop(t + 0.13);
      }
    },
  };
})();

function renderSound() {
  const on = settings.sound;
  $("btn-sound").setAttribute("aria-pressed", String(on));
  $("sound-text").textContent = on ? "Âm thanh kính: bật" : "Âm thanh kính: tắt";
  $("sound-icon").setAttribute("href", on ? "#i-volume" : "#i-volume-off");
}

// ---------------------------------------------------------------------------
// Dòng thời gian "server xử lý lượt hiện tại"
// ---------------------------------------------------------------------------

const STEP_HINT = { listen: "Chờ kính bấm nút", stt: "Chờ giọng nói", intent: "", action: "", reply: "" };

const timeline = {
  el: (n) => document.querySelector(`.step[data-step="${n}"]`),
  set(n, state, detail, ms) {
    const el = this.el(n);
    el.dataset.state = state;
    el.querySelector(".ms").textContent = ms == null ? "" : ms;
    const d = el.querySelector(".step-detail");
    d.replaceChildren();
    if (detail != null && detail !== "") d.append(detail);
  },
  reset() {
    for (const n of Object.keys(STEP_HINT)) this.set(n, "wait", STEP_HINT[n]);
  },
};
timeline.reset();

const pill = (text, hl) => h("span", `pill${hl ? " pill-hl" : ""}`, text);
const quote = (text) => (text ? h("span", "quote", `“${text}”`) : h("span", "quote dim", "Không nhận ra lời nói"));

function showTrace(f) {
  const t = f.timings || {};
  const ms = (k) => (typeof t[k] === "number" ? fmtSec(t[k]) : "");

  timeline.set("listen", "done", `Đã nghe${typeof t.audio_s === "number" ? ` ${fmtSec(t.audio_s)} âm thanh` : ""}`, ms("upload_s") && `tải lên ${ms("upload_s")}`);

  timeline.set("stt", "done", quote(f.transcript), ms("final_stt_s"));

  const intent = h("span", "kv");
  if (f.intent) intent.append(pill(f.intent, true));
  else intent.append(h("span", "quote dim", "Chưa xác định ý định"));
  if (f.lane) intent.append(pill(`lane ${f.lane}`));
  if (f.source) intent.append(pill(f.source));
  if (typeof f.confidence === "number") intent.append(pill(`${fmtNum(f.confidence * 100, 1)}%`));
  timeline.set("intent", "done", intent, ms("intent_s"));

  const act = h("span", "kv");
  const params = f.params && typeof f.params === "object" ? Object.entries(f.params) : [];
  for (const [k, v] of params) act.append(pill(`${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`));
  for (const c of f.cues || []) act.append(pill(`cue ${c}`));
  if (f.live) act.append(pill(`live ${f.live}`, true));
  if (!act.childNodes.length) act.append(h("span", "quote dim", "Không có hành động"));
  timeline.set("action", "done", act, ms("route_s"));

  timeline.set("reply", "active", "Đang tạo giọng nói…", ms("release_to_reply_s") && `${ms("release_to_reply_s")} từ lúc nhả`);
}

// ---------------------------------------------------------------------------
// Lịch sử hội thoại
// ---------------------------------------------------------------------------

const history = (() => {
  const root = $("history");
  const groups = new Map(); // id -> {el, user, sv, pending, ended}
  let pendingGroup = null; // lượt đang chờ câu trả lời ở lượt sau
  let liveGroup = null;

  // Bám đáy khi người xem chưa cuộn lên. Ô lịch sử co lại khi thẻ timeline phía trên cao thêm
  // (trace tới sau lượt đầu), mà scrollTop không đổi nên phải bám lại theo kích thước.
  // Sự kiện scroll tới trễ một nhịp, lúc đó nội dung có thể đã dài thêm: chỉ cuộn LÊN mới bỏ bám.
  let pinned = true;
  let prevTop = 0;
  root.addEventListener("scroll", () => {
    const atBottom = root.scrollHeight - root.scrollTop - root.clientHeight < 40;
    if (root.scrollTop < prevTop) pinned = atBottom;
    else if (atBottom) pinned = true;
    prevTop = root.scrollTop;
  });
  new ResizeObserver(() => { if (pinned) root.scrollTop = root.scrollHeight; }).observe(root);
  const stick = (fn) => {
    fn();
    if (pinned) root.scrollTop = root.scrollHeight;
  };
  const add = (el) => {
    $("history-empty")?.remove();
    root.append(el);
  };
  function svBubble(label, cls) {
    return h("div", `bubble sv ${cls || ""}`, h("span", "who", label));
  }

  return {
    startTurn(id) {
      stick(() => {
        if (pendingGroup) {
          // Lượt trước còn câu hỏi treo: nối bằng đường kẻ để thấy đây là một cuộc hội thoại nhiều lượt.
          add(h("div", "link", h("span", "", "Trả lời câu hỏi trên")));
          pendingGroup.pending.dataset.answered = "1";
          pendingGroup = null;
        }
        const user = h("div", "bubble user dim", h("span", "who", "Bạn"), h("p", "", "Đang nghe…"));
        const el = h("article", "turn", user);
        add(el);
        groups.set(id, { el, user, sv: null, pending: null, ended: false });
        liveGroup = null;
      });
    },
    transcript(id, text) {
      const g = groups.get(id);
      if (!g) return;
      stick(() => {
        g.user.classList.toggle("dim", !text);
        g.user.querySelector("p").textContent = text || "(không nhận ra lời nói)";
      });
    },
    pending(id, text) {
      const g = groups.get(id);
      if (!g || !text) return;
      stick(() => {
        g.pending = h("p", "pending", `Đang chờ trả lời: ${text}`);
        g.el.append(g.pending);
        pendingGroup = g;
      });
    },
    /** `reply_end` của lượt: câu nói sau đó mang cùng `turn` là của live (nav/OCR), không phải của lượt này. */
    endTurn(id) {
      const g = groups.get(id);
      if (g) g.ended = true;
    },
    say(turn, text) {
      if (!text) return;
      stick(() => {
        const base = String(turn || "").replace(/\.\d+$/, "");
        const result = base.startsWith("result-");
        let g = groups.get(base) || groups.get(turn);
        // Live mang id lượt đã mở nó: sau reply_end phải sang bong bóng riêng, nếu không
        // nó chui lên bong bóng cũ (sai thứ tự thời gian) và phình mãi.
        if (g && g.ended && !result) g = null;
        if (!g) {
          if (!result && liveGroup && liveGroup.turn === base) g = liveGroup;
          else {
            const sv = svBubble(result ? "Kết quả từ điện thoại" : "SeeVia · trực tiếp", result ? "is-result" : "is-live");
            const el = h("article", "turn", sv);
            add(el);
            g = { el, user: null, sv, turn: base };
            if (result) groups.set(base, g); else liveGroup = g;
          }
        }
        if (!g.sv) {
          g.sv = svBubble("SeeVia");
          g.pending ? g.el.insertBefore(g.sv, g.pending) : g.el.append(g.sv);
        }
        // Cue lặp liền nhau ("Quay sang trái." mỗi khung) gộp thành một dòng có số lần.
        const last = g.sv.lastElementChild;
        if (last && last.tagName === "P" && last.dataset.text === text) {
          const n = (Number(last.dataset.n) || 1) + 1;
          last.dataset.n = String(n);
          last.textContent = `${text} ×${n}`;
        } else {
          const p = h("p", "", text);
          p.dataset.text = text;
          g.sv.append(p);
        }
      });
    },
  };
})();

// ---------------------------------------------------------------------------
// Chế độ hiện tại + thống kê khung live
// ---------------------------------------------------------------------------

function showMode(f) {
  const on = { obstacle: !!f.obstacle, ocr: !!f.ocr, nav: !!f.navigating, music: !!f.music, call: !!f.call };
  for (const li of document.querySelectorAll("#modes .mode")) {
    const k = li.dataset.mode;
    li.dataset.on = on[k] ? "1" : "0";
    li.querySelector(".sr").textContent = on[k] ? "đang bật" : "đang tắt";
    if (k === "call") $("call-who").textContent = typeof f.call === "string" && f.call ? ` · ${f.call}` : "";
  }
}
function resetMode() {
  showMode({});
}

// ---------------------------------------------------------------------------
// Thống kê khung live: tốc độ server xử lý + lệnh server đang yêu cầu kính
// ---------------------------------------------------------------------------

const stats = {
  recv: [], // thời điểm nhận kết quả det/ocr (mỗi kết quả là một khung server vừa xử lý)
  cmd: "", // lệnh `cam` server đang yêu cầu kính, vd. "qvga · 8 fps"
  trim(now) {
    while (this.recv.length && now - this.recv[0] > 3000) this.recv.shift();
  },
  onResult() {
    this.recv.push(performance.now());
  },
  clear() {
    this.recv = [];
    this.cmd = "";
  },
  render() {
    const now = performance.now();
    this.trim(now);
    const fpsEl = $("stat-fps");
    const fps = this.recv.length > 1 ? (this.recv.length - 1) / ((this.recv[this.recv.length - 1] - this.recv[0]) / 1000) : 0;
    const active = this.cmd !== "" || this.recv.length > 0;
    fpsEl.dataset.stale = active ? "0" : "1";
    fpsEl.textContent = !active ? "live: tắt" : `live${this.cmd ? ` ${this.cmd}` : ""} · server xử lý ${fmtNum(fps)} fps`;
    renderSource();
  },
};
setInterval(() => stats.render(), 500);

// ---------------------------------------------------------------------------
// POV: khung kính gửi lên + box server trả về, vẽ chung một canvas
// ---------------------------------------------------------------------------

// `det`/`ocr` đi cùng khung JPEG nó được tính trên nên box gắn với khung chứ không có hạn dùng riêng.
const pov = { bmp: null, src: "", at: 0, det: null, ocr: null };
const overlay = $("overlay");
let dpr = 1;

const decodeJpeg = (bytes) => createImageBitmap(new Blob([bytes], { type: "image/jpeg" })).catch(() => null);

function sizeOverlay() {
  dpr = window.devicePixelRatio || 1;
  const r = overlay.getBoundingClientRect();
  overlay.width = Math.max(1, Math.round(r.width * dpr));
  overlay.height = Math.max(1, Math.round(r.height * dpr));
  drawOverlay();
}
new ResizeObserver(sizeOverlay).observe($("stage"));

// Màu vẽ box lấy từ token CSS (styles.css :root) để canvas và giao diện luôn cùng một bảng màu.
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const LV = {
  low: { color: cssVar("--lv-low"), width: 2, dash: [], alpha: 0.75 },
  2: { color: cssVar("--lv-2"), width: 3, dash: [10, 6], alpha: 1 },
  3: { color: cssVar("--lv-3"), width: 5, dash: [], alpha: 1 },
};
const INK = cssVar("--on-warn"), OCR_INK = cssVar("--cyan"), STAGE_BG = cssVar("--stage-bg");
const lvStyle = (level) => (level >= 3 ? LV[3] : level === 2 ? LV[2] : LV.low);

/** Vùng ảnh trong canvas: vừa khung (không cắt) để box khớp từng pixel ảnh kính đã gửi. */
function fitRect(W, H, iw, ih) {
  const s = Math.min(W / iw, H / ih);
  const w = iw * s, h2 = ih * s;
  return { x: (W - w) / 2, y: (H - h2) / 2, w, h: h2 };
}

function drawOverlay() {
  const ctx = overlay.getContext("2d");
  const W = overlay.width, H = overlay.height, d = dpr;
  ctx.fillStyle = STAGE_BG;
  ctx.fillRect(0, 0, W, H);
  ctx.lineJoin = "round";
  if (!pov.bmp) return;
  const R = fitRect(W, H, pov.bmp.width, pov.bmp.height);
  ctx.drawImage(pov.bmp, R.x, R.y, R.w, R.h);

  if (pov.det) {
    // Vẽ box mức thấp trước để box cảnh báo nằm trên cùng.
    const boxes = [...pov.det.boxes].sort((a, b) => (a.level || 0) - (b.level || 0));
    for (const b of boxes) {
      const [x0, y0, x1, y1] = b.box.map((v, i) => (i % 2 ? R.y + v * R.h : R.x + v * R.w));
      const st = lvStyle(b.level || 0);
      ctx.globalAlpha = st.alpha;
      ctx.setLineDash(st.dash.map((v) => v * d));
      ctx.strokeStyle = "rgba(2,10,59,.7)"; // viền tối dưới nét màu: đọc được trên mọi nền
      ctx.lineWidth = (st.width + 3) * d;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.strokeStyle = st.color;
      ctx.lineWidth = st.width * d;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.setLineDash([]);

      const label = `${b.level >= 2 ? `L${b.level} ` : ""}${b.kind} ${fmtNum(b.score, 2)}`;
      ctx.font = `600 ${12 * d}px "JetBrains Mono", monospace`;
      const tw = ctx.measureText(label).width + 12 * d, th = 20 * d;
      const ly = y0 - th >= R.y ? y0 - th : y0;
      ctx.fillStyle = st.color;
      ctx.fillRect(x0, ly, tw, th);
      ctx.fillStyle = INK;
      ctx.textBaseline = "middle";
      ctx.fillText(label, x0 + 6 * d, ly + th / 2 + d);
    }
    ctx.globalAlpha = 1;
  }

  if (pov.ocr) {
    for (const [x0p, y0p, x1p, y1p] of pov.ocr.boxes) {
      const x0 = R.x + (x0p / pov.ocr.w) * R.w, x1 = R.x + (x1p / pov.ocr.w) * R.w;
      const y0 = R.y + (y0p / pov.ocr.h) * R.h, y1 = R.y + (y1p / pov.ocr.h) * R.h;
      ctx.fillStyle = "rgba(9,187,200,.18)";
      ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
      ctx.strokeStyle = "rgba(2,10,59,.7)";
      ctx.lineWidth = 5 * d;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
      ctx.strokeStyle = OCR_INK; // Cyan chỉ dùng trên nền video tối
      ctx.lineWidth = 2.5 * d;
      ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
    }
  }
}

/** Đặt khung mới (kèm box của nó nếu có). `src`: "turn" = ảnh lúc bấm nút, "live" = khung server vừa xử lý. */
function setPov(bmp, src, extra = {}) {
  if (bmp) {
    pov.bmp?.close?.();
    pov.bmp = bmp;
    pov.src = src;
    pov.at = performance.now();
    pov.det = pov.ocr = null;
    $("stage").dataset.frame = "1";
    $("stat-size").textContent = `${bmp.width}×${bmp.height}`;
  }
  Object.assign(pov, extra);
  drawOverlay();
  renderSource();
}

function renderSource() {
  const stale = !!pov.bmp && pov.src === "live" && performance.now() - pov.at > STALE_MS;
  $("stage").dataset.stale = stale ? "1" : "0";
  $("stat-size").hidden = !pov.bmp;
  const el = $("stat-src");
  el.hidden = !pov.bmp;
  el.textContent = stale ? "Khung cuối, kính đã ngừng gửi" : pov.src === "turn" ? "Ảnh kính gửi lúc bấm nút" : "Khung live server vừa xử lý";
}

function clearPov() {
  pov.bmp?.close?.();
  Object.assign(pov, { bmp: null, src: "", at: 0, det: null, ocr: null });
  $("stage").dataset.frame = "0";
  $("stat-size").textContent = "--";
  drawOverlay();
  renderSource();
}

const CUE_VI = {
  look_left: ["Quay trái", "i-arrow-left"],
  look_right: ["Quay phải", "i-arrow-right"],
  look_up: ["Hướng lên", "i-arrow-up"],
  look_down: ["Hướng xuống", "i-arrow-down"],
  look_around: ["Nhìn quanh", "i-eye"],
  move_closer: ["Lại gần", "i-zoom-in"],
  move_away: ["Ra xa", "i-zoom-out"],
  hold_still: ["Giữ yên", "i-hand"],
  ocr_no_text: ["Không thấy chữ", "i-eye"],
  ocr_nothing_to_reread: ["Chưa có gì để đọc lại", "i-eye"],
  captured: ["Đã chụp ảnh nét", "i-camera"],
};

const DIR_VI = { left: "bên trái", center: "phía trước", right: "bên phải" };
const DIR_ICON = { left: "i-arrow-left", center: "i-arrow-up", right: "i-arrow-right" };

const timers = {};
function showFor(key, el, ms, fn) {
  fn?.();
  el.hidden = false;
  clearTimeout(timers[key]);
  timers[key] = setTimeout(() => (el.hidden = true), ms);
}
function retrigger(el, cls, ms) {
  el.className = el.className.replace(/\bgo\d?\b/g, "").trim();
  void el.offsetWidth; // khởi động lại animation
  el.classList.add(cls);
  clearTimeout(timers[`rt-${el.id}`]);
  timers[`rt-${el.id}`] = setTimeout(() => el.classList.remove(cls), ms);
}

function ocrChip(cue) {
  const [text, icon] = CUE_VI[cue] || [cue, "i-eye"];
  showFor("ocr", $("ocr-chip"), 2200, () => {
    $("ocr-text").textContent = text;
    $("ocr-icon").setAttribute("href", `#${icon}`);
  });
}

function onDet(f, bmp) {
  stats.onResult();
  setPov(bmp, "live", { det: { boxes: Array.isArray(f.boxes) ? f.boxes : [] } });
  if (f.sent && f.hazard) {
    const { level, dir, kind } = f.hazard;
    retrigger($("ring"), `go${Math.min(3, Math.max(1, level))}`, 1500);
    const hz = $("hz");
    showFor("hz", hz, 1800, () => {
      hz.dataset.dir = DIR_VI[dir] ? dir : "center";
      hz.dataset.level = String(level);
      $("hz-icon").setAttribute("href", `#${DIR_ICON[dir] || "i-arrow-up"}`);
      $("hz-text").textContent = `Mức ${level} · ${kind} ${DIR_VI[dir] || ""}`.trim();
    });
    speaker.beep(level, dir);
    hazardLog(f.hazard);
  }
}

function hazardLog({ level, dir, kind }) {
  const list = $("hlog");
  list.querySelector(".empty")?.remove();
  list.prepend(
    h("li", "", h("span", "t", clock()), h("span", `lv lv-${level}`, `L${level}`), h("span", "", `${kind} · ${DIR_VI[dir] || dir}`)),
  );
  while (list.children.length > 30) list.lastChild.remove();
}

function onOcr(f, bmp) {
  stats.onResult();
  setPov(bmp, "live", { ocr: { w: f.w || 1, h: f.h || 1, boxes: Array.isArray(f.boxes) ? f.boxes : [] } });
  if (f.cue) ocrChip(f.cue);
  if (f.capture) {
    retrigger($("flash"), "go", 400);
    if (!f.cue) ocrChip("captured");
  }
}

// ---------------------------------------------------------------------------
// Trạng thái kết nối + kính
// ---------------------------------------------------------------------------

function setConn(state, text) {
  $("conn-dot").dataset.state = state;
  $("conn-text").textContent = text;
  renderEmpty();
}

const glasses = { online: false, device: "" };

function setGlasses(online, device = "") {
  glasses.online = online;
  glasses.device = online ? device : glasses.device;
  const el = $("glasses");
  el.dataset.state = online ? "on" : "off";
  el.querySelector(".dot").dataset.state = online ? "ok" : "off";
  $("glasses-text").textContent = online ? `Kính đã kết nối${device ? ` · ${device}` : ""}` : "Kính chưa kết nối";
  renderEmpty();
}

/** Chữ hướng dẫn khi chưa có hình: nói rõ đang thiếu mắt xích nào (server hay kính). */
function renderEmpty() {
  const msg = !session.ready
    ? "Chưa nối server. Kiểm tra Cài đặt."
    : !glasses.online
      ? "Kính chưa kết nối. Bật kính để bắt đầu."
      : "Kính đã kết nối. Bấm nút trên kính để gửi hình.";
  $("stage-empty-msg").textContent = msg;
}

// Trạng thái lượt hiện tại: id do KÍNH sinh (turn_start), trang chỉ quan sát.
const cur = { id: null, pressAt: 0, releaseAt: 0, firstAudio: false, said: 0, listening: false, tick: 0 };

function listening(on) {
  cur.listening = on;
  clearInterval(cur.tick);
  $("rec-badge").hidden = !on;
  $("stage").dataset.rec = on ? "1" : "0";
  if (!on) return;
  const update = () => {
    const secs = (performance.now() - cur.pressAt) / 1000;
    $("rec-time").textContent = `${fmtNum(secs)} s`;
    timeline.el("listen").querySelector(".ms").textContent = `${fmtNum(secs)} s`;
  };
  update();
  cur.tick = setInterval(update, 100);
}

function resetGlassesState() {
  listening(false);
  stats.clear();
  resetMode();
  speaker.stop();
}

// ---------------------------------------------------------------------------
// Phiên WebSocket xem
// ---------------------------------------------------------------------------

function wsUrl() {
  const b = settings.base.trim();
  const tail = "/v2/observe";
  if (/^wss?:\/\//i.test(b)) return b.replace(/\/+$/, "") + tail;
  if (/^https?:\/\//i.test(b)) return b.replace(/^http/i, "ws").replace(/\/+$/, "") + tail;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${location.host}${b.startsWith("/") ? b : `/${b}`}${tail}`;
}

let pendingFrame = null; // JPEG 0x04 đang giải mã: JSON det/ocr ngay sau nó là của khung này
let frameChain = Promise.resolve(); // det/ocr xử lý lần lượt theo đúng thứ tự tới

const session = {
  ws: null, ready: false, retry: 0, retryTimer: null, pingTimer: null, stopped: false,
  get open() { return this.ws?.readyState === WebSocket.OPEN; },

  connect() {
    clearTimeout(this.retryTimer);
    this.stopped = false;
    this.ready = false;
    setConn("wait", "Đang kết nối…");
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      return setConn("down", "Địa chỉ server không hợp lệ");
    }
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      const hello = { t: "hello", token: settings.token }; // token rỗng: proxy gắn token cấu hình sẵn
      const device = settings.device || serverDevice;
      if (device) hello.device = device;
      ws.send(JSON.stringify(hello));
    };
    ws.onmessage = (e) => (typeof e.data === "string" ? this.onText(e.data) : this.onBinary(new Uint8Array(e.data)));
    ws.onclose = (e) => {
      if (this.ws !== ws) return;
      this.onClose(e.code);
    };
  },

  close() {
    this.stopped = true;
    clearTimeout(this.retryTimer);
    clearInterval(this.pingTimer);
    const ws = this.ws;
    this.ws = null;
    this.ready = false;
    try { ws?.close(1000); } catch { /* đã đóng */ }
  },

  onClose(code) {
    clearInterval(this.pingTimer);
    this.ready = false;
    this.ws = null;
    setGlasses(false);
    resetGlassesState();
    if (this.stopped) return;
    if (code === 4401) return setConn("down", "Token sai hoặc thiếu. Mở Cài đặt để nhập token");
    if (code === 4400) return setConn("down", "Server từ chối khung hello (lỗi giao thức)");
    // Lùi dần 0,5 -> 8 s, nhiễu ±20%.
    const wait = Math.min(8000, 500 * 2 ** this.retry++) * (0.8 + Math.random() * 0.4);
    setConn("down", `Mất kết nối. Thử lại sau ${fmtNum(wait / 1000)} s`);
    this.retryTimer = setTimeout(() => this.connect(), wait);
  },

  sendJson(obj) {
    if (this.open) this.ws.send(JSON.stringify(obj));
  },

  onBinary(buf) {
    if (buf.length < 2) return;
    switch (buf[0]) {
      case 0x01: // ảnh kính gửi lúc bấm nút
        decodeJpeg(buf.subarray(1)).then((bmp) => bmp && setPov(bmp, "turn"));
        break;
      case 0x04: // khung live server vừa xử lý; JSON det/ocr của nó đến ngay sau
        pendingFrame = decodeJpeg(buf.subarray(1));
        break;
      case 0x02: // tiếng server trả về kính
        if (!cur.firstAudio && cur.releaseAt) {
          cur.firstAudio = true;
          const d = timeline.el("reply").querySelector(".step-detail");
          d.append(pill(`tiếng đầu sau ${fmtMs(performance.now() - cur.releaseAt)} (đo ở trang xem)`, true));
        }
        speaker.push(buf.subarray(1));
        if (settings.sound && media.ctx?.state === "suspended") setNote("Chạm vào trang một lần để bật âm thanh.");
        break;
      default: break;
    }
  },

  onText(raw) {
    let f;
    try { f = JSON.parse(raw); } catch { return; }
    switch (f.t) {
      case "hello_ok":
        this.ready = true;
        this.retry = 0;
        setConn("ok", "Đã nối server · đang xem");
        setGlasses(!!f.glasses);
        clearInterval(this.pingTimer);
        this.pingTimer = setInterval(() => this.sendJson({ t: "ping" }), PING_MS);
        break;
      case "glasses":
        setGlasses(!!f.online, f.device);
        if (!f.online) { resetGlassesState(); renderSource(); }
        break;
      case "turn_start": { // kính vừa bấm nút
        speaker.stop();
        Object.assign(cur, { id: f.turn, pressAt: performance.now(), releaseAt: 0, firstAudio: false, said: 0 });
        timeline.reset();
        timeline.set("listen", "active", "Kính đang nghe người dùng…");
        history.startTurn(f.turn);
        listening(true);
        break;
      }
      case "turn_end":
        if (f.turn !== cur.id) break;
        listening(false);
        cur.releaseAt = performance.now();
        timeline.set("listen", "done", `Đã nghe ${fmtNum((cur.releaseAt - cur.pressAt) / 1000)} s`, "");
        timeline.set("stt", "active", "Server đang nhận dạng…");
        break;
      case "trace":
        history.transcript(f.turn, f.transcript);
        history.pending(f.turn, f.pending);
        if (f.turn === cur.id) showTrace(f);
        break;
      case "say":
        history.say(f.turn, f.text);
        // Chỉ câu của chính lượt này (trước reply_end); câu live sau đó chỉ vào lịch sử.
        if (f.turn === cur.id && f.text && timeline.el("reply").dataset.state !== "done") {
          const d = timeline.el("reply").querySelector(".step-detail");
          if (d.firstChild && d.firstChild.nodeType === 3) d.replaceChildren();
          cur.said += 1;
          if (cur.said <= 2) d.append(h("span", "quote", `“${f.text}” `));
          else { // câu đọc dài (OCR): timeline chỉ giữ 2 câu đầu, phần còn lại xem ở lịch sử
            let more = d.querySelector(".more");
            if (!more) d.append((more = h("span", "pill more", "")));
            more.textContent = `+${cur.said - 2} câu nữa (xem lịch sử)`;
          }
        }
        break;
      case "reply_end":
        history.endTurn(f.turn);
        if (f.turn === cur.id) timeline.el("reply").dataset.state = "done";
        break;
      case "det":
      case "ocr": {
        const frame = pendingFrame;
        pendingFrame = null;
        frameChain = frameChain.then(async () => {
          const bmp = frame ? await frame : null;
          if (f.t === "det") onDet(f, bmp); else onOcr(f, bmp);
        });
        break;
      }
      case "mode": showMode(f); break;
      case "cam": stats.cmd = `${f.size || "?"} · ${f.fps || "?"} fps`; stats.render(); break;
      case "end": stats.cmd = ""; stats.render(); break;
      case "capture": case "hazard": case "reply": case "cue": case "call_state": case "pong": break; // đã thể hiện qua det/ocr/say
      case "error": setNote(`Server báo lỗi: ${f.code}`); setTimeout(() => setNote(""), 4000); break;
      default: break;
    }
  },
};

// ---------------------------------------------------------------------------
// Gắn sự kiện
// ---------------------------------------------------------------------------

// Chạm lần đầu mở khoá âm thanh (trình duyệt chặn phát tự động trước cử chỉ).
document.addEventListener("pointerdown", () => {
  if (!settings.sound) return;
  audioCtx().resume().then(() => $("note").textContent.startsWith("Chạm vào trang") && setNote("")).catch(() => {});
}, { passive: true });

$("btn-sound").addEventListener("click", () => {
  settings.sound = !settings.sound;
  saveSettings();
  if (!settings.sound) speaker.stop();
  renderSound();
});

const dlg = $("dlg-settings");
document.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
$("btn-settings").addEventListener("click", () => {
  $("set-base").value = settings.base;
  $("set-token").value = settings.token;
  $("set-device").value = settings.device;
  $("set-device").placeholder = serverDevice ? `để trống = ${serverDevice}` : "để trống = xem kính đang nối";
  dlg.showModal();
});
$("set-save").addEventListener("click", () => {
  settings.base = $("set-base").value.trim().replace(/\/+$/, "") || DEFAULTS.base;
  settings.token = $("set-token").value.trim();
  settings.device = $("set-device").value.trim();
  saveSettings();
  session.close();
  clearPov();
  session.connect();
});

// ---------------------------------------------------------------------------
// Khởi động
// ---------------------------------------------------------------------------

renderSound();
renderSource();
renderEmpty();
fetch("config.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : {}))
  .then((c) => { serverDevice = typeof c.device === "string" ? c.device : ""; })
  .catch(() => {}) // mở bằng file:// hoặc server cũ: dùng giá trị nhập ở Cài đặt
  .finally(() => session.connect());
