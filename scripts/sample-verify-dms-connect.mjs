// 验证「开户 → 使用」闭环里最后缺的那一环：**租户自己把 DMS 连接配上**。
//
// 用真实浏览器走真路：访客 → 统一登录页 → 租户账号登录 → 产品壳 →
// 「连接第三方 DMS」→ 填账号密码 → **后端真登一次外部 DMS** → 通过才落库。
//
// 为什么必须走浏览器：登录 cookie 带 Secure，本机是 http，curl 不收
// （见 sample-verify-tenant-login.mjs 的说明）。
//
// 用法:
//   node scripts/sample-verify-dms-connect.mjs <租户域名> <端口> <登录账号> <密码> <DMS账号> <DMS密码>
//   例:
//   node scripts/sample-verify-dms-connect.mjs flowu1.localhost 8103 u1owner abc123 dmstest dmstest
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const [host, port, user, pass, dmsUser, dmsPass] = process.argv.slice(2);
if (!host || !port || !user || !pass || !dmsUser || !dmsPass) {
	console.error(
		"用法: node scripts/sample-verify-dms-connect.mjs <域名> <端口> <登录账号> <密码> <DMS账号> <DMS密码>"
	);
	process.exit(2);
}

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const SITE = `http://${host}:${port}`;
const APP = `${SITE}/assets/pearnly_core/pearnly-app/index.html`;

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));

const text = async (sel = "body") =>
	(((await page.textContent(sel)) || "") + "").replace(/\s+/g, " ").trim();

// ── 0. 访客打开站点：必须先去统一登录页 ────────────────────────────────────
await page.goto(`${SITE}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("#kc-form-login", { timeout: 30000 });
check("访客打开站点根 → 统一登录页", page.url().includes("keycloak"));

const loginLabels = await page.evaluate(() =>
	[...document.querySelectorAll("label, button, a")]
		.filter((e) => e.offsetParent !== null)
		.map((e) => (e.innerText || "").trim())
		.filter(Boolean)
);
check(
	"统一登录页没有第三方登录 / 注册入口",
	!loginLabels.some((l) => /Google|LINE|Facebook|Register|Sign up|注册/i.test(l))
);

// ── 1. 租户账号登录 ────────────────────────────────────────────────────────
await page.fill("#username", user);
await page.fill("#password", pass);
await page.click("#kc-login");
await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });
await page.waitForTimeout(2500);
check("租户账号登录成功（离开身份服务）", !page.url().includes("keycloak"), page.url().slice(0, 80));

// ── 2. 产品壳：侧栏要有「连接第三方 DMS」 ─────────────────────────────────
await page.goto(`${APP}#/connect`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.waitForTimeout(1500);

const sidebar = await text(".bg-surface-menu-bar");
console.log("  侧栏：", sidebar.slice(0, 120));
check("侧栏有「连接第三方 DMS」", sidebar.includes("连接第三方 DMS"));
check("侧栏没有「身份证录入」（网页端录入入口已砍）", !sidebar.includes("身份证录入"));

const before = await text();
const wasConnected = before.includes("已连接");
console.log(`  当前状态：${wasConnected ? "已连接（本脚本会改账号密码重连）" : "还没连接"}`);
check(
	"连接页有卡片与入口按钮",
	before.includes("连接第三方 DMS") && /连接 DMS|修改账号/.test(before),
	before.slice(0, 110)
);

// ── 3. 向导弹窗：地址写死隐藏，只填账号密码（照老产品） ───────────────────
await page.click("button:has-text('连接 DMS'), button:has-text('修改账号')");
await page.waitForSelector("[role='dialog'], .modal-container", { timeout: 10000 });
await page.waitForTimeout(600);

const dialogInputs = await page.evaluate(() => {
	const list = [...document.querySelectorAll("[role='dialog'], .modal-container")];
	const last = list[list.length - 1];
	if (!last) return [];
	return [...last.querySelectorAll("input")].map((i) => i.placeholder || i.type);
});
console.log("  弹窗输入框：", JSON.stringify(dialogInputs));
check(
	"弹窗只收账号与密码（没有地址框 —— 老产品把地址写死隐藏）",
	dialogInputs.length === 2 &&
		!dialogInputs.some((p) => /https?:\/\/|地址|url/i.test(p)),
	JSON.stringify(dialogInputs)
);

await page.fill("input[placeholder='DMS 登录账号']", dmsUser);
await page.fill("input[placeholder='DMS 登录密码']", dmsPass);
await page.screenshot({ path: `${SHOT_DIR}/dms-connect-wizard.png` });

// ── 4. 连接并保存：后端会**真登一次外部 DMS**，登上了才写库 ───────────────
console.log("  正在连接真实 DMS（Playwright 登录，几十秒）…");
await page.click("button:has-text('连接并保存')");
await page.waitForFunction(
	() => !/连接中…/.test(document.body.innerText || ""),
	{ timeout: 180000 }
);
await page.waitForTimeout(1200);

const after = await text();
check("弹窗已关闭（保存成功才会关）", after.indexOf("连接并保存") < 0 || !(await page.$("[role='dialog']")));
// 主档统计只可能来自那次"先测"的返回 —— 它出现即证明"先测再存"真的跑了
const SUMMARY_RE = /(车型 \d+ · 分店 \d+ · 顾问 \d+ · 称谓 \d+)/;
const masterLine = (after.match(/连接正常 · (车型[^取]*)/) || [])[1] || "";
const noticeLine = (after.match(/已连接 · (车型[^取]*)/) || [])[1] || "";
const summary = ((noticeLine || masterLine).match(SUMMARY_RE) || [])[1] || "";
check(`保存后回执带主档统计（${summary || "没有"}）`, /车型 \d+/.test(summary));

const okCount = (summary.match(/车型 (\d+)/) || [])[1] || "0";
const branchCount = (summary.match(/分店 (\d+)/) || [])[1] || "0";
check(`抓到了车型主档（${okCount} 条）`, Number(okCount) > 0);
check(`抓到了分店主档（${branchCount} 条）`, Number(branchCount) > 0);

// ── 5. 落库了没有：刷新一次还在 ───────────────────────────────────────────
await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.waitForTimeout(2000);
const reloaded = await text();
check("刷新后仍是「已连接」（真的落库了）", reloaded.includes("已连接"));
check(`连接卡片显示 DMS 账号 ${dmsUser}`, reloaded.includes(dmsUser));
check("卡片标出「有权限账号 ✓」", reloaded.includes("有权限账号"));
await page.screenshot({ path: `${SHOT_DIR}/dms-connect-connected.png` });

// ── 6. 已保存的连接能再测一次 ─────────────────────────────────────────────
console.log("  再测一次已保存的连接…");
await page.click("button:has-text('测试连接')");
await page.waitForFunction(
	() => !/测试中…/.test(document.body.innerText || ""),
	{ timeout: 180000 }
);
await page.waitForTimeout(1200);
const tested = await text();
check(
	"「测试连接」用已保存凭据测通",
	/连接正常 · 车型 \d+/.test(tested),
	(tested.match(/连接正常 · 车型[^取]*/) || ["没有回执"])[0].slice(0, 90)
);

// ── 7. 干净收尾 ───────────────────────────────────────────────────────────
await page.goto(`${APP}#/connect`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.waitForTimeout(1500);
const finalErrs = errs.filter((e) => !/ResizeObserver/.test(e));
check("没有 JS 报错", finalErrs.length === 0, finalErrs.slice(0, 2).join(" | "));

await browser.close();

console.log("");
if (failures.length) {
	console.log(`结果：${seq - failures.length} / ${seq} 通过，${failures.length} 项失败`);
	failures.forEach((f) => console.log("  ✗ " + f));
	process.exit(1);
}
console.log(`结果：${seq} / ${seq} 全过`);
