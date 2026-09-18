// 验"点一下就走完"的开户：在 Desk 表单上填公司名、账号、产品域名，
// 点一次「发号并开通」，剩下的（建号 → 建站 → 装 App → 发角色 → 建工作台）应该自己跑完。
//
// 用法: node scripts/sample-verify-onboarding-ui.mjs [产品] [平台用户名] [平台密码]
//   node scripts/sample-verify-onboarding-ui.mjs dms
//
// 前提：常驻执行器在跑（scripts/provision-runner.sh start）——
// 建站要在别的容器里跑 bench，控制面容器里没有 docker。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const product = process.argv[2] || "dms";
const user = process.argv[3] || "earn";
const pass = process.argv[4] || "earn";

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const BASE = "http://control.localhost:8104";
const STAMP = String(Date.now()).slice(-6);
const TENANT = `一键开通验证 ${STAMP}`;
const EMAIL = `onestop-${STAMP}@sample.local`;
const USERNAME = `onestop${STAMP}`;
const PASSWORD = "abc123";
const PREFIX = `one${STAMP}`;

const PORT = { cowork: "8101", erp: "8102", dms: "8103" }[product];
const HOST = `${PREFIX}.localhost`;

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 }, locale: "zh-CN" });
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));

// ── 1. 登录控制面 ──────────────────────────────────────────────────────────
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#login_email", user);
await page.fill("#login_password", pass);
await page.click("button.btn-login");
await page.waitForURL((u) => !u.href.includes("/login"), { timeout: 30000 });
check("控制面登录成功", !page.url().includes("/login"));

// ── 2. 打开新开户单，只填四个字段 + 一行产品 ───────────────────────────────
await page.goto(`${BASE}/app/pearnly-issue/new`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("input[data-fieldname='tenant_name']", { timeout: 30000 });
await page.waitForTimeout(1000);

// 主字段走真实输入框。⚠️ 只 `fill` 不够：它触发的是 `input`，
// 而 Desk 的控件是听 `change` 的 —— 不补这一下，界面上有值、模型里没有，
// 保存就会失败（踩过：按钮点了没反应，状态一直是"尚未保存"）。
async function fillField(fieldname, value) {
	const sel = `[data-fieldname='${fieldname}'] input`;
	await page.fill(sel, value);
	await page.dispatchEvent(sel, "change");
	await page.dispatchEvent(sel, "blur");
}
await fillField("tenant_name", TENANT);
await fillField("account_email", EMAIL);
await fillField("account_username", USERNAME);
await fillField("account_password", PASSWORD);

// 子表用表单自己的 API 填行（Desk 的 grid 就是用它，
// 而且逐格点选 Select 的写法很脆，稳不住会变成"测选择器"而不是"测流程"）。
// ⚠️ 新表单 Desk **预置了一行空的**：直接用那一行，别再 add_child ——
// 否则多出一行 app 有值、前缀为空的行，保存会被 validate 挡掉，
// 表现是"点了按钮没反应"（踩过）。
await page.evaluate(
	([app, prefix]) => {
		const frm = window.cur_frm;
		const row = (frm.doc.apps || [])[0] || frm.add_child("apps");
		row.app = app;
		row.domain_prefix = prefix;
		frm.refresh_field("apps");
	},
	[product, PREFIX]
);
await page.waitForTimeout(400);

// 光看界面不算数：要确认**模型**里也有值，否则保存必然失败
const model = await page.evaluate(() => {
	const d = window.cur_frm.doc;
	return {
		tenant: d.tenant_name,
		email: d.account_email,
		username: d.account_username,
		passwordLen: (d.account_password || "").length,
		apps: (d.apps || []).map((r) => ({ app: r.app, prefix: r.domain_prefix })),
	};
});
check(
	"四个字段进了表单模型（不是只有界面显示）",
	model.tenant === TENANT &&
		model.email === EMAIL &&
		model.username === USERNAME &&
		model.passwordLen === PASSWORD.length,
	JSON.stringify({ ...model, passwordLen: model.passwordLen })
);
check(
	`产品行进了模型（${product} / ${PREFIX}）`,
	model.apps.some((a) => a.app === product && a.prefix === PREFIX),
	JSON.stringify(model.apps)
);

// ── 3. 草稿态：按钮是「发号并开通」 ─────────────────────────────────────────
const buttonsOf = () =>
	page.evaluate(() =>
		// 表单工具栏在 v16 里分好几块：主按钮、inner_toolbar（add_custom_button 落这儿）、分组下拉
		[...document.querySelectorAll(".page-head button, .page-actions button, .inner-toolbar button")]
			.map((b) => (b.innerText || "").trim())
			.filter(Boolean)
	);
let buttons = await buttonsOf();
console.log("  工具栏按钮：", buttons.join(" / "));
check("草稿态出现「发号并开通」", buttons.some((b) => b.includes("发号并开通")));

// ── 4. 点一下，等它自己跑完 ────────────────────────────────────────────────
console.log(`  点「发号并开通」（公司 ${TENANT} → ${PREFIX}.localhost:${PORT}）…`);
const t0 = Date.now();
await page.locator("button", { hasText: "发号并开通" }).first().click();

async function phase() {
	return page.evaluate(() => ({
		indicator: (document.querySelector(".indicator-pill")?.innerText || "").trim(),
		buttons: [...document.querySelectorAll(".page-head button, .page-actions button, .inner-toolbar button")]
			.map((b) => (b.innerText || "").trim()),
	}));
}

// 发号是同步的（一次请求），先等 "已发号" 提示或按钮变化
await page.waitForTimeout(3000);
let ph = await phase();
console.log("  发号后：", ph.indicator, "|", ph.buttons.join(" / "));
if (ph.indicator.includes("尚未保存")) {
	// 没存下去就一定有弹窗说明原因 —— 把它抓出来，不然只能看到"没反应"
	const why = await page
		.evaluate(() =>
			[...document.querySelectorAll(".modal-body, .msgprint")]
				.map((e) => (e.innerText || "").replace(/\s+/g, " ").trim())
				.filter(Boolean)
				.join(" | ")
		)
		.catch(() => "");
	console.log("    界面上的说明：", why ? why.slice(0, 300) : "（没有弹窗）");
}
check("发号后状态灯不再是「未发号」", !ph.indicator.includes("未发号"), ph.indicator);
check(
	"发号后按钮变成开通相关",
	ph.buttons.some((b) => /执行开通|开通进度|查看进度/.test(b)),
	ph.buttons.join(" / ")
);

// 等「已开通」：建站 + 装 App + 重启 web，本机实测几十秒
const DEADLINE = Date.now() + 8 * 60 * 1000;
let final = ph;
while (Date.now() < DEADLINE) {
	final = await phase();
	if (/已开通/.test(final.indicator)) break;
	if (/失败/.test(final.indicator)) break;
	await page.waitForTimeout(4000);
}
const elapsed = Math.round((Date.now() - t0) / 1000);
console.log(`  最终状态：${final.indicator}（用时 ${elapsed}s）`);
check("状态灯最终是「已开通」", /已开通/.test(final.indicator), final.indicator);

// ── 5. 服务端这边确实建出来了 ──────────────────────────────────────────────
const state = await page.evaluate(
	(issue) =>
		fetch(`/api/method/pearnly_control.provisioning.api_state_for_issue?issue=${issue}`)
			.then((r) => r.json())
			.then((j) => j.message),
	await page.evaluate(() => window.cur_frm.doc.name)
);
const steps = state?.steps || [];
const notDone = steps.filter((s) => !["done", "skipped"].includes(s.status));
check("所有步骤都完成或明确跳过（没有悬着的）", notDone.length === 0, JSON.stringify(notDone));
const skipped = steps.filter((s) => s.status === "skipped").map((s) => s.step_key);
if (skipped.length) console.log("  记为 skipped 的步骤：", skipped.join(", "));

await page.screenshot({ path: `${SHOT_DIR}/onboarding-done.png` });

// ── 6. 真的能用：拿租户账号登进新站点 ──────────────────────────────────────
const site = `http://${HOST}:${PORT}`;
const tenantPage = await ctx.newPage();
await tenantPage.goto(`${site}/`, { waitUntil: "domcontentloaded" });
await tenantPage.waitForSelector("#kc-form-login", { timeout: 30000 });
check("新站点访客被送去统一登录页", tenantPage.url().includes("keycloak"), site);
await tenantPage.fill("#username", USERNAME);
await tenantPage.fill("#password", PASSWORD);
await tenantPage.click("#kc-login");
await tenantPage.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });
await tenantPage.waitForTimeout(2000);
check("用用户名 + 初始密码登得进去", !tenantPage.url().includes("keycloak"), tenantPage.url().slice(0, 70));

const boot = await tenantPage.evaluate(() =>
	fetch("/api/method/pearnly_core.shell.describe").then((r) => r.json()).then((j) => j.message)
);
console.log(`  租户买到的功能：${(boot?.features || []).length} 项`);
check("租户拿到该产品的功能（不是空的）", (boot?.features || []).length > 0);
check(`产品识别正确（${product}）`, boot?.product === product, String(boot?.product));
await tenantPage.screenshot({ path: `${SHOT_DIR}/onboarding-tenant-login.png` });

const finalErrs = errs.filter((e) => !/ResizeObserver/.test(e));
check("没有 JS 报错", finalErrs.length === 0, finalErrs.slice(0, 2).join(" | "));

await browser.close();

console.log("");
console.log(`本站点：${site}　账号 ${USERNAME} / ${PASSWORD}　单号见上面`);
if (failures.length) {
	console.log(`结果：${seq - failures.length} / ${seq} 通过，${failures.length} 项失败`);
	failures.forEach((f) => console.log("  ✗ " + f));
	process.exit(1);
}
console.log(`结果：${seq} / ${seq} 全过`);
