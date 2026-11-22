/*
 * Bộ xử lý API giả lập: nhận (method, path, query, body, token) và trả { status, body } với
 * CÙNG hình dạng như backend thật (xem docs/API_CONTRACT.md). Thuần JS, không đụng DOM.
 */
import * as D from "./data.js";

const MS_DAY = 86400000;
const utc = iso => { const [y, m, d] = iso.split("-").map(Number); return Date.UTC(y, m - 1, d); };
const iso = ms => new Date(ms).toISOString().slice(0, 10);
const addDays = (s, n) => iso(utc(s) + n * MS_DAY);
const weekdayOf = s => { const d = new Date(utc(s)).getUTCDay(); return d === 0 ? 7 : d; };
const jit = (i, seed = 1) => Math.abs((Math.sin(i * 12.9898 + seed * 78.233) * 43758.5453) % 1);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const ok = body => ({ status: 200, body });
const fail = (status, code, message) => ({ status, body: { error: { code, message } } });

// Chia `total` theo tỷ lệ `weights`, tổng đúng bằng `total` (phần dư lớn nhất).
export function distribute(total, weights) {
  const sum = weights.reduce((a, b) => a + b, 0);
  if (!sum || total <= 0) return weights.map(() => 0);
  const raw = weights.map(w => (w / sum) * total);
  const out = raw.map(Math.floor);
  let left = Math.round(total) - out.reduce((a, b) => a + b, 0);
  raw.map((r, i) => [r - Math.floor(r), i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0) { out[i] += 1; left -= 1; } });
  return out;
}

function periodOf(query) {
  const today = new Date(Date.now() + 7 * 3600000).toISOString().slice(0, 10);
  const from = /^\d{4}-\d{2}-\d{2}$/.test(query.from || "") ? query.from : addDays(today, 1 - weekdayOf(today));
  const to = /^\d{4}-\d{2}-\d{2}$/.test(query.to || "") ? query.to : addDays(from, 6);
  const days = Math.max(1, Math.round((utc(to) - utc(from)) / MS_DAY) + 1);
  const dates = Array.from({ length: days }, (_, i) => addDays(from, i));
  // Chỉ các ngày/giờ ĐÃ QUA mới có số liệu: kỳ chưa kết thúc (tuần này, tháng này, hôm nay) không có dữ liệu tương lai.
  const nowVn = new Date(Date.now() + 7 * 3600000);
  const elapsed = clamp(Math.round((utc(today) - utc(from)) / MS_DAY) + 1, 0, days);
  const nowHour = nowVn.getUTCHours(), hourFrac = (nowVn.getUTCMinutes() * 60 + nowVn.getUTCSeconds()) / 3600;   // giờ đang chạy tính theo phần đã qua → "Hôm nay" tăng dần
  const exactWeek = false;   // số liệu luôn được phân bổ theo quy mô VOLUME, không dùng mảng cố định của tuần thiết kế
  // Hôm nay: chỉ tính phần trăm số lượt đã diễn ra tới giờ hiện tại (theo hình dạng trong ngày).
  const shape = D.HOUR_SHAPE, dayShare = from === to && from === today ? (shape.slice(0, nowHour).reduce((x, y) => x + y, 0) + shape[nowHour] * hourFrac) / shape.reduce((x, y) => x + y, 0) : 1;
  return {
    period: query.period || "week", from, to, days, dates, exactWeek, elapsed, today, nowHour, hourFrac, K: ((from === to ? dayShare : elapsed) / 7) * D.VOLUME,
    compare_from: addDays(from, -days), compare_to: addDays(from, -1),
  };
}
const scale = (v, K) => (v === null || v === undefined ? v : Math.round(v * K));
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const pct = (cur, prev) => (prev ? Math.round(((cur - prev) / prev) * 100) : null);
export const GROUP_KEYS = ["see_read", "find", "connect", "go"];
const PAGE_OF = { see_read: "see-read", find: "find", connect: "connect", go: "go" };
const rangeOf = p => ({ period: p.period, from: p.from, to: p.to });

// ---- Chuỗi theo thời gian dùng chung cho Tổng quan, Thời điểm, Gắn bó ----
function seriesFor(p) {
  const total = Math.round(D.BASE_USES * p.K);
  const hourly = p.from === p.to;
  let buckets, uses, prev, owners, prevOwners;
  if (hourly) {
    buckets = Array.from({ length: 24 }, (_, h) => `${p.from}T${String(h).padStart(2, "0")}:00:00+07:00`);
    const live = p.from === p.today ? D.HOUR_SHAPE.map((w, h) => (h < p.nowHour ? w : h === p.nowHour ? w * p.hourFrac : 0)) : D.HOUR_SHAPE;   // giờ chưa tới: chưa có dữ liệu
    uses = distribute(total, live);
    prev = distribute(Math.round(D.BASE_USES * (p.days / 7) * D.VOLUME * 0.93), D.HOUR_SHAPE.map((w, i) => w * (0.8 + 0.4 * jit(i, 3))));
    owners = uses.map(u => (u ? Math.min(D.DAILY_OWNERS, Math.ceil(u / 12)) : 0));
    prevOwners = prev.map(u => (u ? Math.min(3, Math.ceil(u / 6)) : 0));
  } else {
    buckets = p.dates;
    if (p.exactWeek) { uses = [...D.WEEK_USES]; prev = [...D.WEEK_PREV]; owners = [...D.WEEK_OWNERS]; }
    else {
      const w = p.dates.map((d, i) => (i < p.elapsed ? D.WEEK_USES[weekdayOf(d) - 1] * (0.85 + 0.3 * jit(i, 1)) : 0));
      uses = distribute(total, w);
      prev = distribute(Math.round(D.BASE_USES * (p.days / 7) * D.VOLUME * 0.93), p.dates.map((d, i) => D.WEEK_USES[weekdayOf(d) - 1] * (0.85 + 0.3 * jit(i, 2))));
      const top = Math.max(...uses, 1);
      owners = uses.map(u => (u ? clamp(Math.round(D.DAILY_OWNERS * (0.55 + 0.45 * (u / top))), 1, D.ACCOUNTS.length) : 0));
    }
    prevOwners = owners.map((o, i) => clamp(o + ((i % 5) - 2), 0, D.ACCOUNTS.length));
  }
  const usage = distribute(Math.round(D.BASE_USAGE_SECONDS * p.K), uses);
  const usagePrev = distribute(Math.round(D.BASE_USAGE_SECONDS * (p.days / 7) * D.VOLUME * 0.89), prev);
  const comp = uses.map((u, i) => (u ? Math.round(87 + (jit(i, 4) - 0.5) * 8) : null));
  const compPrev = prev.map((u, i) => (u ? Math.round(86 + (jit(i, 5) - 0.5) * 8) : null));
  // Mốc chưa tới: phía "kỳ này" là null (chưa có dữ liệu); phía "kỳ trước" giữ nguyên.
  const future = i => (hourly ? p.from === p.today && i > p.nowHour : i >= p.elapsed);
  const mask = a => a.map((v, i) => (future(i) ? null : v));
  return { hourly, buckets, uses: mask(uses), prev, owners: mask(owners), prevOwners, usage: mask(usage), usagePrev, comp: mask(comp), compPrev };
}

// Phân bổ lượt: tổng kỳ = các nhóm + SOS; mỗi nhóm = tổng lượt các chức năng của nhóm.
function allocation(p) {
  const total = Math.round(D.BASE_USES * p.K), sos = scale(D.SAFETY.sos.count, p.K);
  const g = distribute(total - sos, GROUP_KEYS.map(k => D.GROUP_USES[k]));
  const groups = Object.fromEntries(GROUP_KEYS.map((k, i) => [k, g[i]]));
  const featUses = {};
  GROUP_KEYS.forEach(k => {
    const feats = D.REPORTS[PAGE_OF[k]].features;
    const u = distribute(groups[k], feats.map(f => f.uses));
    feats.forEach((f, i) => { featUses[f.key] = u[i]; });
  });
  return { total, sos, groups, featUses };
}
function errorRows(p) {
  return D.ERROR_ROWS.map(e => ({ ...e, count: scale(e.count, p.K), change: scale(e.change, p.K) })).filter(e => e.count > 0);
}

// Số chủ kính không trùng trong cả kỳ (tuần/tháng lớn hơn số trong một ngày, tối đa ~92% tổng tài khoản).
function uniqueOwners(s, p) {
  const day = Math.max(0, ...s.owners);
  if (s.hourly || p.elapsed <= 1) return day;
  const grow = 1 + 0.4 * Math.min(1, (p.elapsed - 1) / 6) + 0.2 * Math.min(1, Math.max(0, p.elapsed - 7) / 23);
  return Math.min(Math.round(D.ACCOUNTS.length * 0.92), Math.round(day * grow));
}

function overview(p) {
  const s = seriesFor(p), al = allocation(p);
  const usesTotal = sum(s.uses), prevTotal = sum(s.prev.filter((_, i) => s.uses[i] !== null));   // so sánh với cùng khoảng thời gian đã diễn ra của kỳ trước
  const attention = [{ type: "error_increase", error_code: "net_timeout", feature: "ocr", delta: 3 }];
  if (al.sos > 0) attention.push({ type: "sos", count: al.sos, cancelled: scale(D.SAFETY.sos.cancelled_within_5s, p.K) });
  attention.push({ type: "device_offline", device_id: "KINH-02", since: "2026-11-14T21:10:00+07:00" });
  return {
    range: { ...rangeOf(p), compare_from: p.compare_from, compare_to: p.compare_to },
    totals: {
      usage_seconds_per_owner: Math.round(sum(s.usage) / Math.max(1, uniqueOwners(s, p))),
      uses: usesTotal, active_owners: uniqueOwners(s, p), completion_rate: usesTotal ? 87 : null, usage_seconds: sum(s.usage),
      uses_change_pct: pct(usesTotal, prevTotal), active_owners_change: 0, completion_rate_change_points: usesTotal ? 2 : null, usage_seconds_change_pct: usesTotal ? 12 : null,
    },
    series: {
      granularity: s.hourly ? "hour" : "day",
      points: s.buckets.map((bucket, i) => ({
        bucket, uses: s.uses[i], uses_prev: s.prev[i], active_owners: s.owners[i], active_owners_prev: s.prevOwners[i],
        completion_rate: s.comp[i], completion_rate_prev: s.compPrev[i], usage_seconds: s.usage[i], usage_seconds_prev: s.usagePrev[i],
      })),
    },
    groups: GROUP_KEYS.map(key => ({ key, uses: al.groups[key] })), sos_uses: al.sos,
    top_features: D.TOP_FEATURES.map(f => ({ ...f, uses: al.featUses[f.key] })),
    attention,
  };
}

function report(name, p) {
  const src = D.REPORTS[name], al = allocation(p), rows = errorRows(p);
  const groupKey = GROUP_KEYS.find(k => PAGE_OF[k] === name), groupUses = al.groups[groupKey];
  const summary = {};
  for (const [k, v] of Object.entries(src.summary)) summary[k] = D.COUNT_KEYS.has(k) ? scale(v, p.K) : v;
  summary.uses = groupUses;
  if (!groupUses) { summary.completion_rate = null; summary.usage_seconds = 0; if ("alert_mode_seconds" in summary) summary.alert_mode_seconds = 0; }
  summary.share_of_total_pct = al.total ? Math.round((groupUses / al.total) * 100) : null;
  return {
    range: rangeOf(p), summary,
    features: src.features.map(f => {
      const details = {};
      for (const [k, v] of Object.entries(f.details)) details[k] = D.COUNT_KEYS.has(k) ? scale(v, p.K) : v;
      const mine = rows.filter(r => r.feature === f.key).sort((a, b) => b.count - a.count);
      const out = { ...f, uses: al.featUses[f.key], errors: mine.reduce((a, r) => a + r.count, 0), details };
      if (!out.uses) { out.completion_rate = null; out.first_audio_p50_ms = null; }
      if (mine.length) out.top_error = { code: mine[0].code, count: mine[0].count };
      return out;
    }),
  };
}

function community(p) {
  const c = D.COMMUNITY, K = p.K;
  const sessions = scale(c.glasses_sessions, K), attempts = Math.round(c.install_attempts * K), ok = Math.min(attempts, Math.round(c.install_successes * K));
  return {
    range: rangeOf(p), ...c, total_accounts: D.ACCOUNTS.length, emergency_contact_accounts: Math.round(D.ACCOUNTS.length * 0.5),
    install_attempts: attempts, install_successes: ok, install_success_rate: attempts ? Math.round((ok / attempts) * 100) : null,
    install_errors: attempts - ok > 0 ? [{ code: "wifi_5ghz", count: attempts - ok }] : [],
    glasses_sessions: sessions, glasses_session_seconds: sessions ? scale(c.glasses_session_seconds, K) : 0,
    glasses_active_days: sessions ? Math.min(p.elapsed || 1, c.glasses_active_days) : 0,
  };
}

// Ngày (hoặc giờ khi kỳ là một ngày) có nhiều lượt nhất trong kỳ; chỉ xét phần đã diễn ra. Hòa thì lấy ngày sớm nhất.
function peakDay(s, p) {
  if (s.hourly) return null;
  let best = -1;
  s.uses.forEach((u, i) => { if (u !== null && (best < 0 || u > s.uses[best])) best = i; });
  return best < 0 ? null : { date: p.dates[best], uses: s.uses[best] };
}

function time(p) {
  const s = seriesFor(p);
  // Tổng theo thứ lấy từ chính số lượt theo ngày → heatmap, theo ngày và tổng kỳ luôn khớp nhau.
  const totals = [0, 0, 0, 0, 0, 0, 0];
  if (s.hourly) totals[weekdayOf(p.from) - 1] = sum(s.uses); else p.dates.forEach((d, i) => { totals[weekdayOf(d) - 1] += s.uses[i]; });
  const heat = totals.map(t => distribute(t, D.HOUR_SHAPE));
  const best = totals.indexOf(Math.max(...totals));
  return {
    range: rangeOf(p),
    peak_hours: { start_hour: 7, end_hour: 9, secondary_start_hour: 17, secondary_end_hour: 20 },
    busiest_day: peakDay(s, p),
    weekend_uses: totals[5] + totals[6],
    by_day: s.hourly ? [{ date: p.from, weekday: weekdayOf(p.from), uses: sum(s.uses) }] : p.dates.map((date, i) => ({ date, weekday: weekdayOf(date), uses: s.uses[i] })),
    heatmap: { weekdays: [1, 2, 3, 4, 5, 6, 7], hours: Array.from({ length: 24 }, (_, h) => h), cells: heat },
    by_hour: s.hourly ? s.uses.map((uses, hour) => ({ hour, uses })) : Array.from({ length: 24 }, (_, hour) => ({ hour, uses: sum(heat.map(r => r[hour])) })),
    // Theo tuần (Thứ Hai → Chủ Nhật), chỉ khi kỳ dài hơn một tuần; tuần ở đầu/cuối kỳ có thể chưa đủ 7 ngày.
    by_week: p.days > 7 ? weeksOf(p, s) : [],
  };
}

function weeksOf(p, s) {
  const out = [];
  p.dates.forEach((date, i) => {
    const start = addDays(date, 1 - weekdayOf(date));
    let w = out.find(x => x.key === start);
    if (!w) { w = { key: start, week_start: date, week_end: date, uses: null }; out.push(w); }
    w.week_end = date;
    if (s.uses[i] !== null) w.uses = (w.uses ?? 0) + s.uses[i];
  });
  return out.map(({ key, ...rest }) => rest);
}

function quality(p) {
  const q = D.QUALITY, total = Math.round(D.BASE_USES * p.K);
  const outcomes = distribute(total, q.outcomes.map(o => o[1]));
  const attemptsTotal = Math.round(77 * p.K);
  const attempts = distribute(attemptsTotal, q.attempts.map(o => o[1]));
  const share = (v, t) => (t ? Math.round((v / t) * 100) : null);
  return {
    range: rangeOf(p), total_uses: total,
    completion: total ? q.completion : { rate: null, rate_change_points: null, first_try_rate: null, first_try_change_points: null, avg_retries: null, abandon_rate: null },
    speed: total ? q.speed : { first_audio_p50_ms: null, first_audio_p90_ms: null, final_result_p50_ms: null },
    outcomes: q.outcomes.map(([key], i) => ({ key, uses: outcomes[i], share_pct: share(outcomes[i], total) })),
    attempts_total: attemptsTotal,
    attempts: q.attempts.map(([key], i) => ({ key, count: attempts[i], share_pct: share(attempts[i], attemptsTotal) })),
  };
}

// ---- Chi phí AI: dòng như bảng usage_llm_daily của AB, gộp + tính tiền theo đúng quy tắc của AB (services/llm_cost.py) ----
const LLM_PRICES = [
  { model: "gemini-3.8-flash", to: "2026-12-31", input: 0.75, output: 3.75, cached: 0.075 },
  { model: "gemini-3.8-flash", from: "2027-01-01", input: 1.5, output: 7.5, cached: 0.15 },
];
const LLM_FREE = new Set(["uit", "9router"]);
const LLM_COMPARE = "gemini-3.8-flash";
// [chức năng, tỷ trọng lượt, token vào / lượt, token ra / lượt, ảnh / lượt]
const LLM_FEATURES = [["ocr", 81, 1450, 170, 1], ["space", 38, 1180, 120, 1], ["money", 31, 520, 6, 1], ["ask", 25, 1260, 140, 1], ["chat", 24, 820, 160, 0],
  ["find", 20, 1150, 80, 1], ["call", 18, 380, 18, 0], ["message_send", 14, 520, 40, 0], ["music", 10, 300, 12, 0], ["navigation", 8, 340, 20, 0]];
const baseModel = m => String(m).split("/").pop().toLowerCase().replace(/-(minimal|low|medium|high)$/, "");
const priceOf = (model, day) => LLM_PRICES.filter(x => x.model === baseModel(model) && (!x.from || day >= x.from) && (!x.to || day <= x.to)).pop() || null;
function llmCost(price, row) {
  if (!price) return null;
  const cached = Math.min(row.cached_tokens, row.in_tokens);
  return ((row.in_tokens - cached) * price.input + cached * (price.cached ?? price.input) + row.out_tokens * price.output) / 1e6;
}
function llmRows(p) {
  const rows = [], uses = Math.round(D.BASE_USES * p.K);
  const perDay = distribute(uses, p.dates.map((d, i) => (i < p.elapsed ? D.WEEK_USES[weekdayOf(d) - 1] : 0)));
  const add = (day, feature, provider, model, purpose, status, calls, attempts, inT, outT, extra = {}) => {
    if (calls > 0) rows.push({ day, feature, provider, model, purpose, status, calls, attempts, in_tokens: Math.round(inT), out_tokens: Math.round(outT),
      reasoning_tokens: extra.reasoning || 0, cached_tokens: Math.round(extra.cached || 0), images: extra.images || 0, estimated_calls: extra.estimated || 0, latency_ms_sum: Math.round(extra.latency || 0) });
  };
  p.dates.forEach((day, di) => {
    if (!perDay[di]) return;
    const split = distribute(perDay[di], LLM_FEATURES.map(f => f[1]));
    LLM_FEATURES.forEach(([feature, , inT, outT, img], fi) => {
      const n = split[fi]; if (!n) return;
      const trial = Math.round(n * 0.05), main = n - trial;   // ~5% lượt thử nhánh Gemini qua 9router
      const answer = feature === "money" ? "money" : feature === "ocr" ? "ocr" : "answer";
      add(day, feature, "uit", "gemma-4-26b", "classify", "ok", main, main, main * 310, main * 3, { latency: main * 380 });
      if (["call", "message_send", "music", "navigation"].includes(feature)) add(day, feature, "uit", "gemma-4-26b", "extract", "ok", main, 0, main * 420, main * 28, { latency: main * 520 });
      add(day, feature, "uit", "gemma-4-26b", answer, "ok", main, 0, main * inT, main * outT, { images: main * img, cached: main * inT * 0.35, latency: main * 1250 });
      const cancelled = Math.round(main * 0.12 * jit(di + fi, 6));
      add(day, feature, "uit", "gemma-4-26b", "answer", "cancelled", cancelled, 0, cancelled * inT, cancelled * outT * 0.3, { estimated: cancelled, latency: cancelled * 600 });
      add(day, feature, "9router", "ag/gemini-3.8-flash-low", answer, "ok", trial, trial, trial * (inT + 2000), trial * (outT + 90), { reasoning: trial * 90, images: trial * img, latency: trial * 3100 });
    });
    const errors = Math.round(perDay[di] * 0.02);
    add(day, "chat", "uit", "gemma-4-26b", "answer", "error", errors, 0, 0, 0, { latency: errors * 900 });
    const warm = Math.round(perDay[di] * 0.4);
    add(day, "none", "uit", "gemma-4-26b", "prewarm", "ok", warm, 0, warm * 900, warm, { latency: warm * 220 });
  });
  return rows;
}
function llm(p) {
  const rows = llmRows(p);
  const blank = () => ({ calls: 0, estimated_calls: 0, in_tokens: 0, out_tokens: 0, reasoning_tokens: 0, cached_tokens: 0, images: 0, latency_ms_sum: 0, cost_actual: 0, cost_list: 0, cost_what_if: 0, unpriced_calls: 0 });
  const SUMS = ["calls", "estimated_calls", "in_tokens", "out_tokens", "reasoning_tokens", "cached_tokens", "images", "latency_ms_sum"];
  const r6 = v => Math.round(v * 1e6) / 1e6;
  const finish = acc => { const { latency_ms_sum, ...rest } = acc; return { ...rest, avg_latency_ms: acc.calls ? Math.round(latency_ms_sum / acc.calls) : null, cost_actual: r6(acc.cost_actual), cost_list: r6(acc.cost_list), cost_what_if: r6(acc.cost_what_if) }; };
  const totals = blank(), groups = { day: new Map(), feature: new Map(), purpose: new Map(), model: new Map() };
  const waste = { cancelled_calls: 0, cancelled_tokens: 0, cancelled_cost_list: 0, cancelled_cost_what_if: 0, prewarm_calls: 0, prewarm_tokens: 0, prewarm_cost_list: 0, prewarm_cost_what_if: 0, error_calls: 0 };
  const attemptsBy = {};
  let altCost = 0, altPriced = false;
  for (const row of rows) {
    const listed = llmCost(priceOf(row.model, row.day), row), actual = LLM_FREE.has(row.provider) ? 0 : listed || 0;
    const alt = llmCost(priceOf(LLM_COMPARE, row.day), row) || 0;
    const keys = { day: row.day, feature: row.feature, purpose: row.purpose, model: `${row.provider}\u0000${row.model}` };
    for (const acc of [totals, ...Object.entries(keys).map(([g, k]) => { if (!groups[g].has(k)) groups[g].set(k, blank()); return groups[g].get(k); })]) {
      SUMS.forEach(k => { acc[k] += row[k]; });
      acc.cost_actual += actual; acc.cost_what_if += alt;
      if (listed === null) acc.unpriced_calls += row.calls; else acc.cost_list += listed;
    }
    const tokens = row.in_tokens + row.out_tokens;
    if (row.status === "error") waste.error_calls += row.calls;
    for (const kind of [row.status === "cancelled" && "cancelled", row.purpose === "prewarm" && "prewarm"].filter(Boolean)) {
      waste[`${kind}_calls`] += row.calls; waste[`${kind}_tokens`] += tokens; waste[`${kind}_cost_list`] += listed || 0; waste[`${kind}_cost_what_if`] += alt;
    }
    if (row.feature !== "none") attemptsBy[row.feature] = (attemptsBy[row.feature] || 0) + row.attempts;
    altCost += alt; altPriced = altPriced || priceOf(LLM_COMPARE, row.day) !== null;
  }
  const attempts = Object.values(attemptsBy).reduce((x, y) => x + y, 0);
  const sorted = m => [...m.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  Object.keys(waste).forEach(k => { if (k.includes("cost")) waste[k] = r6(waste[k]); });
  return {
    range: rangeOf(p), currency: "USD", what_if_model: LLM_COMPARE, usd_vnd: 26300, attempts,
    totals: finish(totals), waste,
    by_day: sorted(groups.day).map(([day, acc]) => ({ day, ...finish(acc) })),
    by_feature: sorted(groups.feature).map(([feature, acc]) => {
      const out = finish(acc), n = attemptsBy[feature] || 0;
      return { feature, ...out, attempts: n, cost_list_per_attempt: n ? r6(out.cost_list / n) : null, cost_what_if_per_attempt: n ? r6(out.cost_what_if / n) : null };
    }),
    by_purpose: sorted(groups.purpose).map(([purpose, acc]) => ({ purpose, ...finish(acc) })),
    by_model: sorted(groups.model).map(([k, acc]) => { const [provider, model] = k.split("\u0000"); return { provider, model, ...finish(acc) }; }),
    what_if: [{ model: LLM_COMPARE, cost: r6(altCost), priced: altPriced, per_attempt: attempts && altPriced ? r6(altCost / attempts) : null }],
  };
}

function engagement(p) {
  const s = seriesFor(p);
  return {
    range: rangeOf(p),
    active_owners: uniqueOwners(s, p),   // chủ kính hoạt động trong KỲ đang chọn (hôm nay → DAU, tuần → WAU, tháng → MAU)
    new_users: Math.round((p.elapsed || 0) * 1.6), active_glasses: Math.min(D.DEVICES.length, uniqueOwners(s, p)), total_glasses: D.DEVICES.length,
    avg_active_days: Math.round((5.3 / 7) * (p.elapsed || 0) * 10) / 10, period_days: p.elapsed || 0,   // chỉ tính các ngày đã diễn ra
    granularity: s.hourly ? "hour" : "day",
    daily: s.buckets.map((date, i) => ({ date, active_owners: s.owners[i] })),
  };
}

function safety(p) {
  const sos = D.SAFETY.sos, live = D.SAFETY.live, K = p.K;
  const count = scale(sos.count, K), cancelled = Math.min(count, scale(sos.cancelled_within_5s, K));
  const success = Math.max(0, count - cancelled - scale(sos.failed, K));
  const counts = distribute(Math.round(live.alerts * K), live.shares);
  return {
    range: rangeOf(p),
    sos: { count, success, failed: count - cancelled - success, confirm_p50_seconds: sos.confirm_p50_seconds, cancelled_within_5s: cancelled },
    alert_live: {
      alert_mode: "controlled_trial", sessions: scale(live.sessions, K), total_on_seconds: scale(live.total_on_seconds, K), alerts_per_hour: live.alerts_per_hour,
      levels: live.shares.map((share_pct, level) => ({ level, count: counts[level], share_pct })),
    },
  };
}

// ---- Danh sách: lọc, sắp xếp, phân trang phía "server" ----
function listify(rows, query, { searchKeys = ["id"], filters = {}, sorts = [], defaultSort = null }) {
  let items = rows.slice();
  const q = (query.q || "").trim().toLowerCase();
  if (q) items = items.filter(r => searchKeys.some(k => String(r[k] ?? "").toLowerCase().includes(q)));
  for (const [param, field] of Object.entries(filters)) if (query[param] && query[param] !== "all") items = items.filter(r => String(r[field]) === query[param]);
  const chosen = sorts.includes(query.sort);
  const field = chosen ? query.sort : defaultSort?.field;
  const dir = chosen ? (query.order === "desc" ? -1 : 1) : (defaultSort?.order === "desc" ? -1 : 1);
  if (field) items.sort((a, b) => (typeof a[field] === "number" ? a[field] - b[field] : String(a[field]).localeCompare(String(b[field]), "vi", { numeric: true })) * dir);
  const page_size = clamp(parseInt(query.page_size, 10) || 10, 1, 100);
  const total = items.length;
  const page = clamp(parseInt(query.page, 10) || 1, 1, Math.max(1, Math.ceil(total / page_size)));
  return { items: items.slice((page - 1) * page_size, page * page_size), total, page, page_size };
}

function errorsList(p, query) {
  const rows = errorRows(p);
  const res = listify(rows, query, {
    searchKeys: ["code"], filters: { group: "group", firmware_version: "firmware_version" },
    sorts: ["code", "feature", "group", "count", "change", "last_seen"], defaultSort: { field: "count", order: "desc" },
  });
  return {
    range: { ...rangeOf(p), compare_from: p.compare_from, compare_to: p.compare_to }, ...res,
    available_groups: [...new Set(D.ERROR_ROWS.map(e => e.group))], available_firmware_versions: [...new Set(D.ERROR_ROWS.map(e => e.firmware_version))],
  };
}

export function rawRoute(method, path, query, body, token) { return route(method, path, query, body, token); }

// Trạng thái tạm của quản trị viên mẫu (mật khẩu, ảnh đại diện); lưu localStorage nếu có để giữ qua lần tải lại.
const STATE_KEY = "seevia-mock-admin";
const state = (() => {
  const base = { password: D.MOCK_CREDENTIALS.password, avatar_url: null };
  try { return { ...base, ...JSON.parse(globalThis.localStorage?.getItem(STATE_KEY) || "{}") }; } catch { return base; }
})();
const save = () => { try { globalThis.localStorage?.setItem(STATE_KEY, JSON.stringify(state)); } catch { /* bỏ qua */ } };
const meUser = () => ({ ...D.MOCK_ADMIN, avatar_url: state.avatar_url });

function route(method, path, query, body, token) {
  const clean = path.replace(/\/+$/, "") || "/";
  if (method === "POST" && clean === "/admin/auth/login") {
    const c = D.MOCK_CREDENTIALS;
    if (String(body?.email || "").trim().toLowerCase() === c.email && body?.password === state.password) {
      return ok({ token: c.token, expires_in: 3600, user: meUser() });
    }
    return fail(401, "invalid_credentials", "Email hoặc mật khẩu không đúng.");
  }
  if (!token || token !== D.MOCK_CREDENTIALS.token) return fail(401, "unauthenticated", "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.");
  if (method === "POST" && clean === "/admin/auth/logout") return ok({ ok: true });
  // Quản trị viên chỉ tự cập nhật hồ sơ của mình; dữ liệu hệ thống vẫn chỉ đọc. Lưu ý: lỗi sai mật khẩu hiện tại dùng 400, KHÔNG dùng 401 (401 làm đăng xuất).
  if (method === "POST" && clean === "/admin/auth/password") {
    const cur = String(body?.current_password ?? ""), next = String(body?.new_password ?? "");
    if (cur !== state.password) return fail(400, "invalid_current_password", "Mật khẩu hiện tại không đúng.");
    if (next.length < 8 || !/[A-Za-z]/.test(next) || !/\d/.test(next)) return fail(422, "weak_password", "Mật khẩu mới chưa đủ mạnh.");
    if (next === cur) return fail(422, "same_password", "Mật khẩu mới phải khác mật khẩu hiện tại.");
    state.password = next; save();
    return ok({ ok: true });
  }
  if (method === "PUT" && clean === "/admin/auth/avatar") {
    const url = String(body?.avatar_data_url ?? "");
    if (!/^data:image\/(jpeg|png|webp);base64,/.test(url)) return fail(422, "invalid_image", "Ảnh không hợp lệ.");
    if (url.length > 700000) return fail(413, "image_too_large", "Ảnh quá lớn.");
    state.avatar_url = url; save();
    return ok({ user: meUser() });
  }
  if (method === "DELETE" && clean === "/admin/auth/avatar") { state.avatar_url = null; save(); return ok({ user: meUser() }); }
  if (method !== "GET") return fail(405, "method_not_allowed", "Chỉ hỗ trợ đọc dữ liệu.");
  if (clean === "/admin/auth/me") return ok({ user: meUser() });

  const p = periodOf(query);
  const stats = clean.match(/^\/admin\/stats\/([a-z-]+)$/)?.[1];
  switch (stats) {
    case "overview": return ok(overview(p));
    case "see-read": case "find": case "connect": case "go": return ok(report(stats, p));
    case "community": return ok(community(p));
    case "time": return ok(time(p));
    case "quality": return ok(quality(p));
    case "engagement": return ok(engagement(p));
    case "safety": return ok(safety(p));
    case "llm": return ok(llm(p));
    case "errors": return ok(errorsList(p, query));
    default: break;
  }
  if (clean === "/admin/accounts") {
    return ok(listify(D.ACCOUNTS, query, { searchKeys: ["id"], filters: { status: "status" }, sorts: ["id", "created_at", "status", "glasses_count"] }));
  }
  const account = clean.match(/^\/admin\/accounts\/([^/]+)$/);
  if (account) {
    const row = D.ACCOUNTS.find(a => a.id === decodeURIComponent(account[1]));
    if (!row) return fail(404, "not_found", "Không tìm thấy tài khoản.");
    return ok({ ...row, glasses: D.DEVICES.filter(d => d.account_id === row.id).map(({ id, firmware_version, status, last_seen_at }) => ({ id, firmware_version, status, last_seen_at })) });
  }
  if (clean === "/admin/devices") {
    const res = listify(D.DEVICES, query, { searchKeys: ["id"], filters: { status: "status", firmware_version: "firmware_version" }, sorts: ["id", "firmware_version", "status", "last_seen_at", "account_id"] });
    return ok({ ...res, available_firmware_versions: [...new Set(D.DEVICES.map(d => d.firmware_version))] });
  }
  const device = clean.match(/^\/admin\/devices\/([^/]+)$/);
  if (device) {
    const row = D.DEVICES.find(d => d.id === decodeURIComponent(device[1]));
    if (!row) return fail(404, "not_found", "Không tìm thấy thiết bị.");
    return ok({ ...row, recent_errors: D.DEVICE_ERRORS[row.id] || [] });
  }
  if (clean === "/admin/config") return ok({ admin_accounts: D.ADMIN_ACCOUNTS });
  return fail(404, "not_found", "Không tìm thấy đường dẫn.");
}

export function handleMock({ method, path, query = {}, body, token }) {
  try { return route(method, path, query, body, token); } catch { return fail(500, "internal_error", "Lỗi giả lập."); }
}
