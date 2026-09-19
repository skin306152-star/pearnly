// 验"DocType 标签走翻译包"：同一张表在当前界面语言下应显示**这门语言**，
// 而不是库里的英文 msgid。
//
// 为什么需要这条：DocType 的 label 现在存英文（"Company Name"），中文/泰文是翻译包给的。
// 翻译包漏一条，界面上就会出现英文原样 —— 那种"缺一条"只有打开页面才看得见。
//
// 用法（脚本只读当前界面语言，切语言在外面做）:
//   PEARNLY_I18N_LANG=zh node scripts/sample-verify-i18n-labels.mjs
//   PEARNLY_I18N_LANG=th node scripts/sample-verify-i18n-labels.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const BASE = process.env.PEARNLY_CONTROL_URL || "http://control.localhost:8104";
const USER = process.env.PEARNLY_OPS_USER || "earn";
const PASS = process.env.PEARNLY_OPS_PASS || "earn";
const LANG = process.env.PEARNLY_I18N_LANG || "zh";

// 期望看到的字段名（开通单 + 租户表），以及**不该**原样出现的英文 msgid
const EXPECT = {
	zh: {
		want: ["公司名称", "邮箱", "用户名", "初始密码", "产品与域名", "备注"],
		absent: ["Company Name", "Username", "Initial Password", "Products & Domains"],
	},
	th: {
		want: ["ชื่อบริษัท", "อีเมล", "ชื่อผู้ใช้", "รหัสผ่านเริ่มต้น", "ผลิตภัณฑ์และโดเมน", "หมายเหตุ"],
		absent: ["Company Name", "Username", "Initial Password", "Products & Domains"],
	},
};

const { want, absent } = EXPECT[LANG] || EXPECT.zh;
const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 140)));

await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#login_email", USER);
await page.fill("#login_password", PASS);
await page.click(".btn-login");
await page.waitForTimeout(4000);

// [1] 新建开通单：字段名应全是本语言
await page.goto(`${BASE}/desk/pearnly-issue/new`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4000);
const issueText = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
for (const text of want) {
	check(`开通单出现「${text}」（${LANG}）`, issueText.includes(text));
}
for (const text of absent) {
	check(`开通单不出现英文 msgid「${text}」`, !issueText.includes(text));
}
await page.screenshot({ path: `${SHOT_DIR}/i18n-labels-issue-${LANG}.png` });

// [2] 租户表：字段名同样
await page.goto(`${BASE}/desk/pearnly-tenant`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
const tenantText = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
check(
	`租户列表表头是本语言（${LANG}）`,
	tenantText.includes(want[5]) || tenantText.includes(want[0]),
	tenantText.slice(0, 120)
);
await page.screenshot({ path: `${SHOT_DIR}/i18n-labels-tenant-${LANG}.png` });

check(`全程没有 JS 报错`, errs.length === 0, errs.join(" | "));
console.log(`说明: 截图 ${SHOT_DIR}/i18n-labels-issue-${LANG}.png`);

await ctx.close();
await browser.close();
if (failures.length) {
	console.log(`\n失败 ${failures.length} 项：\n  - ` + failures.join("\n  - "));
	process.exit(1);
}
console.log(`\n全部通过（${seq} 项）。`);
