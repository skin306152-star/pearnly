// 验证 DMS 网页端**收窄后**的形态：
//   - 网页端只有：首页 / 连接第三方 DMS / LINE 绑定码 / 推送记录 / 操作员
//   - **没有**身份证录入入口（业务在 LINE 上做，用户 2026-09-17 定的）
//   - 推送记录是真的读后端（不是写死的假数据）
//   - LINE 绑定码能真生成
//   - 网页端**直接调录入接口也会被后端拒** —— 前端不显示入口不等于权限边界
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

// 收窄后**应该**有的菜单项（顺序也照清单）
const EXPECTED_NAV = ["首页", "连接第三方 DMS", "LINE 绑定码", "推送记录", "操作员"];
// 收窄后**不该**再出现的入口
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

// ── 1. 侧栏：收窄后的形态 ──────────────────────────────────────────────────
await page.goto(`${APP}#/home`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".bg-surface-menu-bar", { timeout: 20000 });
const sidebar = ((await page.textContent(".bg-surface-menu-bar")) || "").replace(/\s+/g, " ");
console.log("  侧栏：", sidebar.slice(0, 160));

for (const label of EXPECTED_NAV) {
	check(`侧栏有「${label}」`, sidebar.includes(label));
}
for (const label of FORBIDDEN_NAV) {
	check(`侧栏**没有**「${label}」`, !sidebar.includes(label));
}

// ── 2. 推送记录：真读后端 ──────────────────────────────────────────────────
await page.goto(`${APP}#/records`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.waitForTimeout(1200); // 等列表请求回来

const bodyText = ((await page.textContent("body")) || "").replace(/\s+/g, " ");
const rowCount = await page.locator(".pn-table tbody tr").count();
check(`推送记录有行（${rowCount} 行）`, rowCount >= 3, bodyText.slice(0, 100));
check("列表里有后端那条已推送的记录（IDC-2026-00001）", bodyText.includes("IDC-2026-00001"));
check("状态显示成中文（已推送）", bodyText.includes("已推送"));
check("客户匹配显示成中文（新客户 / 已有客户）", bodyText.includes("新客户") && bodyText.includes("已有客户"));
// 这一页不给新建 —— 网页端不做录入。
// 注意只看**按钮**：页面说明文字里本来就有"录入"这个词（说的是"录入在 LINE 上做"），
// 用整页文字判断会把说明文字也当成入口（踩过：拿整页文本断言按钮）。
const newButtons = await page.locator("button", { hasText: /新建|录入/ }).count();
check(`推送记录页没有「新建 / 录入」按钮（找到 ${newButtons} 个）`, newButtons === 0);
await page.screenshot({ path: `${SHOT_DIR}/frontend-dms-records.png` });

// ── 3. LINE 绑定码：真生成 ─────────────────────────────────────────────────
await page.goto(`${APP}#/line-bind`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.click("text=生成绑定码");
await page.waitForTimeout(1500);

// 码是 6 位、字母表里没有容易看错的字符（0/O、1/I/l）
const code = await page.evaluate(() => {
	const el = [...document.querySelectorAll("div")].find((d) =>
		/^[A-Z0-9]{6}$/.test((d.textContent || "").trim()),
	);
	return el ? el.textContent.trim() : "";
});
check(`点「生成绑定码」拿到了 6 位码（${code || "没拿到"}）`, /^[ACDEFGHJKLMNPQRSTUVWXY23456789]{6}$/.test(code));

// 界面上显示的那个码，后端确实认（不是前端自己编的）
const backendCode = await page.evaluate(async () => {
	const r = await fetch("/api/method/pearnly_core.line_binding.api_my_bindings");
	const d = await r.json();
	return d?.message?.pending_code?.code || "";
});
check(`后端记的就是这个码（后端=${backendCode}）`, Boolean(code) && code === backendCode);
await page.screenshot({ path: `${SHOT_DIR}/frontend-dms-line-bind.png` });

// ── 4. 权限边界：前端不给入口，后端也要拦 ─────────────────────────────────
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

// ── 5. Desk 工作台：入口也改名/改形态了 ────────────────────────────────────
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
	"Desk 工作台里**没有**「身份证录入」入口",
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
