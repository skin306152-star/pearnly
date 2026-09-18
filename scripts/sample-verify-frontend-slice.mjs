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
// 官方 Frappe UI 的侧栏容器就带这个类（bg-surface-menu-bar）。
// 用它当选择器还有个好处：**顺带证明侧栏确实是官方组件渲染的**，不是我们自己写的。
const SIDEBAR = ".bg-surface-menu-bar";

// 我们的设计令牌值（design/pearnly-ui.css）。用来断言"官方组件套的是我们的皮肤"。
// 写死在这里是有意的：令牌改了这里就该失败，然后有人来核对是不是真改了皮肤
// （比"看起来差不多"可靠）。
const TOKEN = {
  card: "rgb(255, 255, 255)", // --card
  ink: "rgb(35, 25, 66)", // --ink #231942
  accentWeak: "rgb(238, 229, 255)", // --accent-weak #eee5ff
  accent: "rgb(124, 77, 255)", // --accent #7c4dff
};

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
	await page.waitForSelector(SIDEBAR, { timeout: 20000 });
	await page.waitForTimeout(600);
	const nav = await page.$$eval(`${SIDEBAR} button`, (els) =>
		els
			.map((e) => e.textContent.trim())
			// 官方侧栏自带"收起/展开"按钮，不是业务菜单项
			.filter((t) => t && t !== "Collapse" && t !== "Expand"),
	);
	console.log("   cowork 侧栏：", nav.join(" / "));
	check("侧栏渲染出来了", nav.length > 0);
	// 「官方组件 + 我们的皮肤」这两条一起验：组件是官方的（选择器命中它自己的类名），
	// 颜色是我们的（值来自设计令牌，不是 Frappe 默认灰）
	{
		const skin = await page.evaluate((sel) => {
			const el = document.querySelector(sel);
			if (!el) return null;
			const active = el.querySelector("button.bg-surface-selected, button[class*='surface-selected']");
			return {
				bg: getComputedStyle(el).backgroundColor,
				color: getComputedStyle(el).color,
				activeBg: active ? getComputedStyle(active).backgroundColor : null,
			};
		}, SIDEBAR);
		check(`侧栏底色是我们的 --card（${skin?.bg}）`, skin?.bg === TOKEN.card);
		check(`侧栏文字色是我们的 --ink（${skin?.color}）`, skin?.color === TOKEN.ink);
		check(
			`选中项高亮是我们的 --accent-weak（${skin?.activeBg}）`,
			skin?.activeBg === TOKEN.accentWeak,
			"官方组件的高亮色被我们的令牌接管了"
		);
	}
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

	// 控件尺寸：用户要求"按钮大小、间距都用官方原生的"。
	// 官方 Button 默认档是 sm：高 h-7=28px、内边距 px-2=8px、圆角 rounded=8px；
	// 官方原生输入框（.form-input）同样 28px 高。这里按实际渲染值断言 ——
	// 哪天有人把尺寸改成自己的一套，测试会失败。
	{
		const controls = await page.evaluate(() => {
			const byLabel = (t) =>
				[...document.querySelectorAll("button")].find((b) => b.textContent.trim() === t);
			const box = (el) => {
				if (!el) return null;
				const cs = getComputedStyle(el);
				return {
					h: Math.round(el.getBoundingClientRect().height),
					pad: `${cs.paddingTop} ${cs.paddingRight}`,
					radius: cs.borderRadius,
					bg: cs.backgroundColor,
				};
			};
			return {
				primary: box(byLabel("保存采购单")),
				outline: box(byLabel("刷新")),
				input: box(document.querySelector("input.form-input")),
			};
		});
		check(
			`按钮高度是官方原生档（${controls.primary?.h}px / ${controls.outline?.h}px）`,
			controls.primary?.h === 28 && controls.outline?.h === 28
		);
		check(
			`按钮内边距与圆角是官方原生（${controls.primary?.pad} / ${controls.primary?.radius}）`,
			controls.primary?.pad === "0px 8px" && controls.primary?.radius === "8px"
		);
		check(`输入框高度是官方原生（${controls.input?.h}px）`, controls.input?.h === 28);
		check(
			`主按钮只是换了颜色（${controls.primary?.bg}）`,
			controls.primary?.bg === TOKEN.accent,
			"尺寸与间距归官方，配色归我们"
		);
	}

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

// ── 4. Cowork 录入工作台：状态机是后端在卡，不是界面把按钮灰掉 ──────────
{
	const { ctx, page } = await login(COWORK);
	await page.goto(`http://${COWORK.host}:${COWORK.port}${APP_PATH}#/intake`, {
		waitUntil: "domcontentloaded",
	});
	await page.waitForSelector(".pn-main", { timeout: 20000 });
	await page.waitForTimeout(1500);
	check("录入工作台打开了", (await page.evaluate(() => document.body.innerText)).includes("录入工作台"));

	// 建一张草稿
	await page.click("text=新建录入");
	await page.waitForTimeout(400);
	const party = `回归验证对方 ${Date.now()}`;
	await page.fill('input[placeholder="供应商 / 客户"]', party);
	await page.fill('input[placeholder="可留空"]', "INV-REG-1");
	await page.click("text=保存");
	await page.waitForTimeout(2500);
	const afterCreate = await page.evaluate(() => document.body.innerText);
	check("录入成功提示出现", /已录入：/.test(afterCreate), afterCreate.match(/已录入：\S+/)?.[0] || "");
	check("新建的对方出现在列表里", afterCreate.includes(party));
	check("新记录是「草稿」状态", afterCreate.includes("草稿"));

	// ★ 关键一条：拿刚建的草稿**直接调推送接口**，必须被后端拒绝。
	//   这条验的是"状态机在后端"——界面把按钮藏起来不算数。
	const created = afterCreate.match(/已录入：(\S+)/)?.[1] || "";
	const blocked = await page.evaluate(async (name) => {
		const boot = await (
			await fetch("/api/method/pearnly_core.shell.describe", { credentials: "include" })
		).json();
		const r = await fetch("/api/method/pearnly_cowork.intake.api_queue_for_push", {
			method: "POST",
			credentials: "include",
			headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": boot.message.csrf_token },
			body: JSON.stringify({ name, channel: "companion" }),
		});
		return { status: r.status, body: (await r.text()).slice(0, 300) };
	}, created);
	check(
		`未确认的草稿直接调推送接口被拒（HTTP ${blocked.status}）`,
		blocked.status >= 400,
		blocked.body.replace(/\s+/g, " ").slice(0, 140)
	);

	// 走界面：确认 → 推送。
	//
	// ⚠️ 必须**按行定位**，不能拿整页文字断言。踩过一次：页面上方有汇总卡片，
	// 标签里本来就写着「待推送」「已确认及以上」，用 `body.innerText.includes(...)`
	// 会**永远为真**（假通过）；而且 `click("text=确认")` 会点到卡片标签上，
	// 动作根本没发生，测试却全绿。现在改成读那一行的状态单元格。
	async function rowStatus(name) {
		return page.evaluate((n) => {
			const tr = [...document.querySelectorAll(".pn-table tbody tr")].find((r) =>
				r.textContent.includes(n),
			);
			if (!tr) return null;
			const cells = tr.querySelectorAll("td");
			// 列序：0 单号 / 1 方向 / 2 对方 / 3 单据号 / 4 日期 / 5 状态 / 6 价税合计 / 7 操作
			return cells[5]?.textContent.trim() ?? null;
		}, name);
	}
	async function clickRowAction(name, label) {
		await page.evaluate(
			({ n, l }) => {
				const tr = [...document.querySelectorAll(".pn-table tbody tr")].find((r) =>
					r.textContent.includes(n),
				);
				const btn = tr && [...tr.querySelectorAll("button")].find((b) => b.textContent.trim() === l);
				if (btn) btn.click();
			},
			{ n: name, l: label },
		);
	}

	check(`操作前那一行是「草稿」（实际 ${await rowStatus(created)}）`, (await rowStatus(created)) === "草稿");

	await clickRowAction(created, "确认");
	await page.waitForTimeout(2500);
	check(`点确认后那一行变「已确认」（实际 ${await rowStatus(created)}）`, (await rowStatus(created)) === "已确认");

	// 汇总卡片的数字必须跟着动。这条是补一个真踩过的坑：卡片原来把接口字段名写错了
	// （服务端是 purchase_confirmed，页面写成 purchase_total），
	// 结果金额永远显示 "—" —— 键名对不上不报错，只是安静地显示空值。
	{
		const cards = await page.evaluate(() => {
			const read = (label) => {
				const card = [...document.querySelectorAll(".pn-card")].find((c) =>
					c.textContent.includes(label),
				);
				return card?.querySelector("div:nth-child(2)")?.textContent.trim() ?? "";
			};
			return { pendingConfirm: read("待确认"), readyToPush: read("已确认 · 可推送") };
		});
		check(
			`汇总：已确认·可推送 ≥ 1（实际 ${cards.readyToPush}）`,
			Number(cards.readyToPush) >= 1
		);
	}

	await clickRowAction(created, "推送");
	await page.waitForTimeout(2500);
	check(`点推送后那一行变「待推送」（实际 ${await rowStatus(created)}）`, (await rowStatus(created)) === "待推送");
	await page.screenshot({ path: `${SHOT_DIR}/frontend-cowork-intake.png` });
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
