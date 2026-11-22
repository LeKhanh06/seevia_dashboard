/*
 * Kiểm tra chéo dữ liệu mẫu (chạy khi USE_MOCK = true, báo lỗi ra console nếu số liệu không khớp nhau):
 * tổng kỳ = tổng theo ngày = tổng heatmap = tổng nhóm + SOS = tổng trạng thái; SOS; mức cảnh báo = 100%; lỗi các trang = bảng lỗi; Chi phí AI: các nhóm = tổng.
 * Thuần JS, chạy được cả trong Node: node -e "import('./validate.js').then(m=>console.log(m.validateMock()))"
 */
import { rawRoute, GROUP_KEYS } from "./handler.js";
import { MOCK_CREDENTIALS, ACCOUNTS } from "./data.js";

const PERIODS = {
  week: { period: "week", from: "2026-11-09", to: "2026-11-15" },
  month: { period: "month", from: "2026-11-01", to: "2026-11-30" },
  today: { period: "today", from: "2026-11-15", to: "2026-11-15" },
};
const sum = a => a.reduce((x, y) => x + (y || 0), 0);
const PAGES = { see_read: "see-read", find: "find", connect: "connect", go: "go" };

export function validateMock() {
  const problems = [];
  const get = (path, q) => rawRoute("GET", path, q, undefined, MOCK_CREDENTIALS.token).body;
  const eq = (label, a, b) => { if (a !== b) problems.push(`${label}: ${a} ≠ ${b}`); };

  for (const [name, q] of Object.entries(PERIODS)) {
    const t = (s) => `[${name}] ${s}`;
    const ov = get("/admin/stats/overview", q), time = get("/admin/stats/time", q), ql = get("/admin/stats/quality", q), sf = get("/admin/stats/safety", q);
    const total = ov.totals.uses;
    eq(t("tổng theo ngày/giờ = tổng kỳ"), sum(ov.series.points.map(p => p.uses)), total);
    eq(t("các nhóm + SOS = tổng kỳ"), sum(ov.groups.map(g => g.uses)) + ov.sos_uses, total);
    eq(t("lượt theo ngày (Thời điểm) = tổng kỳ"), sum(time.by_day.map(d => d.uses)), total);
    eq(t("heatmap giờ × thứ = tổng kỳ"), sum(time.heatmap.cells.flat()), total);
    eq(t("lượt theo giờ = tổng kỳ"), sum(time.by_hour.map(x => x.uses)), total);
    if (time.by_week.length) eq(t("lượt theo tuần = tổng kỳ"), sum(time.by_week.map(x => x.uses)), total);
    eq(t("các trạng thái = tổng kỳ"), sum(ql.outcomes.map(o => o.uses)), total);
    eq(t("total_uses (Chất lượng) = tổng kỳ"), ql.total_uses, total);
    eq(t("SOS = thành công + thất bại + bị hủy"), sf.sos.count, sf.sos.success + sf.sos.failed + sf.sos.cancelled_within_5s);
    eq(t("SOS (An toàn) = SOS (Tổng quan)"), sf.sos.count, ov.sos_uses);
    eq(t("bốn mức cảnh báo = 100%"), sum(sf.alert_live.levels.map(l => l.share_pct)), 100);
    let errorsOnPages = 0;
    for (const k of GROUP_KEYS) {
      const r = get(`/admin/stats/${PAGES[k]}`, q);
      const group = ov.groups.find(g => g.key === k).uses;
      eq(t(`${PAGES[k]}: lượt trang = lượt nhóm ở Tổng quan`), r.summary.uses, group);
      eq(t(`${PAGES[k]}: tổng lượt chức năng = lượt nhóm`), sum(r.features.map(f => f.uses)), group);
      errorsOnPages += sum(r.features.map(f => f.errors));
    }
    const errs = get("/admin/stats/errors", { ...q, page_size: 100 });
    eq(t("tổng lỗi bảng Lỗi hệ thống = tổng cột Lỗi ở các trang nhóm"), sum(errs.items.map(e => e.count)), errorsOnPages);
    const llm = get("/admin/stats/llm", q), close = (x, y) => Math.abs(x - y) < 1e-4;
    for (const g of ["by_day", "by_feature", "by_purpose", "by_model"]) {
      eq(t(`Chi phí AI: lần gọi ${g} = tổng`), sum(llm[g].map(x => x.calls)), llm.totals.calls);
      eq(t(`Chi phí AI: token vào ${g} = tổng`), sum(llm[g].map(x => x.in_tokens)), llm.totals.in_tokens);
      if (!close(sum(llm[g].map(x => x.cost_what_if)), llm.totals.cost_what_if)) problems.push(t(`Chi phí AI: quy đổi ${g} ≠ tổng`));
    }
    eq(t("Chi phí AI: lượt sử dụng = tổng lượt các chức năng"), sum(llm.by_feature.map(f => f.attempts)), llm.attempts);
    if (!close(llm.what_if[0].cost, llm.totals.cost_what_if)) problems.push(t("Chi phí AI: what_if ≠ totals.cost_what_if"));
  }
  const week = PERIODS.week, community = get("/admin/stats/community", week);
  eq("tổng tài khoản (Cộng đồng) = số tài khoản trong danh sách", community.total_accounts, ACCOUNTS.length);
  return problems;
}

export function reportMockProblems() {
  const problems = validateMock();
  if (problems.length) console.error(`[Dữ liệu mẫu] ${problems.length} chỗ không khớp:\n - ${problems.join("\n - ")}`);
  return problems;
}
