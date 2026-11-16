// 展示页：按表单的引导卡（tool-uis/guide-run-card.tsx）真组件 + 替身引擎（guide-run-fixtures.ts），?page=guide-run&state=<场景>&lang=zh。
// 场景：collection（收款整条，可点着走到结果）、collection-wait、collection-invoices、collection-returned、collection-methods、review、
// inputs（各种输入）、expired、cancelled。只在开发服务器 / CC_KIT=1 构建里，不进生产。
import { useState } from "react";
import { GuideRunCard } from "@/components/tool-uis/guide-run-card";
import { useBusiness } from "@/lib/business";
import { setGuideTransport } from "@/lib/guide-run";
import { setGuideLogin } from "@/components/tool-uis/guide-operator";
import { fixtureLogin, fixtureTransport, seed } from "./guide-run-fixtures";

const KIT_LABELS: Record<string, Record<string, string>> = {
  zh: { recon: "对账", stocktake: "盘点", stock_count: "盘点记数", withholding_tax: "预扣税", scheduled_task_delete: "定时任务" },
  th: { recon: "กระทบยอด", stocktake: "นับสต็อก", stock_count: "บันทึกการนับ", withholding_tax: "ภาษีหัก ณ ที่จ่าย", scheduled_task_delete: "งานตามกำหนดเวลา" },
  en: { recon: "Reconciliation", stocktake: "Stocktake", stock_count: "Stock count", withholding_tax: "Withholding tax", scheduled_task_delete: "Scheduled tasks" },
  ja: { recon: "照合", stocktake: "棚卸", stock_count: "棚卸数", withholding_tax: "源泉税", scheduled_task_delete: "定期タスク" },
};

export function GuideRunPreview({ state }: { state: string }) {
  const [run] = useState(() => {
    setGuideTransport(fixtureTransport);
    setGuideLogin(fixtureLogin(state));
    // 菜单字正式页面由服务端按本页语言翻好（businesses 的 labels）；展示页给一份示例
    useBusiness.setState({ offered: { chat: [], operator: [], labels: KIT_LABELS[document.documentElement.lang] ?? KIT_LABELS.en } });
    return seed(state || "collection");
  });
  return (
    <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-16) p-(--cc-space-16)" data-slot="kit-guide-run">
      <GuideRunCard run={{ run, business: "collection", status: "asking", version: 1, startedAt: "" }} sessionId="kit" />
    </main>
  );
}
