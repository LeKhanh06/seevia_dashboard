/* Trang Chi phí AI: GET /admin/stats/llm → thẻ chỉ số, phần tốn vô ích, theo ngày / chức năng / mục đích / mô hình. */
import { vi, fill } from "../i18n/vi.js";
import { fmtInt, fmtPct, fmtMs, fmtUsd, fmtVnd, fmtCompact, ratio, toNum, DASH } from "../lib/format.js";
import { arr, obj, metric, bucketLabel, bucketTitle } from "./util.js";

const tokensOf = g => (toNum(g.in_tokens) ?? 0) + (toNum(g.out_tokens) ?? 0);
const pctOf = (a, b) => { const r = ratio(a, b); return r === null ? DASH : fmtPct(Math.round(r * 1000) / 10); };
const withModel = (def, model) => ({ label: fill(def.label, { model }), tip: { how: fill(def.tip.how, { model }), use: fill(def.tip.use, { model }) } });

export const featureName = key => (key === "none" ? vi.aiCost.noneFeature : vi.aiCost.features[key] || vi.features[key] || key);
export const purposeName = key => vi.aiCost.purposes[key] || key;
export const providerName = key => vi.aiCost.providers[key] || key;

export function mapAiCost(raw) {
  const a = vi.aiCost, M = a.metrics, r = obj(raw), t = obj(r.totals), w = obj(r.waste);
  const model = r.what_if_model || a.whatIfFallback;
  const alt = arr(r.what_if).find(x => x.model === r.what_if_model) || arr(r.what_if)[0] || {};
  const rate = toNum(r.usd_vnd);
  const money = v => (rate === null || toNum(v) === null ? null : fill(a.vndFoot, { value: fmtVnd(toNum(v) * rate) }));
  const join = (...parts) => parts.filter(Boolean).join(" · ") || null;
  const calls = toNum(t.calls) ?? 0;

  const cached = toNum(t.cached_tokens);
  const tokensFoot = fill(a.tokensFoot, { in: fmtCompact(t.in_tokens), out: fmtCompact(t.out_tokens) }) + (cached ? fill(a.cachedFoot, { pct: pctOf(cached, t.in_tokens) }) : "");
  const perAttempt = toNum(alt.per_attempt) === null ? null : fill(a.perAttemptFoot, { value: fmtUsd(alt.per_attempt) });
  const cards = [
    metric(M.tokens, fmtCompact(tokensOf(t)), tokensFoot),
    metric(M.cost_actual, fmtUsd(t.cost_actual), join(a.freeFoot, money(t.cost_actual))),
    metric(M.cost_list, calls && toNum(t.unpriced_calls) === calls ? DASH : fmtUsd(t.cost_list), join(toNum(t.unpriced_calls) ? fill(a.unpricedFoot, { calls: fmtInt(t.unpriced_calls) }) : a.allPricedFoot, money(t.cost_list))),
    metric(withModel(M.what_if, model), alt.priced === false ? DASH : fmtUsd(toNum(t.cost_what_if) ?? alt.cost), join(perAttempt, money(t.cost_what_if ?? alt.cost))),
  ];

  const wasteBlock = (def, n, tokens, cost) => metric(def, fill(a.callsValue, { value: fmtInt(n) }),
    tokens === undefined ? (calls ? fill(a.shareFoot, { pct: pctOf(n, calls) }) : null) : fill(a.wasteFoot, { tokens: fmtCompact(tokens), cost: fmtUsd(cost) }));
  const waste = [
    wasteBlock(M.waste_cancelled, w.cancelled_calls, w.cancelled_tokens ?? 0, w.cancelled_cost_what_if),
    wasteBlock(M.waste_prewarm, w.prewarm_calls, w.prewarm_tokens ?? 0, w.prewarm_cost_what_if),
    wasteBlock(M.waste_error, w.error_calls),
  ];

  const days = arr(r.by_day);
  const bars = days.map(d => {
    const v = tokensOf(d), title = bucketTitle(d.day, "day", 1);
    return { label: bucketLabel(d.day, "day", days.length), title, value: v, empty: false, tip: fill(a.dayCell, { label: title, value: fmtInt(v) }) };
  });

  const features = [...arr(r.by_feature)].sort((x, y) => (toNum(y.cost_what_if) ?? 0) - (toNum(x.cost_what_if) ?? 0) || tokensOf(y) - tokensOf(x));
  const topFeature = Math.max(0, ...features.map(f => toNum(f.cost_what_if) ?? 0));
  const featureRows = features.map(f => [featureName(f.feature), f.feature === "none" ? DASH : fmtInt(f.attempts), fmtInt(f.calls), fmtInt(f.in_tokens), fmtInt(f.out_tokens),
    { text: fmtUsd(f.cost_what_if), bar: topFeature ? (toNum(f.cost_what_if) ?? 0) / topFeature : 0 }, fmtUsd(f.cost_what_if_per_attempt)]);

  const purposes = [...arr(r.by_purpose)].sort((x, y) => tokensOf(y) - tokensOf(x));
  const topPurpose = Math.max(0, ...purposes.map(tokensOf));
  const purposeRows = purposes.map(p => [purposeName(p.purpose), fmtInt(p.calls), { text: fmtCompact(tokensOf(p)), bar: topPurpose ? tokensOf(p) / topPurpose : 0 }, fmtUsd(p.cost_what_if)]);

  const models = [...arr(r.by_model)].sort((x, y) => tokensOf(y) - tokensOf(x));
  const modelRows = models.map(x => [`${providerName(x.provider)} · ${x.model}`, fmtInt(x.calls), fmtCompact(tokensOf(x)), fmtMs(x.avg_latency_ms),
    toNum(x.unpriced_calls) && toNum(x.unpriced_calls) === toNum(x.calls) ? DASH : fmtUsd(x.cost_list), pctOf(x.estimated_calls, x.calls)]);

  const estimated = toNum(t.estimated_calls) ?? 0;
  const csv = {
    headers: ["Nhóm", "Mục", "Lần gọi AI", "Token vào", "Token ra", "Token suy nghĩ", "Token cache", "Chi phí thật (USD)", "Giá niêm yết (USD)", `Quy đổi ${model} (USD)`, "Lượt sử dụng"],
    rows: [
      ["Tổng", "", t.calls ?? "", t.in_tokens ?? "", t.out_tokens ?? "", t.reasoning_tokens ?? "", t.cached_tokens ?? "", t.cost_actual ?? "", t.cost_list ?? "", t.cost_what_if ?? "", r.attempts ?? ""],
      ...days.map(d => ["Ngày", d.day, d.calls ?? "", d.in_tokens ?? "", d.out_tokens ?? "", d.reasoning_tokens ?? "", d.cached_tokens ?? "", d.cost_actual ?? "", d.cost_list ?? "", d.cost_what_if ?? "", ""]),
      ...features.map(f => ["Chức năng", featureName(f.feature), f.calls ?? "", f.in_tokens ?? "", f.out_tokens ?? "", f.reasoning_tokens ?? "", f.cached_tokens ?? "", f.cost_actual ?? "", f.cost_list ?? "", f.cost_what_if ?? "", f.attempts ?? ""]),
      ...purposes.map(p => ["Mục đích", purposeName(p.purpose), p.calls ?? "", p.in_tokens ?? "", p.out_tokens ?? "", p.reasoning_tokens ?? "", p.cached_tokens ?? "", p.cost_actual ?? "", p.cost_list ?? "", p.cost_what_if ?? "", ""]),
      ...models.map(x => ["Mô hình", `${x.provider} · ${x.model}`, x.calls ?? "", x.in_tokens ?? "", x.out_tokens ?? "", x.reasoning_tokens ?? "", x.cached_tokens ?? "", x.cost_actual ?? "", x.cost_list ?? "", x.cost_what_if ?? "", ""]),
    ],
  };

  return {
    cards, waste, bars, hasBars: bars.some(b => b.value > 0),
    featureRows, purposeRows, modelRows,
    estimatedNote: estimated ? fill(a.estimatedNote, { pct: pctOf(estimated, calls) }) : null,
    empty: calls === 0,
    csv,
  };
}
