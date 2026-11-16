// 引导卡替身（展示页与前端测试用，不进生产）：一个内存里的小引擎，按共享笔记「引导式-声明接口说明」「引擎通用能力 N1–N10 约定」
// 与 run.view() 的形状回卡。字段一个不多造：约定里还没定的（题目上的 zero / total、已定项的选项）标「待定」。
// 题目、选项、事实行这些字在正式页面里由服务端按本页语言翻好；这里按 <html lang> 取四语示例。
import type { GuideFiles, GuidePage, GuideQuestion, GuideTransport, GuideView } from "@/lib/guide-run";

const lang = (typeof document !== "undefined" ? document.documentElement.lang : "zh") as "zh" | "th" | "en" | "ja";
const L = (zh: string, th: string, en: string, ja: string) => ({ zh, th, en, ja })[lang] ?? en;

const T = {
  customer: L("选客户", "เลือกลูกค้า", "Customer", "得意先"),
  invoices: L("未收发票", "ใบแจ้งหนี้ค้างรับ", "Open invoices", "未入金請求書"),
  methods: L("收款方式", "วิธีรับชำระ", "Collection method", "入金方法"),
  voucher: L("转账凭证", "หลักฐานการโอน", "Transfer slip", "振込控え"),
  company: L("公司", "บริษัท", "Company", "会社"),
  day: L("收款日期", "วันที่รับชำระ", "Collection date", "入金日"),
  book: L("账套", "ชุดบัญชี", "Account set", "帳簿"),
  cash: L("现金", "เงินสด", "Cash", "現金"),
  discount: L("折扣", "ส่วนลด", "Discount", "値引"),
  reading: L("正在读取 Express 的未收发票", "กำลังอ่านใบแจ้งหนี้ค้างรับจาก Express", "Reading open invoices from Express", "Express の未入金請求書を読み取り中"),
  periods: L("可录期间 2026-01-01 至 2026-12-31", "งวดที่บันทึกได้ 2026-01-01 ถึง 2026-12-31", "Open period 2026-01-01 to 2026-12-31", "記帳可能期間 2026-01-01〜2026-12-31"),
  taken: L("IV6900131 刚被另一笔收款收了，请重选", "IV6900131 เพิ่งถูกรับชำระในรายการอื่น กรุณาเลือกใหม่", "IV6900131 was just collected elsewhere. Choose again.", "IV6900131 は別の入金で消し込まれました。選び直してください"),
  queued: L("已排队，等 CC 桌面版写进 Express", "เข้าคิวแล้ว รอ CC เดสก์ท็อปบันทึกลง Express", "Queued for CC Desktop to write to Express", "CC デスクトップが Express に書き込むのを待っています"),
  month: L("月份", "เดือน", "Month", "月"), date: L("日期", "วันที่", "Date", "日付"), time: L("时间", "เวลา", "Time", "時刻"),
  count: L("数量", "จำนวน", "Quantity", "数量"), note: L("备注", "หมายเหตุ", "Note", "備考"),
  items: L("盘点记数", "นับสต็อก", "Stock count", "棚卸数"),
  warehouses: L("仓库", "คลัง", "Warehouses", "倉庫"),
  pos: L("POS 认列", "การรับรู้ POS", "POS recognition", "POS 認識"),
  posKind: L("收入类型", "ประเภทรายได้", "Income type", "収入区分"),
  posDays: L("按天汇总", "สรุปรายวัน", "Daily summary", "日次集計"),
  sales: L("销售收入", "รายได้จากการขาย", "Sales income", "売上収入"),
  service: L("服务收入", "รายได้ค่าบริการ", "Service income", "役務収入"),
  yes: L("是", "ใช่", "Yes", "はい"), no: L("否", "ไม่", "No", "いいえ"),
};

const CUSTOMERS = [
  { code: "2100883", name: "2100883 ร้านป้าแดง", note: L("未收 3 张", "ค้างรับ 3 ใบ", "3 open", "未入金 3 件") },
  { code: "2100901", name: "2100901 บจก. สยามข้าวทอง", note: L("未收 1 张", "ค้างรับ 1 ใบ", "1 open", "未入金 1 件") },
  { code: "2101002", name: "2101002 ร้านเฮียเส็ง", note: L("未收 5 张", "ค้างรับ 5 ใบ", "5 open", "未入金 5 件") },
  ...Array.from({ length: 30 }, (_x, i) => ({ code: `21020${String(i).padStart(2, "0")}`, name: `21020${String(i).padStart(2, "0")} ร้านค้า ${i + 1}` })),
];
const INVOICES = [
  { code: "IV6900123", name: "IV6900123", note: "2026-10-03", amount: "10700.00", max: "10700.00" },
  { code: "IV6900131", name: "IV6900131", note: "2026-10-17", amount: "3210.50", max: "3210.50" },
  { code: "IV6900140", name: "IV6900140", note: "2026-11-02", amount: "856.00", max: "856.00" },
];
const METHODS = [{ code: "cash", name: T.cash }, { code: "transfer:KB", name: "KBANK 012-3-45678-9" }, { code: "discount", name: T.discount }];
const DECIDED = [
  { key: "company", input: "choice", title: T.company, value: "C001", value_label: "C001 บจก. ข้าวหอมมะลิ", source: "conversation" },
  { key: "day", input: "date", title: T.day, value: "2026-11-16", value_label: "2026-11-16", source: "today" },
  { key: "book", input: "choice", title: T.book, value: "69EXP", value_label: "69EXP · 2569", source: "conversation" },
];

const q = (key: string, input: GuideQuestion["input"], title: string, extra: Partial<GuideQuestion> = {}): GuideQuestion =>
  ({ key, input, rule: "always", tier: "how", title, required: true, ...extra });

type Run = { name: string; business: string; status: string; version: number; answers: Record<string, unknown>; stage: number; flow: string;
  waitLeft: number; returned?: Record<string, { reason: string; text?: string }>; result?: GuideView["result"]; consent?: "confirm" | "allow" };

const runs = new Map<string, Run>();

/** 收款整条（替身）：选客户 → 勾发票（第一次取「在读」，再取就有）→ 方式分摊 + 转账凭证 → 复核 → 确定 → 排队（再取一次）→ 已入账。 */
function collectionCard(run: Run): GuideView["card"] {
  const a = run.answers;
  const decided = DECIDED;
  if (!a.customer) {
    return { version: 2, ready: false, decided, questions: [q("customer", "choice", T.customer, { options: CUSTOMERS.slice(0, 20), more: true, search: true })] };
  }
  if (!a.invoices) {
    const waiting = run.waitLeft > 0;
    return { version: 2, ready: false, decided, answers: a, returned: run.returned,
      questions: [q("invoices", "amounts", T.invoices, { options: waiting ? [] : INVOICES, facts: waiting ? [T.reading] : undefined, wait: waiting || undefined })] };
  }
  const transfer = Array.isArray(a.methods) && (a.methods as { code: string }[]).some((row) => row.code.startsWith("transfer:"));
  // 答过的不再问（同引擎 steps.to_ask）；选了转账才问凭证（声明 when starts "transfer:"）
  const questions = a.methods ? (transfer ? [q("voucher", "file", T.voucher)] : []) : [q("methods", "split", T.methods, { options: METHODS })];
  return { version: 2, ready: false, decided, answers: a, questions };
}

function review(run: Run): GuideView["review"] {
  const a = run.answers;
  const rows = (a.invoices as { code: string; amount: string }[]) ?? [];
  const methods = (a.methods as { code: string; amount: string }[]) ?? [];
  const total = rows.reduce((sum, row) => sum + Number(row.amount), 0).toFixed(2);
  return {
    version: run.version, ready: true, total,
    lines: [
      { key: "company", label: T.company, value: "C001 บจก. ข้าวหอมมะลิ", step: "company" },
      { key: "book", label: T.book, value: "69EXP · 2569", step: "book" },
      { key: "customer", label: T.customer, value: CUSTOMERS.find((c) => c.code === a.customer)?.name ?? String(a.customer), step: "customer" },
      { key: "day", label: T.day, value: "2026-11-16", step: "day" },
    ],
    tables: [
      { key: "invoices", label: T.invoices, step: "invoices", rows: rows.map((row) => ({ code: row.code, name: row.code, amount: row.amount })) },
      { key: "methods", label: T.methods, step: "methods",
        rows: methods.map((row) => ({ code: row.code, name: METHODS.find((m) => m.code === row.code)?.name, amount: row.amount })) },
    ],
  };
}

/** 各种输入一览（替身）：月份 / 日期 / 时间 / 数字 / 文字 / 多选 / 子题 / 金额多选带搜索与 0。 */
function inputsCard(): GuideView["card"] {
  return {
    version: 2, ready: false, decided: [],
    questions: [
      q("month", "month", T.month, { facts: [T.periods] }),
      q("date", "date", T.date),
      q("time", "time", T.time),
      q("count", "number", T.count),
      q("note", "text", T.note, { required: false }),
      q("warehouses", "multi", T.warehouses, { options: [{ code: "01", name: "01 คลังหลัก" }, { code: "02", name: "02 คลังสาขา" }, { code: "03", name: "03 คลังสินค้าเสีย" }] }),
      q("pos", "questions", T.pos, { questions: [
        { key: "kind", input: "choice", title: T.posKind, required: true, options: [{ code: "sales", name: T.sales }, { code: "service", name: T.service }] },
        { key: "daily", input: "choice", title: T.posDays, required: true, options: [{ code: "yes", name: T.yes }, { code: "no", name: T.no }] },
        { key: "from", input: "time", title: T.time, required: false },
      ] }),
      q("items", "amounts", T.items, { search: true, more: true, zero: true, options: ITEMS.slice(0, 5) }),
    ],
  };
}
const ITEMS = Array.from({ length: 40 }, (_x, i) => ({ code: `RICE-${String(i + 1).padStart(3, "0")}`, name: `RICE-${String(i + 1).padStart(3, "0")} ข้าวหอมมะลิ ${5 * (i + 1)} กก.` }));

function view(run: Run): GuideView {
  const base = { run: run.name, business: run.business, status: run.status, version: run.version, company: "C001", ledger: "69EXP", period: "2026-11",
    consent: run.consent ?? "confirm" } as GuideView;
  if (run.status === "cancelled" || run.status === "expired") return { ...base, reason: run.status };
  if (run.status === "asking") {
    const card = run.flow === "inputs" ? inputsCard() : collectionCard(run);
    if (run.waitLeft > 0) run.waitLeft -= 1;
    return { ...base, card };
  }
  if (run.status === "reviewing") return { ...base, review: review(run) };
  if (run.status === "confirmed") {
    run.status = "done";
    return { ...base, status: "confirmed", result: { status: "queued", reason_text: T.queued, total: review(run)?.total, customer: String(run.answers.customer), wait: true } };
  }
  return { ...base, result: run.result ?? { status: "entered", docnum: "RE6911-0042", total: review(run)?.total, customer: String(run.answers.customer) } };
}

/** 一个场景：collection（整条）、collection-wait（发票在读）、collection-invoices、collection-returned（退回发票那一步）、collection-methods、
 *  review、queued、done、inputs、expired、cancelled。 */
export function seed(scenario: string): string {
  const name = `kit-${scenario}`;
  const run: Run = { name, business: "collection", status: "asking", version: 1, answers: {}, stage: 0, flow: "collection", waitLeft: 0 };
  if (scenario === "collection-wait") Object.assign(run, { answers: { customer: "2100883" }, waitLeft: 99 });
  if (scenario === "collection-invoices") Object.assign(run, { answers: { customer: "2100883" } });
  if (scenario === "collection-returned") Object.assign(run, { answers: { customer: "2100883" }, returned: { invoices: { reason: "over_collected", text: T.taken } } });
  if (scenario === "collection-methods") Object.assign(run, { answers: { customer: "2100883", invoices: [{ code: "IV6900123", amount: "10700.00" }] } });
  if (scenario === "review") Object.assign(run, { status: "reviewing", answers: { customer: "2100883", invoices: [{ code: "IV6900123", amount: "10700.00" }, { code: "IV6900140", amount: "500.00" }], methods: [{ code: "cash", amount: "700.00" }, { code: "transfer:KB", amount: "10500.00" }] } });
  if (scenario === "queued" || scenario === "done") Object.assign(run, { status: scenario === "queued" ? "confirmed" : "done",
    answers: { customer: "2100883", invoices: [{ code: "IV6900123", amount: "10700.00" }], methods: [{ code: "cash", amount: "10700.00" }] } });
  if (scenario === "inputs") Object.assign(run, { flow: "inputs", business: "stock_count" });
  if (scenario === "expired" || scenario === "cancelled") Object.assign(run, { status: scenario });
  runs.set(name, run);
  return name;
}

const delay = <T,>(value: T) => new Promise<T>((resolve) => setTimeout(() => resolve(value), 120));

export const fixtureTransport: GuideTransport = {
  start: (_s, business) => { const name = seed("collection"); runs.get(name)!.business = business; return delay(view(runs.get(name)!)); },
  view: (name) => delay(view(runs.get(name)!)),
  answer: (name, given, version) => {
    const run = runs.get(name)!;
    if (version !== undefined && version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    run.answers = { ...run.answers, ...given };
    run.returned = undefined;
    run.version += 1;
    const a = run.answers;
    const transfer = Array.isArray(a.methods) && (a.methods as { code: string }[]).some((row) => row.code.startsWith("transfer:"));
    if (run.flow === "collection" && a.customer && a.invoices && a.methods && (!transfer || a.voucher)) run.status = "reviewing";
    if (run.flow === "inputs") run.status = "reviewing";
    if (run.flow === "collection" && a.customer && !a.invoices && run.version === 2) run.waitLeft = 1;
    return delay(view(run));
  },
  confirm: (name, version) => {
    const run = runs.get(name)!;
    if (version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    run.status = "confirmed";
    return delay(view(run));
  },
  cancel: (name) => { const run = runs.get(name)!; run.status = "cancelled"; return delay(view(run)); },
  search: (name, key, txt, start): Promise<GuidePage> => {
    const source = key === "items" ? ITEMS : CUSTOMERS;
    const needle = txt.toLocaleLowerCase();
    const hit = source.filter((row) => !needle || row.name.toLocaleLowerCase().includes(needle));
    void name;
    return delay({ options: hit.slice(start, start + 20), more: hit.length > start + 20 });
  },
  files: (): GuideFiles => ({
    list: () => delay([{ id: "UPL-1", filename: "slip-kbank-1116.jpg" }, { id: "UPL-2", filename: "IV6900123.pdf" }]),
    upload: (file) => delay({ fileId: `UPL-${Date.now()}`, filename: file.name }),
  }),
};
