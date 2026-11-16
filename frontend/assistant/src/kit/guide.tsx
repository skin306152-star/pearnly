// 展示页：引导卡（tool-uis/guide-card.tsx）真组件预览，?page=guide&lang=zh[&decided=open]。卡的内容是示例（题目、选项、来源的字取服务端
// 共用术语表 guide.* 的中文原文，正式页面里由服务端按本轮语言出）；只在开发服务器 / CC_KIT=1 构建里，不进生产。
import { GuideCardBody, type GuideCardData } from "@/components/tool-uis/guide-card";

const SAMPLE: GuideCardData = {
  questions: [
    { key: "pay", kind: "payment", tier: "how", title: "付款方式", title_params: { label: "Flow Supplier Co., Ltd.", count: 5 }, count: 5,
      preselected: "credit",
      options: [{ code: "credit", label: "赊账", recommended: true, note: "按票种判断" }, { code: "cash", label: "现金", note: "记为现购 / 现销" }] },
    { key: "detail", kind: "detail", tier: "whether", title: "明细", title_params: { label: "INV-2026-0117", count: 1 }, count: 1,
      preselected: "", options: [{ code: "amount_only", label: "只记金额", note: "只记表头与分录，不入库" }, { code: "skip", label: "不入账" }] },
  ],
  decided: [
    { key: "loc", kind: "location", title: "仓位", value: "01", value_label: "01 主仓", source_label: "记住的",
      options: [{ code: "01", label: "01 主仓" }, { code: "02", label: "02 分仓" }] },
    { key: "acc", kind: "account", title: "科目", value: "5300", value_label: "5300 运费", source_label: "Express 历史" },
  ],
};

export function GuidePreview({ decidedOpen, last }: { decidedOpen: boolean; last?: boolean }) {
  return (
    <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-16) p-(--cc-space-16)">
      <GuideCardBody card={SAMPLE} phase="ask" onSubmit={() => {}} decidedOpen={decidedOpen} startAt={last ? SAMPLE.questions.length - 1 : 0} />
    </main>
  );
}
