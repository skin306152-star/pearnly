// 引导式录入展示页的示例数据（只给 kit.html 用）。题目、选项、原因、单据种类这些字在产品里由服务端按本轮语言出，
// 这里按四语各放一份示例；公司、对方、单号、金额是编的。
export type Lang = "th" | "zh" | "en" | "ja";

type Words = {
  question: string;
  cash: string; cashWhy: string; credit: string; creditWhy: string;
  kindCash: string; kindCredit: string; skipped: string; outside: string;
  restate: string; restateCollection: string; files: string;
};

const WORDS: Record<Lang, Words> = {
  zh: {
    question: "บริษัท สยามแม็คโคร จำกัด（5 张）：现购还是赊购？",
    cash: "现购", cashWhy: "票面有收据，已付", credit: "赊购", creditWhy: "记应付，以后再付",
    kindCash: "现购 HP", kindCredit: "赊购 RR", skipped: "跳过", outside: "日期在账套期间外",
    restate: "录进 SINCERE ICE 的账套 T.8：采购 11 张，1 张已录过不录。",
    restateCollection: "录进 นิว ลิงค์ 的账套 NLTTEST：收 ร้านข้าวสมชาย 2 张发票共 23,500.00，转账。", files: "12 个文件",
  },
  en: {
    question: "บริษัท สยามแม็คโคร จำกัด (5 documents): cash or credit purchase?",
    cash: "Cash purchase", cashWhy: "A receipt is on the document: paid", credit: "Credit purchase", creditWhy: "Posted to payables, paid later",
    kindCash: "Cash HP", kindCredit: "Credit RR", skipped: "Skipped", outside: "Date outside the account set period",
    restate: "Into SINCERE ICE, account set T.8: 11 purchases; 1 already entered is left out.",
    restateCollection: "Into นิว ลิงค์, account set NLTTEST: 23,500.00 from ร้านข้าวสมชาย on 2 invoices, by transfer.", files: "12 files",
  },
  th: {
    question: "บริษัท สยามแม็คโคร จำกัด (5 ใบ): ซื้อสดหรือซื้อเชื่อ?",
    cash: "ซื้อสด", cashWhy: "มีใบเสร็จในเอกสาร ชำระแล้ว", credit: "ซื้อเชื่อ", creditWhy: "ตั้งเจ้าหนี้ ชำระภายหลัง",
    kindCash: "ซื้อสด HP", kindCredit: "ซื้อเชื่อ RR", skipped: "ข้าม", outside: "วันที่อยู่นอกงวดของชุดบัญชี",
    restate: "บันทึกเข้า SINCERE ICE ชุดบัญชี T.8: ซื้อ 11 ใบ เคยบันทึกแล้ว 1 ใบไม่บันทึก",
    restateCollection: "บันทึกเข้า นิว ลิงค์ ชุดบัญชี NLTTEST: รับจาก ร้านข้าวสมชาย 2 ใบ รวม 23,500.00 โอน", files: "12 ไฟล์",
  },
  ja: {
    question: "บริษัท สยามแม็คโคร จำกัด（5 件）：現金仕入ですか、掛仕入ですか？",
    cash: "現金仕入", cashWhy: "領収書あり、支払済み", credit: "掛仕入", creditWhy: "買掛金に計上、後払い",
    kindCash: "現金仕入 HP", kindCredit: "掛仕入 RR", skipped: "スキップ", outside: "日付が会計期間外",
    restate: "SINCERE ICE の帳簿 T.8 に記帳：仕入 11 件、記帳済みの 1 件は除外。",
    restateCollection: "นิว ลิงค์ の帳簿 NLTTEST に記帳：ร้านข้าวสมชาย の請求書 2 件、計 23,500.00、振込。", files: "12 ファイル",
  },
};

export const words = (lang: string): Words => WORDS[(lang as Lang)] ?? WORDS.en;

export const COMPANY = "SINCERE ICE";
export const LEDGER = "T.8";
export const PERIOD = "2026-06";
export const COLLECTION_COMPANY = "บริษัท นิว ลิงค์ จำกัด";
export const COLLECTION_LEDGER = "NLTTEST";
export const COLLECTION_PERIOD = "2026-11";
export const CUSTOMER = "ร้านข้าวสมชาย（C0127）";
export const BANK = "กสิกรไทย";
export const SLIP = "slip-1112.jpg";

// 复核表的行：marked = 单号写法异常（标红不拦）；already = 已录过、用户在卡里选了跳过
export const TICKETS = [
  { id: "1", party: "สยามแม็คโคร", number: "IV6906-00751", date: "2026-06-03", total: "4,280.00", kind: "cash", on: true },
  { id: "2", party: "สยามแม็คโคร", number: "IV/69/06751", date: "2026-06-05", total: "1,926.00", kind: "cash", on: true, marked: true },
  { id: "3", party: "ไทยน้ำทิพย์", number: "TN-2606118", date: "2026-06-09", total: "12,840.00", kind: "credit", on: true },
  { id: "4", party: "การไฟฟ้าส่วนภูมิภาค", number: "0626-88120", date: "2026-06-12", total: "3,115.40", kind: "", on: false, already: true },
] as const;

export const RESULTS = [
  { id: "1", party: "สยามแม็คโคร", number: "IV6906-00751", ledger: "HP6906030001", total: "4,280.00", ok: true },
  { id: "3", party: "ไทยน้ำทิพย์", number: "TN-2606118", ledger: "RR6906090001", total: "12,840.00", ok: true },
  { id: "5", party: "บิ๊กซี ซูเปอร์เซ็นเตอร์", number: "BC-118204", ledger: "—", total: "856.00", ok: false },
] as const;

export const INVOICES = [
  { id: "a", number: "IV6910-0412", date: "2026-10-08", due: "18,500.00", receive: "18,500.00", on: true },
  { id: "b", number: "IV6910-0467", date: "2026-10-21", due: "9,200.00", receive: "5,000.00", on: true },
  { id: "c", number: "IV6911-0031", date: "2026-11-02", due: "7,740.00", receive: "", on: false },
] as const;
