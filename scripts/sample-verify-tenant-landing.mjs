// 验证"租户登录后直接进工作台（带左侧导航），不要先过一屏桌面"。
//
// 用法:
//   node scripts/sample-verify-tenant-landing.mjs
//   可用环境变量覆盖默认的两个租户：
//     PEARNLY_LANDING_SITES='[["cowork","qc65956.localhost","8101","录入工作台"], ...]'
//
// 背景（用户 2026-09-18 的要求）："我不要这个桌面，直接进入工作台，左侧导航"。
// 落地靠三处：User.default_workspace（首页规则会返回工作台地址）、
// 登录回调按首页规则算落点（原来写死 /desk）、根路径对已登录用户重定向。
// 三处缺一个都会表现成"还是先看到桌面"，所以这里逐条验。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const USER = process.env.PEARNLY_LANDING_USER || "prov-65956@sample.local";
const PASS = process.env.PEARNLY_LANDING_PASS || "Tenant-Test-2026";

// [标签, 域名, 端口, 该租户买到的功能在工作台上应该出现的字样, 截图名]
const SITES = JSON.parse(
	process.env.PEARNLY_LANDING_SITES ||
		JSON.stringify([
			["cowork 租户", "qc65956.localhost", "8101", "录入工作台", "tenant-landing-cowork.png"],
			["dms 租户", "qd65956.localhost", "8103", "身份证录入", "tenant-landing-dms.png"],
		])
);

const failures = [];
const notes = [];
let seq = 0;

function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });

for (const [label, host, port, expectCard, shot] of SITES) {
	const site = `http://${host}:${port}`;
	const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, locale: "zh-CN" });
	const page = await ctx.newPage();
	const errs = [];
	page.on("pageerror", (e) => errs.push("pageerror: " + String(e).slice(0, 160)));
	page.on("console", (m) => {
		if (m.type() !== "error") return;
		// 两种已知的本地样例噪音都与租户落点无关（见文档"边界"一节）：
		//   - "Failed to load resource" 不带 URL，判不了是不是下面的 socket.io；
		//   - socketio 端口没映射到宿主机，控制台一直报连接失败。
		// 其它控制台错误一律算问题。
		if (/Failed to load resource/.test(m.text())) return;
		if (/socket\.io/i.test(m.text())) return;
		errs.push("console: " + m.text().slice(0, 160));
	});
	// 已知的本地样例问题：socketio 端口没映射到宿主机（与租户落点无关，见
	// docs/p4/11-tenant-landing.md 的边界一节）。除此之外的失败请求都算问题。
	const KNOWN_DOWN = /\/socket\.io\//;
	page.on("response", (r) => {
		if (r.status() >= 400 && !KNOWN_DOWN.test(r.url())) {
			errs.push(`http ${r.status()}: ${r.url().split("?")[0]}`);
		}
	});

	// [入口] 访客打开站点根 → 统一登录页（不是 Frappe 自带登录页）
	await page.goto(`${site}/`, { waitUntil: "domcontentloaded" });
	await page.waitForURL(/keycloak/, { timeout: 25000 });
	check(`${label}：访客打开站点根 → 统一登录页`, page.url().includes("keycloak"));

	// [登录] 走完真实登录
	await page.waitForSelector("#kc-form-login", { timeout: 25000 });
	await page.fill("#username", USER);
	await page.fill("#password", PASS);
	await page.click("#kc-login");
	await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });
	await page.waitForLoadState("domcontentloaded");
	await page.waitForTimeout(3500);

	const landed = await page.evaluate(() => {
		const bottom = document.querySelector(".body-sidebar-bottom");
		return {
			path: decodeURIComponent(location.pathname),
			hasSidebar: !!document.querySelector(".body-sidebar"),
			// 左侧导航里除搜索/通知之外的条目（工作区入口就在这儿）
			navItems: [...document.querySelectorAll(".body-sidebar .standard-sidebar-item")]
				.filter((e) => (e.offsetWidth || e.offsetHeight) && !e.closest(".edit-mode"))
				.map((e) => e.textContent.trim().replace(/\s+/g, " "))
				.filter(Boolean),
			bodyText: document.body.innerText.replace(/\s+/g, " "),
			hasUserMenu: !!bottom,
		};
	});

	// [落点] 登录后必须落在 /desk/<工作区>，不是 /desk 那个桌面
	check(`${label}：登录后落在工作台（${landed.path}）`, /^\/desk\/.+/.test(landed.path),
		`实际 ${landed.path}`);
	// [导航] 左侧导航在，且里面有工作区条目
	check(`${label}：左侧导航在且有条目（${landed.navItems.join(" / ") || "空"}）`,
		landed.hasSidebar && landed.navItems.length > 0);
	check(`${label}：左下角有账号菜单`, landed.hasUserMenu);
	// [内容] 工作台画出了已购功能的卡片，不是空态
	check(`${label}：工作台里出现了「${expectCard}」`,
		landed.bodyText.includes(expectCard),
		"只看到欢迎语说明 workspace 的 content 是空的");
	check(`${label}：没有落到"欢迎来到…"的空工作台`,
		!/Welcome to the .* workspace/i.test(landed.bodyText));

	await page.screenshot({ path: `${SHOT_DIR}/${shot}` });
	notes.push(`${label} 截图 ${SHOT_DIR}/${shot}`);

	// [根路径] 已经登录的人再打开裸域名，也应该回工作台（书签/重开浏览器都会这样）
	await page.goto(`${site}/`, { waitUntil: "domcontentloaded" });
	await page.waitForTimeout(3000);
	const rootPath = decodeURIComponent(new URL(page.url()).pathname);
	check(`${label}：已登录打开裸域名 → 仍回工作台（${rootPath}）`, /^\/desk\/.+/.test(rootPath));

	// [不误伤] 显式访问 /desk 仍然可用（不能拦，否则工具栏的入口像坏了）
	await page.goto(`${site}/desk`, { waitUntil: "domcontentloaded" });
	await page.waitForTimeout(2500);
	check(`${label}：显式打开 /desk 仍可用（不被重定向走）`,
		decodeURIComponent(new URL(page.url()).pathname).replace(/\/$/, "") === "/desk");

	check(`${label}：全程没有 JS 报错`, errs.length === 0, errs.join(" | "));
	await ctx.close();
}

// [不越界] 控制面不在统一身份里，也不该被这条改动影响
{
	const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
	const page = await ctx.newPage();
	await page.goto("http://control.localhost:8104/", { waitUntil: "domcontentloaded" });
	await page.waitForTimeout(2500);
	check(`控制面不受影响：访客没有被送去统一登录页（${page.url().split("?")[0]}）`,
		!page.url().includes("keycloak"));
	await ctx.close();
}

await browser.close();

console.log("");
notes.forEach((n) => console.log("说明: " + n));
if (failures.length) {
	console.log(`\n失败 ${failures.length} 项:`);
	failures.forEach((f) => console.log(" - " + f));
	process.exit(1);
}
console.log(`\n全部通过（${seq} 项）。`);
