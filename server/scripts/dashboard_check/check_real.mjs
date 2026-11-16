// Đối soát dashboard trên API thật — xem README.md cùng thư mục.
// Run the dashboard's own mappers on REAL AB responses (no browser needed) and re-check validate.js rules.
// usage: node check-real.mjs <dashboard_root> <api_base> <email> <password>
import path from "node:path";
import { pathToFileURL } from "node:url";
const [root, base, email, password] = process.argv.slice(2);
const imp = p => import(pathToFileURL(path.join(root, p)).href);
const { ROUTES } = await imp("js/registry.js");
const login = await (await fetch(`${base}/admin/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) })).json();
const H = { Authorization: `Bearer ${login.token}` };
const get = async (p, q = {}) => { const r = await fetch(`${base}${p}?${new URLSearchParams(q)}`, { headers: H }); return { status: r.status, body: await r.json() }; };
const BAD = /\[object|undefined|\bNaN\b/;
let failures = 0;
const fail = m => { failures++; console.error("✗", m); };
const scan = (label, v, seen = new Set()) => {
  if (typeof v === "function") return;
  if (typeof v === "string") { if (BAD.test(v)) fail(`${label}: "${v.slice(0, 80)}"`); return; }
  if (typeof v === "number" && Number.isNaN(v)) { fail(`${label}: NaN`); return; }
  if (v && typeof v === "object") { if (seen.has(v)) return; seen.add(v); for (const [k, x] of Object.entries(v)) scan(`${label}.${k}`, x, seen); }
};
const PERIODS = { week: { period: "week", from: "2026-11-09", to: "2026-11-15" }, today: { period: "today", from: "2026-11-12", to: "2026-11-12" },
                  month: { period: "month", from: "2026-11-01", to: "2026-11-30" } };
const accounts = (await get("/admin/accounts")).body.items, devices = (await get("/admin/devices")).body.items;
for (const [pname, period] of Object.entries(PERIODS)) {
  for (const [id, def] of Object.entries(ROUTES)) {
    const params = id === "account-detail" ? [accounts[0]?.id] : id === "device-detail" ? [devices[0]?.id] : [];
    const ctx = { id, def, params, query: new URLSearchParams(), period, yesterday: "2026-11-15" };
    const q = def.period ? { ...period } : {};
    const { status, body } = await get(def.endpoint(ctx), q);
    if (status !== 200) { fail(`[${pname}] ${id}: HTTP ${status} ${JSON.stringify(body).slice(0, 120)}`); continue; }
    try { scan(`[${pname}] ${id}`, def.map(body, ctx)); } catch (e) { fail(`[${pname}] ${id}: mapper threw ${e.message}`); }
  }
  // validate.js cross-checks, on the real API
  const s = a => a.reduce((x, y) => x + (y || 0), 0), eq = (l, a, b) => { if (a !== b) fail(`[${pname}] ${l}: ${a} ≠ ${b}`); };
  const ov = (await get("/admin/stats/overview", period)).body, tm = (await get("/admin/stats/time", period)).body,
        ql = (await get("/admin/stats/quality", period)).body, sf = (await get("/admin/stats/safety", period)).body;
  const total = ov.totals.uses;
  eq("series", s(ov.series.points.map(p => p.uses)), total); eq("groups+sos", s(ov.groups.map(g => g.uses)) + ov.sos_uses, total);
  eq("by_day", s(tm.by_day.map(d => d.uses)), total); eq("heatmap", s(tm.heatmap.cells.flat()), total);
  eq("by_hour", s(tm.by_hour.map(x => x.uses)), total); if (tm.by_week.length) eq("by_week", s(tm.by_week.map(x => x.uses)), total);
  eq("outcomes", s(ql.outcomes.map(o => o.uses)), total); eq("total_uses", ql.total_uses, total);
  eq("sos parts", sf.sos.count, sf.sos.success + sf.sos.failed + sf.sos.cancelled_within_5s); eq("sos=sos_uses", sf.sos.count, ov.sos_uses);
  const shares = sf.alert_live.levels.map(l => l.share_pct); if (shares.every(x => x !== null)) eq("levels=100", s(shares), 100);
  let pageErrors = 0;
  for (const [g, page] of Object.entries({ see_read: "see-read", find: "find", connect: "connect", go: "go" })) {
    const r = (await get(`/admin/stats/${page}`, period)).body, gu = ov.groups.find(x => x.key === g).uses;
    eq(`${page} summary`, r.summary.uses, gu); eq(`${page} features`, s(r.features.map(f => f.uses)), gu); pageErrors += s(r.features.map(f => f.errors));
  }
  eq("errors table", s((await get("/admin/stats/errors", { ...period, page_size: 100 })).body.items.map(e => e.count)), pageErrors);
  console.log(`[${pname}] uses=${total} groups=${JSON.stringify(ov.groups.map(g => g.uses))} sos=${ov.sos_uses}`);
}
eq0: {
  const c = (await get("/admin/stats/community", PERIODS.week)).body;
  if (c.total_accounts !== (await get("/admin/accounts", { page_size: 100 })).body.total) fail("total_accounts ≠ accounts list");
}
console.log(failures ? `${failures} lỗi` : "✓ Mọi route map được dữ liệu thật, không undefined/NaN; mọi ràng buộc nhất quán khớp");
process.exit(failures ? 1 : 0);
