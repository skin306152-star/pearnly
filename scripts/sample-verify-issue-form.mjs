// 验「开户单」的形式：字段是**邮箱 + 用户名 + 初始密码**，且页面上不堆解释文案。
//
// 用户 2026-09-18 的两条要求：
//   1. "使用姓名改为用户名，联系电话改为初始密码" —— 换成邮箱/用户名/初始密码三件套，
//      它们构成首次绑定关系；
//   2. "不要加那么多文案说明，字段对齐" —— 每个字段下面不该再挂一段解释。
//
// 控制面用的是本地账号（ADR-004），不走统一身份，所以这里登的是 Frappe 登录页。
//
// 用法: node scripts/sample-verify-issue-form.mjs [平台用户名] [平台密码]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const user = process.argv[2] || "earn";
const pass = process.argv[3] || "earn";
const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const BASE = "http://control.localhost:8104";

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));

await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#login_email", user);
await page.fill("#login_password", pass);
await page.click("button.btn-login");
await page.waitForURL((u) => !u.href.includes("/login"), { timeout: 30000 });
check("控制面登录成功", !page.url().includes("/login"), page.url().slice(0, 60));

await page.goto(`${BASE}/app/pearnly-issue/new`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("input[data-fieldname='tenant_name']", { timeout: 30000 });
await page.waitForTimeout(1200);

const visible = async (sel) => !!(await page.$(`${sel}:visible`));

check("有「公司名称」", await visible("input[data-fieldname='tenant_name']"));
check("有「邮箱」", await visible("input[data-fieldname='account_email']"));
check("有「用户名」", await visible("input[data-fieldname='account_username']"));
check("有「初始密码」，且是密码框", await visible("input[data-fieldname='account_password'][type='password']"));

check(
	"没有「使用姓名」（旧字段已去掉）",
	!(await visible("[data-fieldname='account_name']"))
);
check(
	"没有「联系电话」（旧字段已去掉）",
	!(await visible("[data-fieldname='contact_phone']"))
);
// delivery 字段仍在表里（老单子的历史数据要留着），但表单上不再出现
check("表单上不显示「凭据交付方式」（已固定用初始密码）", !(await visible("[data-fieldname='delivery']")));

// 字段下面的解释文案：Frappe 渲染成 `.help-box` / `.description` 之类的小字。
// 要求是**一个都不留** —— 有就说明文案又堆回来了。
const helps = await page.$$eval(
	".frappe-control .help-box, .frappe-control .description",
	(els) => els.map((e) => (e.textContent || "").trim()).filter(Boolean)
);
check("字段下面没有解释文案", helps.length === 0, helps.slice(0, 3).join(" | "));

// 字段标签取每个控件的 label 文本，确认叫法统一（不出现"登录账号"这种混称）
const labels = await page.$$eval(".frappe-control[data-fieldname] label", (els) =>
	els.map((e) => (e.textContent || "").replace(/\s+/g, "").trim()).filter(Boolean)
);
console.log("  字段标签：", labels.join(" / "));
check("标签里没有「登录账号」这种混称", !labels.some((l) => l.includes("登录账号")));

await page.screenshot({ path: `${SHOT_DIR}/issue-form-fields.png`, fullPage: true });

const finalErrs = errs.filter((e) => !/ResizeObserver|Cannot read properties of null/.test(e));
check("没有 JS 报错", finalErrs.length === 0, finalErrs.slice(0, 2).join(" | "));

await browser.close();

console.log("");
if (failures.length) {
	console.log(`结果：${seq - failures.length} / ${seq} 通过，${failures.length} 项失败`);
	failures.forEach((f) => console.log("  ✗ " + f));
	process.exit(1);
}
console.log(`结果：${seq} / ${seq} 全过`);
