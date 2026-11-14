// 展示页：引导卡的几种输入（tool-uis/guide-inputs.tsx）真组件预览，?page=guide-inputs&lang=zh。数据是示例；只在开发服务器 /
// CC_KIT=1 构建里，不进生产。
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { AmountsField, SplitField, ValueField } from "@/components/tool-uis/guide-inputs";
import type { AmountRow } from "@/lib/guide-inputs";

const INVOICES = [
  { code: "IV6900123", name: "IV6900123", note: "2026-10-03", amount: "10700.00", max: "10700.00" },
  { code: "IV6900131", name: "IV6900131", note: "2026-10-17", amount: "3210.50", max: "3210.50" },
  { code: "IV6900140", name: "IV6900140", note: "2026-11-02", amount: "856.00", max: "856.00" },
];
const METHODS = [{ code: "cash", name: "Cash" }, { code: "transfer", name: "KBANK 012-3-45678-9" }, { code: "discount", name: "Discount" }];

// 展示页自己的小标题与无障碍名（不是产品文案：只在这张开发用的预览页上）
const TITLES = { amounts: "amounts", split: "split", values: "month · number", month: "month", number: "number" };

export function GuideInputsPreview() {
  const [invoices, setInvoices] = useState<AmountRow[]>([{ code: "IV6900123", amount: "10700.00" }, { code: "IV6900131", amount: "4000" }]);
  const [split, setSplit] = useState<AmountRow[]>([{ code: "cash", amount: "700.00" }]);
  const [month, setMonth] = useState("2026-11");
  const [cost, setCost] = useState("12.5x");
  return (
    <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-16) p-(--cc-space-16)">
      <Card>
        <CardHeader><CardTitle>{TITLES.amounts}</CardTitle></CardHeader>
        <CardContent><AmountsField options={INVOICES} rows={invoices} onChange={setInvoices} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{TITLES.split}</CardTitle></CardHeader>
        <CardContent><SplitField options={METHODS} rows={split} total="13910.50" onChange={setSplit} /></CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>{TITLES.values}</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-(--cc-space-8)">
          <ValueField kind="month" label={TITLES.month} value={month} onChange={setMonth} />
          <ValueField kind="number" label={TITLES.number} value={cost} onChange={setCost} />
        </CardContent>
      </Card>
    </main>
  );
}
