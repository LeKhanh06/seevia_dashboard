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
  const nowHour = nowVn.getUTCHours();
  const exactWeek = false;   // số liệu luôn được phân bổ theo quy mô VOLUME, không dùng mảng cố định của tuần thiết kế
  // Hôm nay: chỉ tính phần trăm số lượt đã diễn ra tới giờ hiện tại (theo hình dạng trong ngày).
  const shape = D.HOUR_SHAPE, dayShare = from === to && from === today ? shape.slice(0, nowHour + 1).reduce((x, y) => x + y, 0) / shape.reduce((x, y) => x + y, 0) : 1;
  return {
    period: query.period || "week", from, to, days, dates, exactWeek, elapsed, today, nowHour, K: ((from === to ? dayShare : elapsed) / 7) * D.VOLUME,
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
    const live = p.from === p.today ? D.HOUR_SHAPE.map((w, h) => (h <= p.nowHour ? w : 0)) : D.HOUR_SHAPE;   // giờ chưa tới: chưa có dữ liệu
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

function route(method, path, query, body, token) {
  const clean = path.replace(/\/+$/, "") || "/";
  if (method === "POST" && clean === "/admin/auth/login") {
    const c = D.MOCK_CREDENTIALS;
    if (String(body?.email || "").trim().toLowerCase() === c.email && body?.password === c.password) {
      return ok({ token: c.token, expires_in: 3600, user: D.MOCK_ADMIN });
    }
    return fail(401, "invalid_credentials", "Email hoặc mật khẩu không đúng.");
  }
  if (!token || token !== D.MOCK_CREDENTIALS.token) return fail(401, "unauthenticated", "Phiên đăng nhập không hợp lệ hoặc đã hết hạn.");
  if (method === "POST" && clean === "/admin/auth/logout") return ok({ ok: true });
  if (method !== "GET") return fail(405, "method_not_allowed", "Chỉ hỗ trợ đọc dữ liệu.");
  if (clean === "/admin/auth/me") return ok({ user: D.MOCK_ADMIN });

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
