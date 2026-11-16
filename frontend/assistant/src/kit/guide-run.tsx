// 展示页：按表单的引导卡（tool-uis/guide-run-card.tsx）真组件 + 替身引擎（guide-run-fixtures.ts），?page=guide-run&state=<场景>&lang=zh。
// 场景：collection（收款整条，可点着走到结果）、collection-wait、collection-invoices、collection-returned、collection-methods、review、
// inputs（各种输入）、expired、cancelled。只在开发服务器 / CC_KIT=1 构建里，不进生产。
import { useState } from "react";
import { GuideRunCard } from "@/components/tool-uis/guide-run-card";
import { setGuideTransport } from "@/lib/guide-run";
import { setGuideLogin } from "@/components/tool-uis/guide-operator";
import { fixtureLogin, fixtureTransport, seed } from "./guide-run-fixtures";

export function GuideRunPreview({ state }: { state: string }) {
  const [run] = useState(() => {
    setGuideTransport(fixtureTransport);
    setGuideLogin(fixtureLogin(state));
    return seed(state || "collection");
  });
  return (
    <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-16) p-(--cc-space-16)" data-slot="kit-guide-run">
      <GuideRunCard run={{ run, business: "collection", status: "asking", version: 1, startedAt: "" }} sessionId="kit" />
    </main>
  );
}
