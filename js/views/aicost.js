/* Trang Chi phí AI: 4 thẻ (token, chi phí thật, giá niêm yết, quy đổi), token tốn vô ích, theo ngày, theo chức năng, mục đích, mô hình. */
import { h } from "../lib/dom.js";
import { vi } from "../i18n/vi.js";
import { barChart } from "./charts.js";
import { kpiGrid, metricGrid, section, dataTable, emptyState } from "./components.js";

export function viewAiCost(vm) {
  const a = vi.aiCost, c = a.cols;
  if (vm.empty) return h("div", { class: "stack-y" }, kpiGrid(vm.cards, 4), section({ title: a.dayTitle, id: "ai-day" }, emptyState()));

  const day = section({ title: a.dayTitle, tip: a.dayTip, id: "ai-day" },
    vm.hasBars ? barChart({ bars: vm.bars, ariaLabel: a.dayTitle, valueHeader: c.inTokens + " + " + c.outTokens.toLowerCase() }) : emptyState());

  const features = section({ title: a.featureTitle, tip: a.featureTip, id: "ai-feature" },
    dataTable({
      caption: a.featureTitle,
      columns: [{ key: "f", label: c.feature }, { key: "a", label: c.attempts, align: "end" }, { key: "n", label: c.calls, align: "end" },
        { key: "i", label: c.inTokens, align: "end" }, { key: "o", label: c.outTokens, align: "end" },
        { key: "w", label: c.whatIf, align: "end" }, { key: "p", label: c.whatIfPerAttempt, align: "end" }],
      rows: vm.featureRows,
    }));

  const purposes = section({ title: a.purposeTitle, id: "ai-purpose", cls: "fill" },
    dataTable({
      caption: a.purposeTitle,
      columns: [{ key: "p", label: c.purpose }, { key: "n", label: c.calls, align: "end" }, { key: "t", label: vi.aiCost.metrics.tokens.label, align: "end" }, { key: "w", label: c.whatIf, align: "end" }],
      rows: vm.purposeRows,
    }));

  const models = section({ title: a.modelTitle, tip: a.modelTip, id: "ai-model", cls: "fill" },
    dataTable({
      caption: a.modelTitle,
      columns: [{ key: "m", label: c.model }, { key: "n", label: c.calls, align: "end" }, { key: "t", label: vi.aiCost.metrics.tokens.label, align: "end" },
        { key: "l", label: c.latency, align: "end" }, { key: "c", label: c.costList, align: "end" }, { key: "e", label: c.estimated, align: "end" }],
      rows: vm.modelRows,
    }));

  return h("div", { class: "stack-y" },
    kpiGrid(vm.cards, 4),
    section({ title: a.wasteTitle, tip: a.wasteTip, id: "ai-waste" }, metricGrid(vm.waste, "cols-3")),
    day, features, h("div", { class: "grid-2" }, purposes, models),
    vm.estimatedNote ? h("p", { class: "footnote" }, vm.estimatedNote) : null,
    h("p", { class: "footnote" }, a.tokenizerNote));
}
