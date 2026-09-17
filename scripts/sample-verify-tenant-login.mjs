// 验证"租户能用自己的账号登进自己的站点" —— 用真实浏览器走完整 OIDC 流程。
//
// 用法:
//   node scripts/sample-verify-tenant-login.mjs <域名> <端口> <用户名> <密码>
//   例: node scripts/sample-verify-tenant-login.mjs qc65956.localhost 8101 \
//         prov-65956@sample.local 'Tenant-Test-2026'
//
// 为什么用浏览器而不是 curl：登录 cookie（AUTH_SESSION_ID / KC_RESTART）带 `Secure`，
// 而本机是 http。浏览器把 `*.localhost` 当作安全上下文，会正常收发这些 cookie；
// curl 的"localhost 例外"范围更窄（只认 127.0.0.1 / localhost 字面量），
// 拿 `xxx.localhost` 走这条流程会因为不回传 cookie 而失败 —— 那是工具的问题，
// 不是系统的问题。所以这里用浏览器验，才是用户真实走的那条路。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const [host, port, user, pass] = process.argv.slice(2);
if (!host || !port || !user || !pass) {
	console.error("用法: node scripts/sample-verify-tenant-login.mjs <域名> <端口> <用户名> <密码>");
	process.exit(2);
}

const site = `http://${host}:${port}`;
const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));

await page.goto(`${site}/`, { waitUntil: "domcontentloaded" });
console.log("站点首页:", page.url());

// 与真人一致：站点发起 → 身份服务登录 → 提交 → 回站点
await page.goto(`${site}/api/method/pearnly_core.identity.begin_login`, {
	waitUntil: "domcontentloaded",
});
console.log("跳转到:", page.url().split("?")[0]);
if (!page.url().includes("keycloak")) {
	console.log("[FAIL] 没有跳到身份服务");
	await browser.close();
	process.exit(1);
}

await page.fill('input[name="username"], #username', user);
await page.fill('input[name="password"], #password', pass);
await Promise.all([
	page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {}),
	page.click('input[type="submit"], button[type="submit"], #kc-login'),
]);
await page.waitForTimeout(3000);
console.log("登录后地址:", page.url().split("?")[0]);

const me = await page.evaluate(async () => {
	const r = await fetch("/api/method/frappe.auth.get_logged_user", { credentials: "include" });
	return r.ok ? (await r.json()).message : `HTTP ${r.status}`;
});
console.log("会话里的当前用户:", me);
console.log("JS 报错:", errs.length ? errs : "无");

try {
	mkdirSync(SHOT_DIR, { recursive: true });
	await page.screenshot({ path: `${SHOT_DIR}/tenant-login.png` });
} catch {}

const loggedIn = String(me).toLowerCase() === user.toLowerCase();
const noWizard = !page.url().includes("setup-wizard");
const ok = loggedIn && noWizard && errs.length === 0;
console.log(
	ok
		? "[PASS] 租户用真实浏览器登录成功，且落点是 Desk（不是设置向导）"
		: `[FAIL] 登录=${loggedIn} 落点正常=${noWizard} 无JS报错=${errs.length === 0}`
);
await browser.close();
process.exit(ok ? 0 : 1);
