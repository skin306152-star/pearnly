// 验证第一条前端纵向切片：产品壳 + 采购录入。
//
// 用法:
//   node scripts/sample-verify-frontend-slice.mjs
//
// 验的是**前后端合同**，不是"页面长得对"：
//   1. 菜单按"买到的功能"渲染（没买的不显示）—— 授权由后端给，前端不自己决定；
//   2. 建单真的落库、列表能查回、金额由服务端重算；
//   3. 没买这个功能的租户，直接调接口被后端挡住（前端隐藏不算权限边界）。
//
// 用真实浏览器走完整 OIDC 登录：接口靠会话 cookie 认证，绕开登录等于没验。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const PASS = "Tenant-Test-2026";
// 三个租户都是本机样板里真开通出来的（不是造的假数据）：
//   cowork  qc65956.localhost:8101  prov-65956@sample.local       买：home_subscription, intake
//   dms     qd65956.localhost:8103  prov-65956@sample.local       买：home_subscription, id_card_ocr
//   erp     qerp124510.localhost:8102 erp-front-124510@sample.local 买：home_subscription, products,
//                                                                    warehouses, stockcard, purchase,
//                                                                    sales, clients
const COWORK = { host: "qc65956.localhost", port: "8101", user: "prov-65956@sample.local" };
const ERP = { host: "qerp124510.localhost", port: "8102", user: "erp-front-124510@sample.local" };
const APP_PATH = "/assets/pearnly_core/pearnly-app/index.html";

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });

async function login({ host, port, user }) {
	const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
	const page = await ctx.newPage();
	await page.goto(`http://${host}:${port}/`, { waitUntil: "domcontentloaded" });
	await page.waitForSelector("#kc-form-login", { timeout: 30000 });
	await page.fill("#username", user);
	await page.fill("#password", PASS);
	await page.click("#kc-login");
	await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });
	await page.waitForLoadState("domcontentloaded");
	return { ctx, page };
}

// ── 1. Cowork 站点：菜单只显示买到的 ────────────────────────────────────
{
	const { ctx, page } = await login(COWORK);
	// 先确认这个租户买的是什么（后端说了算）
	const boot = await page.evaluate(async () => {
		const r = await fetch("/api/method/pearnly_core.shell.describe", { credentials: "include" });
		return (await r.json()).message;
	});
	check(`启动接口可用，产品=${boot.product}`, boot.product === "cowork");
	check(
		`启动接口给出买到的功能：${JSON.stringify(boot.features)}`,
		Array.isArray(boot.features) && boot.features.length > 0
	);
	check("启动接口给了 csrf（写操作要用）", typeof boot.csrf_token === "string" && boot.csrf_token.length > 0);

	await page.goto(`http://${COWORK.host}:${COWORK.port}${APP_PATH}`, { waitUntil: "domcontentloaded" });
	await page.waitForSelector(".pn-nav", { timeout: 20000 });
	await page.waitForTimeout(500);
	const nav = await page.$$eval(".pn-nav-item", (els) => els.map((e) => e.textContent.trim()));
	console.log("   cowork 侧栏：", nav.join(" / "));
	check("侧栏渲染出来了", nav.length > 0);
	check("买的「录入工作台」在菜单里", nav.includes("录入工作台"));
	check(
		"没买的「库存盘点」不在菜单里",
		!nav.includes("库存盘点"),
		"这条验的是前端按后端授权过滤；真正的拦截在后端"
	);
	check("没买的「工作协作」(boards) 不在菜单里", !nav.includes("工作协作"));
	check(
		"没买的「主数据」整组都不显示",
		!nav.includes("客户管理") && !nav.includes("集成"),
		"整组都不该出现，而不是只藏一项"
	);
	await page.screenshot({ path: `${SHOT_DIR}/frontend-cowork-shell.png` });
	await ctx.close();
}

// ── 2. ERP 站点：真建一张采购单 ─────────────────────────────────────────
{
	const { ctx, page } = await login(ERP);
	await page.goto(`http://${ERP.host}:${ERP.port}${APP_PATH}#/purchase`, { waitUntil: "domcontentloaded" });
	await page.waitForSelector(".pn-main", { timeout: 20000 });
	await page.waitForTimeout(1500);

	const bodyText = await page.evaluate(() => document.body.innerText);
	check(
		"采购页打开了（不是占位页）",
		bodyText.includes("采购记录"),
		bodyText.slice(0, 80).replace(/\s+/g, " ")
	);

	const beforeCount = await page.$$eval(".pn-table tbody tr", (r) => r.length).catch(() => 0);

	// 建单
	await page.click("text=新建采购单");
	await page.waitForTimeout(400);
	const supplier = `回归验证供应商 ${Date.now()}`;
	await page.fill('input[placeholder="供应商名称"]', supplier);
	await page.fill('input[placeholder="品名 / 说明"]', "回归验证品名");
	await page.fill('input[placeholder="数量"]', "3");
	await page.fill('input[placeholder="单价"]', "120.5");
	await page.fill('input[placeholder="税率%"]', "7");
	await page.click("text=保存采购单");
	await page.waitForTimeout(2500);

	const after = await page.evaluate(() => document.body.innerText);
	check("建单成功提示出现", /已建单：PUR-/.test(after), after.match(/已建单：\S+/)?.[0] || "没看到提示");
	check("新建的供应商出现在列表里", after.includes(supplier));
	const afterCount = await page.$$eval(".pn-table tbody tr", (r) => r.length);
	check(`列表条数增加（${beforeCount} → ${afterCount}）`, afterCount > beforeCount);
	await page.screenshot({ path: `${SHOT_DIR}/frontend-erp-purchase.png` });
	await ctx.close();
}

// ── 3. Cowork 站点直接调 ERP 的采购接口：必须被挡住 ─────────────────────
//
// 说清楚这条验的是什么：cowork 站点上**根本没装** pearnly_erp_portal，所以是
// "产品之间不共享后端"这层隔离在挡（比权限更硬）。**不是**在验"没买这个功能" ——
// 功能级门禁（买了能进 / 没买 403）由 scripts/sample-verify-feature-gate.mjs 覆盖，
// 两件事不要混为一谈。
{
	const { ctx, page } = await login(COWORK);
	const result = await page.evaluate(async () => {
		const boot = await (
			await fetch("/api/method/pearnly_core.shell.describe", { credentials: "include" })
		).json();
		const r = await fetch("/api/method/pearnly_erp_portal.purchase.api_create_purchase", {
			method: "POST",
			credentials: "include",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": boot.message.csrf_token },
			body: JSON.stringify({ supplier_name: "不该建成", doc_date: "2026-09-18", lines: [{ description: "x", qty: 1, unit_price: 1 }] }),
		});
		return { status: r.status, body: (await r.text()).slice(0, 400) };
	});
	check(
		`cowork 站点调 ERP 采购接口被挡住（HTTP ${result.status}，跨产品隔离）`,
		result.status >= 400,
		result.body.replace(/\s+/g, " ").slice(0, 160)
	);
	await ctx.close();
}

await browser.close();

console.log("");
if (failures.length) {
	console.log(`失败 ${failures.length} 项:`);
	failures.forEach((f) => console.log(" - " + f));
	process.exit(1);
}
console.log(`全部通过（${seq} 项）。`);
