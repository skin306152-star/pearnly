// 验证控制面 /desk 是否还会整页无限重载（闪屏）。
// 用法：
//   NODE_PATH=<pearnly-app>/node_modules node scripts/sample-verify-desk-stable.mjs [base]
// 判定标准：
//   同一个 URL 在观察窗口内只发生 1 次文档导航，且 Desk 工具栏已渲染。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

// playwright 不在本仓库的依赖里，从外部解析（默认借用 pearnly-app 的 node_modules）。
const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const base = process.argv[2] || "http://control.localhost:8104";
const USER = process.env.PEARNLY_DESK_USER || "earn";
const PASS = process.env.PEARNLY_DESK_PASS || "earn";
const WATCH_MS = 8000;
// 复现用户原始动作：打开一个表单页，点左侧栏的“主页”。控制面默认用租户管理员单据。
const DOC_PATH = process.env.PEARNLY_DESK_DOC || "/desk/user/tenant-admin@sample.local";
const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";

const navs = [];

async function probe(page, label, slug, url) {
	navs.length = 0;
	await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
	const before = navs.length;
	await page.waitForTimeout(WATCH_MS);
	const during = navs.length - before;
	let shell = false;
	let here = page.url();
	try {
		// 坏状态下 desk.js 的 make_nav_bar() 会整个跳过工具栏，所以这里要求“应用外壳已渲染”。
		// 桌面页用 .navbar，工作台路由用 .page-head（两处都验过）。
		shell = await page.evaluate(() => !!(window.frappe?.boot && document.querySelector(".navbar, .page-head")));
		here = await page.evaluate(() => location.href);
	} catch {}
	const stable = during <= 1;
	let shot = "";
	try {
		mkdirSync(SHOT_DIR, { recursive: true });
		shot = `${SHOT_DIR}/desk-stable-${slug}.png`;
		await page.screenshot({ path: shot, fullPage: false });
	} catch {}
	console.log(
		`${label}: url=${url} 窗口内文档导航=${during} 界面已渲染=${shell} 最终地址=${here} => ${stable ? "稳定" : "仍在重载"}`
	);
	if (shot) console.log(`   截图: ${shot}`);
	return { during, shell, stable: stable && shell, here };
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext();
const page = await ctx.newPage();
page.on("framenavigated", (f) => {
	if (f === page.mainFrame()) navs.push({ url: f.url(), t: Date.now() });
});

// 登录
await page.goto(`${base}/login`, { waitUntil: "domcontentloaded", timeout: 30000 });
let already = false;
try {
	already = await page.evaluate(() => !!(window.frappe?.boot && window.frappe.boot.user && window.frappe.boot.user !== "Guest"));
} catch {}
if (!already) {
	await page.fill('input[name="usr"], #login_email', USER);
	await page.fill('input[name="pwd"], #login_password', PASS);
	await Promise.all([page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}), page.click('button[type="submit"], .btn-login')]);
	console.log("登录后地址:", page.url());
}

const r1 = await probe(page, "A. 直接打开 /desk", "a-direct-desk", `${base}/desk`);
const r2 = await probe(page, "B. 走首页入口 /app（点首页等价路径）", "b-home-entry", `${base}/app`);

// D. 复现“点首页就闪”：表单页 -> 左侧栏“主页”
let r4 = null;
navs.length = 0;
await page.goto(`${base}${DOC_PATH}`, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
await page.waitForTimeout(5000);
const beforeClick = navs.length;
const urlBeforeClick = page.url();
let clickInfo = "未找到侧边栏“主页”入口";
try {
	const item = page.locator(".standard-sidebar-item, .sidebar-item-container").filter({ hasText: /^\s*主页/ }).first();
	await item.click({ timeout: 5000 });
	clickInfo = "已点击侧边栏“主页”";
} catch (e) {}
await page.waitForTimeout(WATCH_MS);
const during = navs.length - beforeClick;
let shell = false;
let here = page.url();
try {
	shell = await page.evaluate(() => !!(window.frappe?.boot && document.querySelector(".navbar, .page-head")));
	here = await page.evaluate(() => location.href);
} catch {}
// 点击必须真的换到别的路由，否则“导航数=0”是假阳性（什么都没点成功）
const routed = here !== urlBeforeClick;
if (clickInfo.startsWith("已点击") && routed) {
	r4 = { during, shell, stable: during <= 1 && shell };
	console.log(`D. ${clickInfo}：${urlBeforeClick.replace(base, "")} -> ${here.replace(base, "")}，窗口内文档导航=${during} 界面已渲染=${shell} => ${r4.stable ? "稳定" : "仍在重载"}`);
	const shot = `${SHOT_DIR}/desk-stable-d-home-click.png`;
	await page.screenshot({ path: shot }).catch(() => {});
	console.log(`   截图: ${shot}`);
} else if (clickInfo.startsWith("已点击")) {
	console.log(`D. ${clickInfo}，但路由没变（${urlBeforeClick}）——本次不作为有效证据`);
} else {
	console.log(`D. ${clickInfo}（本环境无此入口，跳过）`);
}

const checks = [r1, r2, r4].filter(Boolean);
const ok = checks.every((r) => r.stable);
console.log(ok ? "结论：PASS —— 不再闪屏" : "结论：FAIL —— 仍有重载或工具栏未渲染");
await browser.close();
process.exit(ok ? 0 : 1);
