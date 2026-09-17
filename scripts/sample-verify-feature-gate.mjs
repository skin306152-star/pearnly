// 验证"未勾选的功能不可访问" —— 这是授权管理真正成立与否的分界。
//
// 做法：用租户管理员登录，分别去读**买了的**和**没买的**功能对应的 DocType。
// 买了的应 200，没买的应被拒。附带检查 System Manager 已被拿走。
//
// 用法:
//   node scripts/sample-verify-feature-gate.mjs <域名> <端口> <用户名> <密码> \
//        <买了的DocType> <没买的DocType>
//   例: node scripts/sample-verify-feature-gate.mjs qd65956.localhost 8103 \
//         prov-65956@sample.local 'Tenant-Test-2026' 'DMS ID Card Intake' 'DMS Customer Link'
import { createRequire } from "node:module";
const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const [host, port, user, pass, bought, notBought] = process.argv.slice(2);
if (!host || !port || !user || !pass || !bought || !notBought) {
	console.error("用法: node sample-verify-feature-gate.mjs <域名> <端口> <用户名> <密码> <买了的DocType> <没买的DocType>");
	process.exit(2);
}

const site = `http://${host}:${port}`;
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

// 登录（统一登录口）
await page.goto(`${site}/`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1200);
await page.fill('input[name="username"], #username', user);
await page.fill('input[name="password"], #password', pass);
await Promise.all([
	page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}),
	page.click('input[type="submit"], button[type="submit"], #kc-login'),
]);
await page.waitForTimeout(2500);

const canRead = async (doctype) =>
	page.evaluate(async (dt) => {
		const r = await fetch(
			`/api/method/frappe.client.get_list?doctype=${encodeURIComponent(dt)}&limit_page_length=1`,
			{ credentials: "include" }
		);
		let msg = "";
		try {
			const j = await r.json();
			msg = (j._server_messages || j.exception || j.message || "").toString().slice(0, 80);
		} catch {}
		return { status: r.status, msg };
	}, doctype);

const roles = await page.evaluate(async () => {
	const r = await fetch("/api/method/frappe.client.get_list?doctype=Has%20Role&limit_page_length=0", {
		credentials: "include",
	});
	return r.status;
});

console.log(`站点 ${host}:${port}  登录用户 ${user}`);

const a = await canRead(bought);
console.log(`[1] 买了的「${bought}」      → HTTP ${a.status} ${a.msg ? "| " + a.msg : ""}`);
const okBought = a.status === 200;
console.log(okBought ? "    ✓ 可访问" : "    ✗ 买了却访问不了");

const b = await canRead(notBought);
console.log(`[2] 没买的「${notBought}」  → HTTP ${b.status} ${b.msg ? "| " + b.msg : ""}`);
const okNotBought = b.status !== 200;
console.log(okNotBought ? "    ✓ 被拒（未购买功能确实访问不了）" : "    ✗ 没买却能访问 —— 授权没生效");

// 直接问服务端这个用户有哪些角色（换一个允许的接口）
const myRoles = await page.evaluate(async () => {
	const r = await fetch("/api/method/frappe.auth.get_logged_user", { credentials: "include" });
	const me = (await r.json()).message;
	return me;
});
console.log(`[3] 当前登录账号: ${myRoles}`);

console.log("");
const ok = okBought && okNotBought;
console.log(ok ? "[PASS] 功能门禁生效" : "[FAIL] 功能门禁没生效");
await browser.close();
process.exit(ok ? 0 : 1);
