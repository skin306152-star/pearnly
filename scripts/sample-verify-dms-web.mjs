// 验证 DMS 网页端**不提供身份证录入入口**（用户 2026-09-17 定的：网页只负责绑定，
// 录入在 LINE 上做）。这件事落在三处，这个脚本验其中的后端与 Desk 两处
// （产品壳侧栏那一处由 `sample-verify-dms-roster.mjs` 验）：
//
//   1. 权限：网页端那张表对业务角色**只读** —— 界面上不会出现"新增"按钮，
//      **直接调接口也会被拒**（前端藏按钮不等于权限边界）；
//   2. Desk 工作台：入口名字是「推送记录」而不是「身份证录入」；
//   3. 只读不等于不能看：列表接口仍可读。
//
// 用法: node scripts/sample-verify-dms-web.mjs
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const SITE = { host: "qd65956.localhost", port: "8103", user: "prov-65956@sample.local" };
const PASS = "Tenant-Test-2026";
const APP = `http://${SITE.host}:${SITE.port}/assets/pearnly_core/pearnly-app/index.html`;
// 工作台是按**租户名**建的（控制面 `_workspace_for` 用 issue.tenant_name 当标题），
// 所以 Desk 那个入口的地址就是它。
const WORKSPACE = "开通验证租户 65956";

// 网页端**不该**再出现的录入类入口
const FORBIDDEN_NAV = ["身份证录入", "录入工作台", "库存盘点"];

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
const page = await ctx.newPage();

// ── 0. 统一登录 ────────────────────────────────────────────────────────────
await page.goto(`http://${SITE.host}:${SITE.port}/`, { waitUntil: "domcontentloaded" });
const onLoginPage = await page
	.waitForSelector("#kc-form-login", { timeout: 20000 })
	.then(() => true)
	.catch(() => false);
check("访客打开站点根 → 统一登录页", onLoginPage);

await page.fill("#username", SITE.user);
await page.fill("#password", PASS);
await page.click("#kc-login");
await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });

// ── 1. 侧栏：没有录入入口 ──────────────────────────────────────────────────
await page.goto(`${APP}#/home`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".bg-surface-menu-bar", { timeout: 20000 });
const sidebar = ((await page.textContent(".bg-surface-menu-bar")) || "").replace(/\s+/g, " ");
console.log("  侧栏：", sidebar.slice(0, 160));
for (const label of FORBIDDEN_NAV) {
	check(`侧栏没有「${label}」`, !sidebar.includes(label));
}

// ── 2. 权限边界：前端不给入口，后端也要拦 ─────────────────────────────────
// 用当前会话直接调录入接口：这张表在网页端只有 read，写会被拒。
const denied = await page.evaluate(async () => {
	const boot = await (await fetch("/api/method/pearnly_core.shell.describe")).json();
	const csrf = boot?.message?.csrf_token || "";
	const r = await fetch("/api/method/pearnly_dms.id_card.api_create_intake", {
		method: "POST",
		headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": csrf },
		body: JSON.stringify({ id_number: "1101700000099", full_name: "权限验证" }),
	});
	return { status: r.status, text: (await r.text()).slice(0, 120) };
});
check(
	`网页端直接调录入接口被后端拒（HTTP ${denied.status}）`,
	denied.status === 403,
	denied.text
);

// 只读仍然可用：列表接口照常返回（否则这一页就没法看了）
const canRead = await page.evaluate(async () => {
	const r = await fetch("/api/method/pearnly_dms.id_card.api_list_intake");
	return r.status;
});
check(`列表接口仍然可读（HTTP ${canRead}）`, canRead === 200);

// ── 3. Desk 工作台：入口名字也是「推送记录」 ───────────────────────────────
// 直接开工作台那一页（`/desk` 是桌面，不是工作台；落点见 tenant-landing 那条验证）。
// 地址是**连字符**形式（`开通验证租户-65956`），不是把空格编码成 %20 —— 用 %20
// 会被 desk 当成一个名叫那串的东西去找"页面"，报"没有权限访问 页面"（踩过）。
await page.goto(`http://${SITE.host}:${SITE.port}/desk/${WORKSPACE.replace(/ /g, "-")}`, {
	waitUntil: "domcontentloaded",
});
await page.waitForTimeout(2500);
// 只看**能点的链接**，不看整页文字：页面的启动数据里带着用户自己的角色名
// （其中就有"DMS 身份证录入"这个角色），用整页文本判断会把角色名当成入口
// —— "拿整页文字断言界面"这个坑在这一轮已经踩第二次了。
const deskLinks = (await page.locator("a").allTextContents())
	.map((t) => t.trim().replace(/\s+/g, " "))
	.filter(Boolean)
	.join(" | ");
check("Desk 工作台里有「推送记录」入口", deskLinks.includes("推送记录"));
check(
	"Desk 工作台里没有「身份证录入」入口",
	!deskLinks.includes("身份证录入"),
	deskLinks.slice(0, 160)
);
await page.screenshot({ path: `${SHOT_DIR}/frontend-dms-desk.png` });

await browser.close();

console.log("");
if (failures.length) {
	console.log(`失败 ${failures.length} 项:`);
	failures.forEach((f) => console.log(" - " + f));
	process.exit(1);
}
console.log(`全部通过（${seq} 项）。`);
