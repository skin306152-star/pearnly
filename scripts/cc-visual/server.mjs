// CC 前端画面比对用的本机假服务端（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
// 只给无界面 Chrome 把 frontend/assistant 的构建产物整页渲染出来：接口全部返回写死的假数据，不连任何真实服务。
// 时间是固定的（NOW），页面里的 Date 也由 shoot.mjs 固定到同一刻，「5 分钟前」这类相对时间两次运行一字不差。
//
// 两种启动方式（同 2.6 的加载时序问题）：
//   /assistant?...               直接开产物 index.html（标签写死在页面里）
//   /assistant?...&__boot=page   照生产页 apps/pearnly_core/pearnly_core/www/assistant.html：页面脚本同步取产物 index.html，
//                                再逐个 createElement 插入 <link> 与 module <script>（脚本不等样式表生效）
// 另：&__lang=th|zh|en 决定 <html lang>（界面主语言是泰文）。
//
// 单独起：node scripts/cc-visual/server.mjs --dist <产物目录> [--port 18950]；shoot.mjs 会自己在进程里起。

import http from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = fileURLToPath(new URL(".", import.meta.url));
const ROOT = resolve(HERE, "../..");
const PAGE_TEMPLATE = join(ROOT, "apps/pearnly_core/pearnly_core/www/assistant.html");
const BASE = "/assets/pearnly_core/assistant/";

/** 固定的「现在」：服务端时间戳与页面 Date 都从这里算。 */
export const NOW = Date.parse("2026-10-15T03:00:00.000Z");
const iso = (minAgo) => new Date(NOW - minAgo * 60000).toISOString();
const t0 = NOW - 10 * 60000;
const at = (s) => new Date(t0 + s * 1000).toISOString();

const COMPANIES = [
  { id: "C-0001", title: "สยามเทรดดิ้ง จำกัด", subtitle: "0105551000001", count: 4, last_active: iso(5) },
  { id: "C-0002", title: "曼谷物流有限公司", subtitle: "0105551000002", count: 2, last_active: iso(60) },
  { id: "C-0003", title: "Green Leaf Co., Ltd.", subtitle: "0105551000003", count: 1, last_active: iso(600) },
];

const session = (id, title, project, extra = {}) => ({
  id, title, project, period: "", scope: "", starred: false, unread: false, status: "idle",
  created_at: iso(300), updated_at: iso(5), archived_at: null, ...extra,
});

const SESSIONS = [
  session("sesn_a1", "核对九月进项发票", "C-0001", { period: "2026-09", starred: true }),
  session("sesn_a2", "把八月的销售单推到 Express", "C-0001", { period: "2026-08", unread: true, status: "running", updated_at: iso(20) }),
  session("sesn_a3", "预扣税凭证整理", "C-0001", { updated_at: iso(80) }),
  session("sesn_rich", "สรุปการกระทบยอดเดือนกันยายน", "C-0001", { period: "2026-09", updated_at: iso(90) }),
  session("sesn_b1", "银行对账 九月", "C-0002", { period: "2026-09", updated_at: iso(60) }),
  session("sesn_b2", "供应商主档补全", "C-0002", { updated_at: iso(160) }),
  session("sesn_c1", "Green Leaf 年度结账准备", "C-0003", { updated_at: iso(600) }),
  session("sesn_r1", "怎么设置定时任务", "", { updated_at: iso(30) }),
  session("sesn_err", "连接出错的对话", "", { updated_at: iso(40) }),
  session("sesn_r2", "一个非常非常长的对话标题用来看侧栏末尾渐隐是否正常显示", "", { updated_at: iso(300) }),
];

const PENDING = [
  { type: "user.message", id: "sevt_u1", processed_at: at(0), content: [{ type: "text", text: "帮我核对九月的进项发票，把能推的推到 Express。" }] },
  { type: "agent.message", id: "sevt_m1", processed_at: at(5), content: [{ type: "text", text: "好的。我先看一下九月收到的单据：\n\n- 共 **42** 张，已识别 40 张\n- 2 张缺日期\n\n下面要运行一条命令读取本月清单。" }] },
  { type: "agent.tool_use", id: "sevt_t0", processed_at: at(6), name: "bash", input: { command: "ls /mnt/session/uploads" } },
  { type: "agent.tool_result", id: "sevt_r0", processed_at: at(7), tool_use_id: "sevt_t0", content: [{ type: "text", text: "a.pdf\nb.pdf" }] },
  { type: "agent.message", id: "sevt_m2", processed_at: at(8), content: [{ type: "text", text: "读到了两份文件。接下来要改一个文件，需要你允许。" }] },
  { type: "agent.tool_use", id: "sevt_t1", processed_at: at(9), name: "write", input: { file_path: "/mnt/session/outputs/summary.md", content: "# 九月进项\n共 42 张" } },
  { type: "session.status_idle", id: "sevt_s1", processed_at: at(10), stop_reason: { type: "requires_action", event_ids: ["sevt_t1"] } },
];

const DONE = [
  { type: "user.message", id: "sevt_u9", processed_at: at(0), content: [{ type: "text", text: "预扣税凭证要怎么整理？" }] },
  { type: "session.status_running", id: "sevt_s8", processed_at: at(1) },
  { type: "agent.message", id: "sevt_m9", processed_at: at(4), content: [{ type: "text", text: "按月份把凭证放进同一个文件夹，我会逐张读出金额与税号。" }] },
  { type: "session.status_idle", id: "sevt_s9", processed_at: at(5), stop_reason: { type: "end_turn" } },
];

const MARKDOWN = [
  "## สรุปการกระทบยอด",
  "",
  "เดือนกันยายนตรวจแล้ว **42** รายการ มี `2` รายการที่ยังไม่ตรง：",
  "",
  "1. ใบกำกับภาษี IV-001 ไม่มีวันที่",
  "2. ใบกำกับภาษี IV-002 ต้องเลือกบัญชี",
  "",
  "| เลขที่ | คู่ค้า | ยอดเงิน | สถานะ |",
  "| :- | :- | -: | :- |",
  "| IV-001 | PTT | 1,070.00 | ขาดวันที่ |",
  "| IV-002 | MEA | 2,140.00 | เลือกบัญชี |",
  "| IV-003 | 7-Eleven | 535.00 | ตรงกัน |",
  "",
  "> 对不上的两张已经放进待处理。",
  "",
  "```python",
  "total = sum(row.amount for row in rows)",
  "print(f\"{total:,.2f}\")",
  "```",
  "",
  "详见 [对账说明](https://example.invalid/recon)。",
].join("\n");

// 前面几轮问答：让对话比一屏长，滚到底按钮才有机会出现
const EARLIER = Array.from({ length: 6 }, (_, i) => [
  { type: "user.message", id: `sevt_pu${i}`, processed_at: new Date(t0 - (60 - i * 5) * 60000).toISOString(), content: [{ type: "text", text: `第 ${i + 1} 个问题：这家公司${i + 1}月的进项有多少张？` }] },
  { type: "agent.message", id: `sevt_pm${i}`, processed_at: new Date(t0 - (59 - i * 5) * 60000).toISOString(), content: [{ type: "text", text: `${i + 1} 月共收到 ${30 + i} 张进项发票，已全部识别；其中 ${i} 张缺税号，已经放进待处理，等你确认后再推送到 Express。` }] },
]).flat();

const RICH = [
  ...EARLIER,
  { type: "user.message", id: "sevt_ru1", processed_at: at(0), content: [{ type: "text", text: "把九月的对账结果整理成表格" }] },
  { type: "session.status_running", id: "sevt_rs1", processed_at: at(1) },
  { type: "agent.tool_use", id: "sevt_rt1", processed_at: at(2), name: "bash", input: { command: "python3 recon.py --month 2026-09" } },
  { type: "agent.tool_result", id: "sevt_rr1", processed_at: at(3), tool_use_id: "sevt_rt1", content: [{ type: "text", text: "42 rows\n2 mismatched" }] },
  { type: "agent.message", id: "sevt_rm1", processed_at: at(4), content: [{ type: "text", text: MARKDOWN }] },
  { type: "pearnly.notice", id: "pnotice_1", processed_at: at(5), kind: "task", title: "每天读文件夹", text: "九月新到 3 份单据，已识别。", session: "sesn_rich" },
  { type: "session.status_idle", id: "sevt_rs2", processed_at: at(6), stop_reason: { type: "end_turn" } },
];

const ERRORED = [
  { type: "user.message", id: "sevt_eu1", processed_at: at(0), content: [{ type: "text", text: "读一下这个月的银行流水" }] },
  { type: "session.status_running", id: "sevt_es1", processed_at: at(1) },
  { type: "session.error", id: "sevt_ee1", processed_at: at(2), error: { message: "The session hit an error.", retry_status: { type: "terminal" } } },
  { type: "session.status_idle", id: "sevt_es2", processed_at: at(3), stop_reason: { type: "end_turn" } },
];

const RUNNING = [
  { type: "user.message", id: "sevt_gu1", processed_at: at(0), content: [{ type: "text", text: "把八月的销售单推到 Express" }] },
  { type: "session.status_running", id: "sevt_gs1", processed_at: at(1) },
];

const IDLE_RUN = (asOf) => ({ status: "idle", parked: [], awaiting_approval: [], executing: [], as_of: asOf });
const EVENTS = {
  sesn_a1: { events: PENDING, full: true, run: { status: "requires_action", parked: ["sevt_t1"], awaiting_approval: [], executing: [], as_of: at(10) } },
  sesn_a2: { events: RUNNING, full: true, run: { status: "running", parked: [], awaiting_approval: [], executing: [], as_of: at(1) } },
  sesn_a3: { events: DONE, full: true, run: IDLE_RUN(at(5)) },
  sesn_rich: { events: RICH, full: true, run: IDLE_RUN(at(6)) },
  sesn_err: { events: ERRORED, full: true, run: IDLE_RUN(at(3)) },
};

const FILES = {
  sesn_rich: [
    { id: "file_x", filename: "กระทบยอด-2026-09.xlsx", size_bytes: 12345, mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", downloadable: true, created_at: iso(3), kind: "output" },
    { id: "file_d", filename: "九月对账说明.docx", size_bytes: 23456, mime_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", downloadable: true, created_at: iso(3), kind: "output" },
    { id: "file_h", filename: "summary.html", size_bytes: 3456, mime_type: "text/html", downloadable: true, created_at: iso(3), kind: "output" },
    { id: "file_p", filename: "a.pdf", size_bytes: 54321, mime_type: "application/pdf", downloadable: true, created_at: iso(9), kind: "upload", pages: 2 },
  ],
  sesn_a1: [
    { id: "file_1", filename: "九月进项清单.xlsx", size_bytes: 12345, mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", downloadable: true, created_at: iso(3), kind: "output" },
  ],
};

const PREVIEWS = {
  file_x: { type: "sheets", sheets: [
    { name: "สรุป", rows: [["เลขที่", "คู่ค้า", "ยอดเงิน", "สถานะ"], ["IV-001", "PTT", "1,070.00", "ขาดวันที่"], ["IV-002", "MEA", "2,140.00", "เลือกบัญชี"], ["IV-003", "7-Eleven", "535.00", "ตรงกัน"]] },
    { name: "明细", rows: [["日期", "摘要", "金额"], ["2026-09-01", "电费", "1,070.00"]] },
  ] },
  file_d: { type: "document", blocks: [
    { type: "paragraph", style: "h1", runs: [{ text: "九月对账说明" }] },
    { type: "paragraph", runs: [{ text: "本月共核对 42 张单据，2 张待处理。" }] },
    { type: "paragraph", list: { level: 0, marker: "•" }, runs: [{ text: "IV-001 缺日期" }] },
    { type: "paragraph", list: { level: 1, marker: "◦" }, runs: [{ text: "已通知供应商补开" }] },
    { type: "table", rows: [
      { header: true, cells: [{ blocks: [{ type: "paragraph", runs: [{ text: "单号" }] }] }, { blocks: [{ type: "paragraph", runs: [{ text: "金额" }] }] }] },
      { cells: [{ blocks: [{ type: "paragraph", runs: [{ text: "IV-001" }] }] }, { blocks: [{ type: "paragraph", runs: [{ text: "1,070.00" }] }] }] },
    ] },
  ] },
  file_h: { type: "none" },
  file_p: { type: "pdf", pages: 2 },
};

const HTML_FILE = "<!doctype html><meta charset=utf-8><title>summary</title><h1>九月对账</h1><p>共 42 张。</p>";
// 8×8 浅灰 PNG（PDF 页图占位）
const PAGE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAEklEQVR4nGP4//8/AxbAOCQkALJNP9EcQ9YbAAAAAElFTkSuQmCC", "base64");

const DETAIL = (company) => {
  const c = COMPANIES.find((x) => x.id === company) ?? COMPANIES[0];
  return {
    company: { id: c.id, title: c.title, legal_name: c.title, tax_id: c.subtitle, branch: "00000", adapter: "express", created_by: "test@example.com", created_at: iso(10000) },
    account_sets: [{ connection: "CONN-1", adapter: "express", key: "SET1", label: "2569", year_label: "2569", writable: true, branch: "00000" }],
    branches: [{ branch: "00000", label: "สำนักงานใหญ่", account_sets: [{ connection: "CONN-1", adapter: "express", key: "SET1", label: "2569", year_label: "2569", writable: true }] }],
    period_set: { key: "SET1", label: "2569" },
    folders: [{ reader: "R1", device: "OFFICE-PC", label: "D:\\Docs\\Siam", state: "ok", month_folder: "09", mine: true }],
    notes: [{ id: "N1", text: "九月起改用新的银行账户", origin: "person", updated_by: "test", updated_at: iso(2000) }],
    choices: [
      { key: "K1", source: "rule", kind: "account", subject_label: "电费", value: "5300", value_label: "5300 水电费", editable: true, set_by: "test", set_at: iso(3000) },
      { key: "K2", source: "history", kind: "account", subject_label: "PTT", value: "5310", value_label: "5310 油费", count: 4, promotable: true },
    ],
    tasks: [{ name: "T1", title: "每天读文件夹", enabled: 1, account_label: "2569", folder_label: "D:\\Docs" }],
    drafts: [],
  };
};

const MONTH = (company, period) => ({
  company, period, ended: false, generated_at: iso(1),
  stages: {
    intake: { received: 42, recognized_only: 2, no_date: 2, tasks: { count: 1, enabled: 1, attention: 0 }, files: { failed: 1, not_invoice: 0, pos_layout_needed: 0 }, state: "attention" },
    entry: { pushed: 30, in_flight: 2, waiting: 3, not_pushed: 7, state: "open" },
    verify: { sets: [{ connection: "CONN-1", key: "SET1", label: "2569", status: "ready", readback: "RB1", ready_at: iso(30), counts: { ok: 30 } }], state: "clear" },
    tax: { vat: [{ draft: "D1", branch: "00000", updated_at: iso(50), tax_total: "1234.50", issues: 0 }], wht: [], state: "open" },
  },
  exceptions: {
    items: [
      { code: "no_date", stage: "intake", severity: "warn", action: "open", group: null, document: "DOC-1", invoice_no: "IV-001", counterparty: "PTT", amount: "1070.00", date: "", filename: "a.pdf", reason_label: "缺日期" },
      { code: "needs_choice", stage: "entry", severity: "warn", action: "choose", group: null, document: "DOC-2", invoice_no: "IV-002", counterparty: "MEA", amount: "2140.00", date: "2026-09-12", filename: "b.pdf", reason_label: "要选科目", choice: { connection: "CONN-1", account_set_key: "SET1", kinds: ["account"] } },
    ],
    total: 2, counts: { intake: 1, entry: 1 }, severities: { warn: 2 },
  },
});

const OVERVIEW = { companies: { "C-0001": { count: 1 } }, tasks: { T1: "C-0001" }, titles: {} };

function method(name, q, unmocked) {
  const A = "pearnly_core.assistant.api.";
  if (name === "pearnly_core.shell.describe") return { product: "cowork", product_label: "Cowork", full_name: "ทดสอบ ผู้ใช้", user: "test@example.com" };
  if (name.startsWith(A)) {
    const m = name.slice(A.length);
    switch (m) {
      case "projects": return { kind: "company", period: true, scope: false, projects: COMPANIES };
      case "listing": return { sessions: SESSIONS, nextCursor: null, groups: {}, ordered: [], runs: {} };
      case "get": return SESSIONS.find((s) => s.id === q.get("session_id")) ?? SESSIONS[0];
      case "events": return EVENTS[q.get("session_id")] ?? { events: [], full: true, run: IDLE_RUN(null) };
      case "layout": return { prefs: null };
      case "tool_steps": return { done: { bash: "已运行命令", write: "已写文件" }, running: {}, ask: {}, ask_when: {}, gate: {} };
      case "session_files": return FILES[q.get("session_id")] ?? [];
      case "preview": return PREVIEWS[q.get("file_id")] ?? { type: "none" };
      case "push_key": return { key: "" };
      case "save_layout": case "notices_seen": case "confirm": case "send": case "interrupt": return {};
      default: break;
    }
  }
  const C = "pearnly_cowork.companies.api.";
  if (name.startsWith(C)) {
    const m = name.slice(C.length);
    switch (m) {
      case "companies": return COMPANIES.map((c) => ({ id: c.id, title: c.title, legal_name: c.title, tax_id: c.subtitle, branch: "00000", adapter: "express", years: ["2569"], mine_count: c.count, mine_last_active: c.last_active }));
      case "detail": return DETAIL(q.get("company"));
      case "pending": return { items: MONTH("C-0001", "2026-09").exceptions.items.map((x) => ({ ...x, company: "C-0001", reason_code: x.code, month: "2026-09" })) };
      case "pending_counts": return { total: 2, companies: { "C-0001": 2 } };
      case "task_overview": return OVERVIEW;
      case "month": return MONTH(q.get("company"), q.get("period"));
      default: break;
    }
  }
  const F = "pearnly_cowork.folder_intake.api.";
  if (name.startsWith(F)) {
    const m = name.slice(F.length);
    switch (m) {
      case "listing": return { rules: [] };
      case "terms": return {};
      case "computers": return [];
      default: break;
    }
  }
  if (name === "pearnly_cowork.connections.api.records") return [];
  unmocked.add(name);
  return null;
}

const TYPES = {
  ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
  ".html": "text/html; charset=utf-8", ".woff2": "font/woff2", ".woff": "font/woff", ".webmanifest": "application/manifest+json",
  ".json": "application/json",
};

/** 生产页模板（Jinja）照渲染：去掉 {# … #} 注释，填 lang 与 csrf_token。 */
function productionPage(lang) {
  const template = readFileSync(PAGE_TEMPLATE, "utf8");
  return template
    .replace(/\{#[\s\S]*?#\}/g, "")
    .replace(/\{\{\s*lang\s*\}\}/g, lang)
    .replace(/\{\{\s*csrf_token\s*\}\}/g, "ccvisual");
}

function distPage(dist, lang) {
  return readFileSync(join(dist, "index.html"), "utf8")
    .replace(/<html lang="[^"]*">/, `<html lang="${lang}">`)
    .replace("<head>", '<head><script>window.csrf_token="ccvisual";</script>');
}

/** 起服务：返回 { port, close(), unmocked }。 */
export function start({ dist, port = 18950 }) {
  const root = resolve(dist);
  if (!existsSync(join(root, "index.html"))) throw new Error(`产物目录里没有 index.html：${root}`);
  const unmocked = new Set();
  const holds = new Map(); // 接口方法名 → 挂到什么时候（加载骨架场景）
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host}`);
    req.resume();
    req.on("end", () => {
      const json = (body) => {
        res.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
        res.end(JSON.stringify(body));
      };
      if (url.pathname === "/__unmocked") return json([...unmocked].sort());
      if (url.pathname === "/__hold") {
        if (url.searchParams.has("clear")) holds.clear();
        else holds.set(url.searchParams.get("m"), Date.now() + Number(url.searchParams.get("ms") ?? 10000));
        return json({ ok: true });
      }
      if (url.pathname === "/api/method/pearnly_core.assistant.api.page_image") {
        res.writeHead(200, { "content-type": "image/png" });
        return res.end(PAGE_PNG);
      }
      if (url.pathname === "/api/method/pearnly_core.assistant.api.download") {
        res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
        return res.end(HTML_FILE);
      }
      if (url.pathname.startsWith("/api/method/")) {
        const name = url.pathname.slice("/api/method/".length);
        const wait = (holds.get(name) ?? 0) - Date.now();
        const reply = () => {
          if (!res.destroyed) json({ message: method(name, url.searchParams, unmocked) });
        };
        if (wait > 0) return void setTimeout(reply, wait);
        return reply();
      }
      if (url.pathname.startsWith(BASE)) {
        const file = join(root, decodeURIComponent(url.pathname.slice(BASE.length)));
        if (file.startsWith(root) && existsSync(file) && statSync(file).isFile()) {
          res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream", "cache-control": "no-store" });
          return res.end(readFileSync(file));
        }
      }
      if (url.pathname === "/assistant" || url.pathname === "/") {
        const lang = url.searchParams.get("__lang") ?? "zh";
        const html = url.searchParams.get("__boot") === "page" ? productionPage(lang) : distPage(root, lang);
        res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
        return res.end(html);
      }
      res.writeHead(404);
      res.end("");
    });
  });
  return new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(port, "127.0.0.1", () => ok({ port, unmocked, close: () => new Promise((r) => server.close(r)) }));
  });
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const arg = (name, fallback) => {
    const i = process.argv.indexOf(name);
    return i > 0 ? process.argv[i + 1] : fallback;
  };
  const dist = arg("--dist");
  if (!dist) {
    console.error("用法：node scripts/cc-visual/server.mjs --dist <产物目录> [--port 18950]");
    process.exit(2);
  }
  const s = await start({ dist, port: Number(arg("--port", 18950)) });
  console.log(`假服务端：http://127.0.0.1:${s.port}/assistant（产物 ${resolve(dist)}）`);
}
