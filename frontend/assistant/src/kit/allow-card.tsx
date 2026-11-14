// 展示页：允许 / 拒绝卡（tool-uis/approval-bar.tsx）真组件预览，?page=allow&lang=zh[&theme=dark]。外框与问话照 steps.tsx 的 AskStep
// （.cc-steps-card > .cc-ask > .cc-ask-q）；数据是示例。只在开发服务器 / CC_KIT=1 构建里，不进生产。
import { ApprovalBar } from "@/components/tool-uis/approval-bar";

// 示例数据（不是产品文案：只在这张开发用的预览页上）
const SAMPLES = [
  { ask: "Delete these documents?", approval: { id: "a1", note: "ABC Trading Co., Ltd. · T.8 2026",
      preview: [{ field: "INV-2026-0117", before: "", after: "Flow Supplier Co., Ltd. · 10,700.00" },
        { field: "INV-2026-0121", before: "", after: "Siam Paper Co., Ltd. · 3,210.50" },
        { field: "INV-2026-0130", before: "", after: "Flow Supplier Co., Ltd. · 856.00" }] } },
  { ask: "Change the reminder time?", approval: { id: "a2", preview: [{ field: "Month-end entry", before: "08:00", after: "09:30" }] } },
  { ask: "Refresh the account sets?", approval: { id: "a3" } },
];

// 中文界面看中文示例（给用户看截图用），其余语言看英文示例
const SAMPLES_ZH = [
  { ask: "删除这些单据？", approval: { id: "a1", note: "ABC 贸易有限公司 · T.8 2026",
      preview: [{ field: "INV-2026-0117", before: "", after: "Flow Supplier Co., Ltd. · 10,700.00" },
        { field: "INV-2026-0121", before: "", after: "Siam Paper Co., Ltd. · 3,210.50" },
        { field: "INV-2026-0130", before: "", after: "Flow Supplier Co., Ltd. · 856.00" }] } },
  { ask: "修改提醒时间？", approval: { id: "a2", preview: [{ field: "月结", before: "08:00", after: "09:30" }] } },
  { ask: "重新读取账套？", approval: { id: "a3" } },
];

export function AllowCardPreview() {
  const samples = document.documentElement.lang === "zh" ? SAMPLES_ZH : SAMPLES;
  return (
    <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-16) p-(--cc-space-16)">
      {samples.map((sample) => (
        <div key={sample.approval.id} className="cc-steps-card">
          <div className="aui-step cc-ask" data-phase="ask">
            <p className="cc-ask-q">{sample.ask}</p>
            <ApprovalBar approval={sample.approval} respondToApproval={() => {}} />
          </div>
        </div>
      ))}
    </main>
  );
}
