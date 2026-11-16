// 引导卡替身（展示页与前端测试用，不进生产）：一个内存里的小引擎，按共享笔记「引导式-声明接口说明」「引擎通用能力 N1–N10 约定」
// 与 run.view() 的形状回卡。字段一个不多造：约定里还没定的（题目上的 zero / total、已定项的选项）标「待定」。
// 题目、选项、事实行这些字在正式页面里由服务端按本页语言翻好；这里按 <html lang> 取四语示例。
import type { DocFace, DocRow, GuideFiles, GuidePage, GuideQuestion, GuideReview, GuideTransport, GuideView } from "@/lib/guide-run";

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
  waitLeft: number; returned?: Record<string, { reason: string; text?: string }>; result?: GuideView["result"]; consent?: "confirm" | "allow";
  /** 按单据（采购）：交了的文件、识别还要等几次、题答了没有、票面 */
  docs?: { files: string[]; readLeft: number; answered: boolean; faces: Record<string, DocFace>; versions: Record<string, number> };
  /** 代录开始前（A4）：带 operator 块与事实行、多一道「再录一次」 */
  operator?: boolean; loginSet?: boolean };

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

function review(run: Run): GuideReview {
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


// ── 按单据（采购，documents 源）替身：交文件 → 识别中（再取一次就好）→ 第 1 期题卡 → 复核（改一格）→ 写入 → 每张一行 ──────────
const FACES: Record<string, DocFace> = {
  "D-1": { doc_no: "IV2026-0117", party_name: "บจก. โฟลว์ ซัพพลาย", party_tax: "0105551234567", party_branch: "00000", date: "2026-11-03",
    subtotal: "1000.00", vat: "70.00", total: "1070.00",
    lines: [{ description: "ข้าวสาร 5 กก.", qty: "10", unit_price: "100.00", amount: "1000.00" }] },
  "D-2": { doc_no: "", party_name: "ร้านเฮียเส็ง", party_tax: "123", party_branch: "", date: "2026-11-05", subtotal: "500.00", vat: "35.00", total: "540.00",
    lines: [{ description: "ถุงพลาสติก", qty: "5", unit_price: "100.00", amount: "500.00" }] },
};
const T2 = {
  pay: L("付款方式", "วิธีชำระเงิน", "Payment", "支払方法"),
  credit: L("赊账", "เงินเชื่อ", "Credit", "掛け"), cash: T.cash,
  byType: L("按票种判断", "ตามประเภทเอกสาร", "By document type", "書類種別で判断"),
  warehouse: L("仓库", "คลัง", "Warehouse", "倉庫"),
  posted: L("已排队，等 CC 桌面版写进 Express", "เข้าคิวแล้ว รอ CC เดสก์ท็อปบันทึกลง Express", "Queued for CC Desktop", "CC デスクトップ待ち"),
  noNumber: L("没有单号，这次不写", "ไม่มีเลขที่ ครั้งนี้ไม่บันทึก", "No number, not written this time", "番号なしのため今回は書き込みません"),
  computed: L("合计、税额、明细金额由系统计算", "ยอดรวม ภาษี และจำนวนเงินรายการคำนวณโดยระบบ", "Totals, VAT and line amounts are calculated", "合計・税額・明細金額は自動計算です"),
};
const COMPUTED = ["total", "vat", "lines.*.amount"];
const EDITABLE = ["doc_no", "party_name", "party_tax", "party_branch", "date", "subtotal", "lines.*.description", "lines.*.qty", "lines.*.unit_price"];

function marks(face: DocFace): Pick<DocRow, "marks" | "cells" | "blocking"> {
  const cells: Record<string, DocRow["marks"][number]> = {};
  const blocking: string[] = [];
  if (!face.doc_no) { cells.doc_no = "number"; blocking.push("doc_no"); }
  if (face.party_tax && face.party_tax.replace(/\D/g, "").length !== 13) cells.party_tax = "tax_id";
  if (Math.abs(Number(face.subtotal) + Number(face.vat) - Number(face.total)) > 0.01) cells.total = "amount";
  const order: DocRow["marks"] = ["number", "duplicate", "tax_id", "amount", "date"];
  return { marks: order.filter((m) => Object.values(cells).includes(m)), cells, blocking };
}

function documentsView(run: Run, base: GuideView): GuideView {
  const d = run.docs!;
  const out: GuideView = { ...base, source: "documents" as const,
    ...(run.operator ? { operator: { targets: [{ connection_title: "C001 บจก. ข้าวหอมมะลิ", account_set_title: "69EXP · 2569", computer: "ACC-PC01", count: 2 }] } } : {}) };
  if (run.status === "confirmed" || run.status === "done") {
    const done = run.status === "done";
    run.status = "done";
    return { ...out, status: done ? "done" : "confirmed", result: { status: "started", wait: !done, rows: [
      { doc_id: "D-1", doc_no: d.faces["D-1"].doc_no, status: done ? "entered" : "queued", ...(done ? { docnum: "PO6911-0007" } : {}) },
      { doc_id: "D-2", doc_no: d.faces["D-2"].doc_no || "D-2", status: "held", reason: "blocking", reason_text: T2.noNumber },
    ] } };
  }
  if (!d.files.length) {
    return { ...out, stage: "files", card: { version: 2, source: "documents", ready: false, decided: [],
      questions: [q("documents", "file", L("请上传采购票据。", "กรุณาอัปโหลดเอกสารซื้อ", "Please upload the purchase documents.", "仕入書類をアップロードしてください。"))] } };
  }
  const uploads = { files: d.files.map((file, i) => ({ batch: `B-${i + 1}`, state: d.readLeft > 0 ? "reading" : "done" })), wait: d.readLeft > 0 };
  if (d.readLeft > 0) {
    d.readLeft -= 1;
    return { ...out, stage: "reading", uploads, card: { version: 2, source: "documents", ready: false, decided: [], questions: [], wait: true } };
  }
  if (!d.answered) {
    // 第 1 期那张卡的形状（kind / options.label / recommended / preselected / count），不是表单题
    const card = { version: 2, source: "documents", ready: false,
      questions: [{ key: "pay|D-1", kind: "payment", tier: "how", title: T2.pay, count: 2, preselected: "credit",
        options: [{ code: "credit", label: T2.credit, recommended: true, note: T2.byType }, { code: "cash", label: T2.cash }] },
        // A3 单位题：选「填换算」还要填一个数，交「factor:6」
        { key: "unit|D-1|0", kind: "unit", tier: "how", title: L("单位换算", "การแปลงหน่วย", "Unit conversion", "単位換算"), count: 1,
          title_params: { label: "ข้าวสาร 5 กก." },
          options: [{ code: "same", label: L("与库存单位相同", "เหมือนหน่วยสต็อก", "Same as stock unit", "在庫単位と同じ") },
            { code: "factor", label: L("填换算", "ระบุอัตราแปลง", "Enter factor", "換算を入力"), entry: { input: "number", min_exclusive: "0" } }] }],
      decided: [{ key: "loc", kind: "location", title: T2.warehouse, value: "01", value_label: "01 คลังหลัก" }] };
    if (run.operator) {
      // 原开始卡上的「已写过」变成做不做的一题（不预选）；停下 / 等待的单是事实行
      card.questions.push({ key: "again|D-9", kind: "again", tier: "whether", title: L("IV2026-0099 已写进 Express", "IV2026-0099 บันทึกใน Express แล้ว", "IV2026-0099 is already in Express", "IV2026-0099 は Express に記帳済み"), count: 1,
        options: [{ code: "skip", label: L("跳过", "ข้าม", "Skip", "スキップ") }, { code: "again", label: L("再录一次", "บันทึกอีกครั้ง", "Record again", "もう一度記帳") }] } as never);
      Object.assign(card, { facts: { waiting: [L("IV2026-0101 · 等电脑读主档", "IV2026-0101 · รอคอมพิวเตอร์อ่านข้อมูลหลัก", "IV2026-0101 · waiting for the computer to read the master", "IV2026-0101 · パソコンのマスタ読込待ち")] } });
    }
    return { ...out, stage: "asking", uploads: run.operator ? undefined : uploads, card: card as unknown as GuideView["card"] };
  }
  const rows: DocRow[] = ["D-1", "D-2"].map((id) => ({ doc_id: id, version: d.versions[id] ?? 1, face: d.faces[id], editable: EDITABLE, ...marks(d.faces[id]) }));
  return { ...out, stage: "review", review: { rows, held: [], seen: `seen-${run.version}`, ready: rows.some((r) => !r.blocking.length) } };
}

function editFace(face: DocFace, field: string, value: string): DocFace {
  const line = /^lines\.(\d+)\.(\w+)$/.exec(field);
  if (!line) return { ...face, [field]: value };
  const lines = face.lines.map((row, i) => (i === Number(line[1]) ? { ...row, [line[2]]: value } : row));
  const subtotal = lines.reduce((sum, row) => sum + Number(row.qty) * Number(row.unit_price), 0);
  const fixed = lines.map((row) => ({ ...row, amount: (Number(row.qty) * Number(row.unit_price)).toFixed(2) }));
  return { ...face, lines: fixed, subtotal: subtotal.toFixed(2), vat: (subtotal * 0.07).toFixed(2), total: (subtotal * 1.07).toFixed(2) };
}

/** 预扣税待确认行（P3，表单 review 里逐行可改的表）。 */
const WHT_ROWS = [
  { code: "W-1", name: "บจก. ขนส่งไทย", note: "2026-10-05", amount: "30.00",
    cells: { include: "yes", income_type: "transport", payment_date: "2026-10-05", rate: "1", base: "3000.00", tax: "30.00", payee_tax_id: "0105551234567", form: "53" },
    issues: [] as string[] },
  { code: "W-2", name: "นายสมชาย ใจดี", note: "2026-10-12", amount: "150.00",
    cells: { include: "yes", income_type: "service", payment_date: "2026-10-12", rate: "3", base: "5000.00", tax: "150.00", payee_tax_id: "123", form: "3" },
    issues: [L("税号不是 13 位", "เลขประจำตัวผู้เสียภาษีไม่ครบ 13 หลัก", "Tax ID is not 13 digits", "納税者番号が 13 桁ではありません")] },
];
function whtReview(rows = WHT_ROWS): GuideReview {
  const choice = (options: [string, string][]) => ({ input: "choice" as const, options: options.map(([code, name]) => ({ code, name })) });
  const editable = { include: choice([["yes", L("算", "นับ", "Include", "含める")], ["no", L("不算", "ไม่นับ", "Exclude", "含めない")]]),
    income_type: choice([["transport", L("运输费", "ค่าขนส่ง", "Transport", "運送費")], ["service", L("服务费", "ค่าบริการ", "Service", "役務費")], ["rent", L("租金", "ค่าเช่า", "Rent", "賃料")]]),
    payment_date: { input: "date" as const }, rate: { input: "number" as const }, base: { input: "number" as const }, tax: { input: "number" as const },
    payee_tax_id: { input: "text" as const }, form: choice([["3", "ภ.ง.ด.3"], ["53", "ภ.ง.ด.53"]]) };
  return { version: 1, ready: true, total: "180.00", tables: [{ key: "wht", label: L("待确认的预扣税", "ภาษีหัก ณ ที่จ่ายที่รอยืนยัน", "Withholding tax to confirm", "確認待ちの源泉税"),
    columns: [["include", L("算入", "นับรวม", "Include", "計上")], ["income_type", L("收入类型", "ประเภทเงินได้", "Income type", "所得区分")],
      ["payment_date", L("付款日", "วันที่จ่าย", "Payment date", "支払日")], ["rate", L("税率", "อัตรา", "Rate", "税率")], ["base", L("计税额", "ฐานภาษี", "Base", "課税標準")],
      ["tax", L("税额", "ภาษี", "Tax", "税額")], ["payee_tax_id", L("收款方税号", "เลขผู้รับเงิน", "Payee tax ID", "受取人番号")], ["form", L("申报表", "แบบ", "Form", "様式")]]
      .map(([key, label]) => ({ key, label })),
    rows: rows.map((row) => ({ ...row, editable })) }] };
}

/** 允许卡式确定（N5）的替身：删一个定时任务。 */
function allowReview(): GuideReview {
  return { version: 1, ready: true, lines: [
    { key: "task", label: L("定时任务", "งานตามกำหนดเวลา", "Scheduled task", "定期タスク"), value: L("每月 1 日读回", "อ่านกลับทุกวันที่ 1", "Read back on the 1st", "毎月 1 日に読み戻し") },
    { key: "company", label: T.company, value: "C001 บจก. ข้าวหอมมะลิ" },
  ] };
}

function view(run: Run): GuideView {
  const base = { run: run.name, business: run.business, status: run.status, version: run.version, company: "C001", ledger: "69EXP", period: "2026-11",
    consent: run.consent ?? "confirm" } as GuideView;
  if (run.status === "cancelled" || run.status === "expired") return { ...base, reason: run.status };
  if (run.flow === "purchase") return documentsView(run, base);
  if (run.flow === "wht") return run.status === "reviewing" ? { ...base, review: whtReview() } : { ...base, result: { status: "entered" } };
  if (run.flow === "allow") {
    if (run.status === "reviewing") return { ...base, review: allowReview() };
    return { ...base, result: { status: "entered", reason_text: L("已删除", "ลบแล้ว", "Deleted", "削除しました") } };
  }
  if (run.flow === "follow") {
    return { ...base, status: "done", result: run.result ?? { status: "entered", total: "48,210.00",
      files: [{ file: "OUT-1", name: "recon-2026-10.xlsx" }, { file: "OUT-2", name: "unmatched-2026-10.pdf" }],
      questions: [{ key: "fee", input: "choice", title: L("银行手续费 35.00 记到", "ค่าธรรมเนียมธนาคาร 35.00 บันทึกเข้า", "Bank fee 35.00 goes to", "銀行手数料 35.00 の計上先"), required: true,
        options: [{ code: "5310", name: "5310 ค่าธรรมเนียมธนาคาร" }, { code: "5390", name: "5390 ค่าใช้จ่ายเบ็ดเตล็ด" }] }] } };
  }
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
 *  review、queued、done、inputs、expired、cancelled；purchase（按单据整条）、purchase-reading、purchase-asking、purchase-review、purchase-done；
 *  allow（允许卡式确定）、follow（结果带文件与追问）。 */
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
  if (scenario.startsWith("purchase")) {
    const step = scenario.slice("purchase".length);
    Object.assign(run, { flow: "purchase", business: "purchase", docs: { files: step ? ["UPL-1", "UPL-2"] : [], readLeft: step === "-reading" ? 99 : 0,
      answered: step === "-review" || step === "-done", faces: { ...FACES }, versions: {} } });
    if (step === "-done") run.status = "done";
  }
  if (scenario === "operator" || scenario === "operator-login") {
    Object.assign(run, { flow: "purchase", business: "purchase", operator: true, loginSet: scenario === "operator-login",
      docs: { files: ["I:1", "I:2"], readLeft: 0, answered: false, faces: { ...FACES }, versions: {} } });
  }
  if (scenario === "wht") Object.assign(run, { flow: "wht", business: "withholding_tax", status: "reviewing" });
  if (scenario === "allow") Object.assign(run, { flow: "allow", business: "scheduled_task_delete", status: "reviewing", consent: "allow" });
  if (scenario === "follow") Object.assign(run, { flow: "follow", business: "recon", status: "done" });
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
    run.version += 1;
    if (run.flow === "purchase") {
      const d = run.docs!;
      if (Array.isArray(given.documents)) Object.assign(d, { files: given.documents as string[], readLeft: 1 });
      else if (given.answers) d.answered = true;
      return delay(view(run));
    }
    run.answers = { ...run.answers, ...given };
    run.returned = undefined;
    const a = run.answers;
    const transfer = Array.isArray(a.methods) && (a.methods as { code: string }[]).some((row) => row.code.startsWith("transfer:"));
    if (run.flow === "collection" && a.customer && a.invoices && a.methods && (!transfer || a.voucher)) run.status = "reviewing";
    if (run.flow === "inputs") run.status = "reviewing";
    if (run.flow === "collection" && a.customer && !a.invoices && run.version === 2) run.waitLeft = 1;
    return delay(view(run));
  },
  confirm: (name, version, seen) => {
    const run = runs.get(name)!;
    if (version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    if (run.flow === "purchase" && seen !== `seen-${run.version}`) return delay({ ...view(run), outcome: "changed" as const });
    run.status = "confirmed";
    return delay(view(run));
  },
  cancel: (name) => { const run = runs.get(name)!; run.status = "cancelled"; return delay(view(run)); },
  edit: (name, docId, field, value, version) => {
    const run = runs.get(name)!;
    if (version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    if (run.flow === "wht") {
      // 表单表格的就地改（P3）：税号不是 13 位就拒，带译文
      if (field === "payee_tax_id" && value.replace(/\D/g, "").length !== 13) {
        return delay({ ...view(run), outcome: "refused" as const, refused: { payee_tax_id: "tax_id" }, refused_text: { payee_tax_id: L("税号要 13 位", "ต้องมี 13 หลัก", "Tax ID needs 13 digits", "13 桁が必要です") } });
      }
      const row = WHT_ROWS.find((r) => r.code === docId)!;
      row.cells = { ...row.cells, [field]: value };
      if (field === "payee_tax_id") row.issues = [];
      run.version += 1;
      return delay(view(run));
    }
    const column = field.replace(/^lines\.\d+\./, "lines.*.");
    if (COMPUTED.includes(column)) return delay({ ...view(run), outcome: "refused" as const, refused: { [field]: T2.computed } });
    const d = run.docs!;
    d.faces[docId] = editFace(d.faces[docId], field, value);
    d.versions[docId] = (d.versions[docId] ?? 1) + 1;
    run.version += 1;
    return delay(view(run));
  },
  reopen: (name, step, version) => {
    const run = runs.get(name)!;
    if (version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    // 回那一步：它与它之后各步的答案作废（替身照收款的先后：customer → invoices → methods → voucher）
    const order = ["customer", "invoices", "methods", "voucher"];
    const at = order.indexOf(step);
    run.answers = Object.fromEntries(Object.entries(run.answers).filter(([key]) => at < 0 || order.indexOf(key) < at));
    Object.assign(run, { status: "asking", version: run.version + 1 });
    return delay(view(run));
  },
  follow: (name, _answers, version) => {
    const run = runs.get(name)!;
    if (version !== run.version) return delay({ ...view(run), outcome: "changed" as const });
    run.version += 1;
    run.result = { status: "entered", total: "48,210.00", files: [{ file: "OUT-1", name: "recon-2026-10.xlsx" }] };
    return delay(view(run));
  },
  search: (name, key, txt, start): Promise<GuidePage> => {
    // 已定项改值也走这里（11-16 引擎定）：公司 / 账套各给一小页
    const decided: Record<string, { code: string; name: string }[]> = {
      company: [{ code: "C001", name: "C001 บจก. ข้าวหอมมะลิ" }, { code: "C002", name: "C002 ร้านข้าวทอง" }],
      book: [{ code: "69EXP", name: "69EXP · 2569" }, { code: "70EXP", name: "70EXP · 2570" }],
    };
    if (decided[key]) return delay({ options: decided[key], more: false });
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

/** 展示页问本机登录的替身：operator-login 场景当已设置，其余当没设置。 */
export const fixtureLogin = (scenario: string) => async () => (scenario === "operator-login" ? { set: true, user: "skin3" } : { set: false, user: "" });
