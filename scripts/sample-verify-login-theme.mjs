// 验证"统一登录页"的实际渲染与完整登录 —— 用真实浏览器打开，不看代码猜。
//
// 用法:
//   node scripts/sample-verify-login-theme.mjs [租户域名] [端口] [账号] [密码]
//   默认: qc65956.localhost 8101 prov-65956@sample.local Tenant-Test-2026
//
// 为什么从业务站点进、而不是直接拼身份服务的授权地址：用户真实走的是这条路，
// 顺便把"访客被送到统一登录页"这条一起验了。
//
// 这一版跟上一版的不同（2026-09-18 用户纠偏）：登录页的正确形态是
// 「一整张背景图 + 按坐标叠真实控件 + 右侧不透明磨砂卡」，不是用 CSS 重画。
// 所以这里检查的是**复用有没有到位**：底图加载了吗、卡的位置跟线上一致吗、
// 语言短标签对不对、注册/第三方登录真的没有吗。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const [host, port, user, pass] = [
	process.argv[2] || "qc65956.localhost",
	process.argv[3] || "8101",
	process.argv[4] || "prov-65956@sample.local",
	process.argv[5] || "Tenant-Test-2026",
];

// 线上那套（pearnly.com/cowork）量出来的卡片几何：位置、大小必须一致，
// 否则说明我们没在复用原设计，而是又画了一遍。
const EXPECTED_CARD = { x: 744, w: 616, h: 914 };
const EXPECTED_STAGE = { w: 1402, h: 1122 };
const EXPECTED_LOCALES = ["中", "TH", "EN", "日"];
const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const site = `http://${host}:${port}`;
const failures = [];
const notes = [];

function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

function watch(page) {
	const errs = [];
	page.on("pageerror", (e) => errs.push("pageerror: " + String(e).slice(0, 200)));
	page.on("console", (m) => {
		if (m.type() === "error") errs.push("console: " + m.text().slice(0, 200));
	});
	page.on("response", (r) => {
		if (r.status() >= 400) errs.push(`http ${r.status()}: ${r.url().split("?")[0]}`);
	});
	return errs;
}

const browser = await chromium.launch({ headless: true });

// ── 桌面：打开租户站点，应落到统一登录页 ───────────────────────────────
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
const errs = watch(page);

await page.goto(`${site}/`, { waitUntil: "domcontentloaded" });
await page.waitForURL(/keycloak/, { timeout: 25000 });
await page.waitForSelector("#kc-form-login", { timeout: 25000 });
await page.waitForTimeout(600);

check("[1] 访客打开站点根 → 落到统一登录页", page.url().includes("keycloak"));

const dom = await page.evaluate(() => {
	const r = (s) => {
		const e = document.querySelector(s);
		if (!e) return null;
		const b = e.getBoundingClientRect();
		return { x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height) };
	};
	const img = document.querySelector(".auth-shell");
	const bg = img ? getComputedStyle(img).backgroundImage : "";
	return {
		stage: r(".auth-stage"),
		shell: r(".auth-shell"),
		card: r(".auth-card"),
		bg,
		bgUrl: (bg.match(/url\("([^"]+)"\)/) || [])[1] || "",
		greeting: (document.querySelector(".welcome-copy h1") || {}).textContent || "",
		bubble: (document.querySelector(".work-bubble p") || {}).textContent || "",
		tasks: [...document.querySelectorAll(".check-row span:last-child")].map((e) => e.textContent.trim()),
		quoteLabel: (document.querySelector(".quote-card header span:last-child") || {}).textContent || "",
		title: (document.querySelector("#auth-title") || {}).textContent || "",
		subtitle: (document.querySelector("#auth-subtitle") || {}).textContent || "",
		labels: [...document.querySelectorAll(".language-switcher a")].map((a) => a.textContent.trim()),
		labelTitles: [...document.querySelectorAll(".language-switcher a")].map((a) => a.getAttribute("title") || ""),
		fields: [...document.querySelectorAll(".field input")].map((i) => `${i.id}:${i.name}`),
		hasRemember: !!document.querySelector("#rememberMe"),
		hasForgot: !!document.querySelector('a[href*="reset-credentials"]'),
		hasSignup: !!document.querySelector('[data-mode="signup"], .mode-btn, #kc-registration'),
		hasSso: !!document.querySelector(".sso-row, .outline-btn, [data-sso]"),
		supportVisible: !!document.querySelector(".support-card"),
		securityVisible: !!document.querySelector(".security-footer"),
	};
});

check(`[2] 舞台尺寸 ${dom.stage.w}×${dom.stage.h}`, dom.stage.w === EXPECTED_STAGE.w && dom.stage.h === EXPECTED_STAGE.h,
	`期望 ${EXPECTED_STAGE.w}×${EXPECTED_STAGE.h}`);
check(`[3] 登录卡位置与线上一致（相对舞台 x=${dom.card.x - dom.stage.x}, ${dom.card.w}×${dom.card.h}）`,
	dom.card.x - dom.stage.x === EXPECTED_CARD.x && dom.card.w === EXPECTED_CARD.w && dom.card.h === EXPECTED_CARD.h,
	`期望 x=${EXPECTED_CARD.x} ${EXPECTED_CARD.w}×${EXPECTED_CARD.h}`);
check("[4] 底图走压缩版 WebP", dom.bgUrl.endsWith("auth-bg-desktop.webp"), dom.bgUrl);

const bgLoaded = await page.evaluate(async (u) => {
	const res = await fetch(u, { cache: "force-cache" });
	const blob = await res.blob();
	const img = await new Promise((resolve) => {
		const el = new Image();
		el.onload = () => resolve(el);
		el.onerror = () => resolve(null);
		el.src = u;
	});
	return { bytes: blob.size, w: img ? img.naturalWidth : 0, h: img ? img.naturalHeight : 0 };
}, dom.bgUrl);
check(`[5] 底图能取到且尺寸正确（${bgLoaded.w}×${bgLoaded.h}, ${Math.round(bgLoaded.bytes / 1024)} KB）`,
	bgLoaded.w === 4096 && bgLoaded.h === 3291);
check(`[6] 底图体积远小于旧版 5.6 MB（当前 ${(bgLoaded.bytes / 1024 / 1024).toFixed(2)} MB）`,
	bgLoaded.bytes < 1024 * 1024);

check("[7] 左侧问候语叠在图上", dom.greeting.trim().length > 0, dom.greeting.trim());
check("[8] 气泡文案在", dom.bubble.replace(/\s+/g, "").length > 0, dom.bubble.replace(/\s+/g, " ").trim());
check(`[9] 清单四条：${dom.tasks.join(" / ")}`, dom.tasks.length === 4);
check("[10] 引言卡文案在", dom.quoteLabel.trim().length > 0, dom.quoteLabel.trim());
check(`[11] 标题/副标题：${dom.title.trim()}`, dom.title.trim().length > 0 && dom.subtitle.trim().length > 0);
check(`[12] 语言是大写的四个短标签 ${JSON.stringify(dom.labels)}`,
	JSON.stringify(dom.labels) === JSON.stringify(EXPECTED_LOCALES),
	`期望 ${JSON.stringify(EXPECTED_LOCALES)}`);
check("[13] 每个语言标签带完整名字（悬停可见）", dom.labelTitles.every((t) => t.length > 0));
check(`[14] 表单字段 ${JSON.stringify(dom.fields)}`,
	JSON.stringify(dom.fields) === JSON.stringify(["username:username", "password:password"]));
check("[15] 有「记住登录状态」", dom.hasRemember);
check("[16] 有「忘记密码」并指向身份服务的重置流程", dom.hasForgot);
check("[17] 没有注册入口", !dom.hasSignup);
check("[18] 没有第三方登录", !dom.hasSso);
check("[19] 帮助卡在（旧设计里就有）", dom.supportVisible);

await page.screenshot({ path: `${SHOT_DIR}/login-theme-desktop.png` });

// ── 语言切换 ────────────────────────────────────────────────────────────
const enLink = await page.$('.language-switcher a[title*="English"]');
await enLink.click();
await page.waitForSelector("#auth-title", { timeout: 25000 });
const enTitle = (await page.$eval("#auth-title", (e) => e.textContent)).trim();
check(`[20] 切到英文，标题变英文（${enTitle}）`, /welcome back/i.test(enTitle));
const thLink = await page.$('.language-switcher a[title*="ไทย"]');
await thLink.click();
await page.waitForSelector("#auth-title", { timeout: 25000 });
const thTitle = (await page.$eval("#auth-title", (e) => e.textContent)).trim();
check(`[21] 切到泰文，标题变泰文（${thTitle}）`, /ยินดีต้อนรับ/.test(thTitle));
await page.click('.language-switcher a[title*="中文"]');
await page.waitForSelector("#auth-title", { timeout: 25000 });
const zhTitle = (await page.$eval("#auth-title", (e) => e.textContent)).trim();
check(`[22] 切回中文（${zhTitle}）`, /欢迎回来/.test(zhTitle));

// ── 密码错误要有提示，且留在登录页 ───────────────────────────────────────
await page.fill("#username", user);
await page.fill("#password", "definitely-wrong-password");
await page.click("#kc-login");
await page.waitForTimeout(2500);
const badState = await page.evaluate(() => ({
	onLoginPage: !!document.querySelector("#kc-form-login"),
	errText: [...document.querySelectorAll(".auth-message.error, .pn-field-error, #input-error")]
		.map((e) => e.textContent.trim())
		.join(" | "),
}));
check(`[23] 密码错误留在登录页并给出提示（${badState.errText.slice(0, 60)}）`,
	badState.onLoginPage && badState.errText.length > 0);
await page.screenshot({ path: `${SHOT_DIR}/login-theme-error.png` });

// 到这一步为止的报错都算"登录页自己的"。再往下是租户站点里的 Desk，
// 那边的 socket.io / 图标 404 属于租户应用，不是登录主题的事，分开算。
const loginPhaseErrs = [...errs];

// ── 正确密码：真的登进去 ────────────────────────────────────────────────
await page.fill("#username", user);
await page.fill("#password", pass);
await page.click("#kc-login");
await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });
await page.waitForLoadState("domcontentloaded");
await page.waitForTimeout(2500);
check(`[24] 登录成功并落回租户站点（${page.url().split("?")[0]}）`,
	page.url().includes(host) && !page.url().includes("keycloak"));
notes.push(`登录后落在 ${page.url().split("?")[0]}`);

check("[25] 登录页阶段没有报错、没有 404", loginPhaseErrs.length === 0, loginPhaseErrs.join(" | "));
const afterLoginErrs = errs.slice(loginPhaseErrs.length);
if (afterLoginErrs.length) {
	notes.push(`登录后租户站点自身的控制台消息（不属登录主题）：${[...new Set(afterLoginErrs)].join(" | ").slice(0, 220)}`);
}
notes.push(`桌面截图 ${SHOT_DIR}/login-theme-desktop.png`);
notes.push(`错误态截图 ${SHOT_DIR}/login-theme-error.png`);
await ctx.close();

// ── 忘记密码页：同一套外壳 ──────────────────────────────────────────────
const fctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const fp = await fctx.newPage();
const ferrs = watch(fp);
await fp.goto(`${site}/`, { waitUntil: "domcontentloaded" });
await fp.waitForSelector('a[href*="reset-credentials"]', { timeout: 25000 });
await fp.click('a[href*="reset-credentials"]');
await fp.waitForSelector("#kc-reset-password-form", { timeout: 25000 });
await fp.waitForTimeout(600);
const forgot = await fp.evaluate(() => {
	const card = document.querySelector(".auth-card");
	const b = card.getBoundingClientRect();
	return {
		hasShell: !!document.querySelector(".auth-shell .brand-panel"),
		card: { w: Math.round(b.width), h: Math.round(b.height) },
		title: (document.querySelector("#auth-title") || {}).textContent || "",
		submit: (document.querySelector(".primary-btn") || {}).textContent || "",
		styledField: !!document.querySelector(".field input#username"),
	};
});
check(`[26] 忘记密码页套同一套外壳（标题：${forgot.title.trim()}）`, forgot.hasShell && forgot.styledField);
check(`[27] 忘记密码页的按钮是设计里的主按钮（${forgot.submit.trim()}）`, forgot.submit.trim().length > 0);
await fp.screenshot({ path: `${SHOT_DIR}/login-theme-forgot.png` });
check("[28] 忘记密码页没有 JS 报错", ferrs.length === 0, ferrs.join(" | "));
notes.push(`忘记密码页截图 ${SHOT_DIR}/login-theme-forgot.png`);
await fctx.close();

// ── 手机 ────────────────────────────────────────────────────────────────
const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
const mp = await mctx.newPage();
const merrs = watch(mp);
await mp.goto(`${site}/`, { waitUntil: "domcontentloaded" });
await mp.waitForSelector(".auth-stage", { timeout: 25000 });
await mp.waitForTimeout(1200);
const mOverflow = await mp.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
check(`[29] 手机端不横向溢出（多出 ${mOverflow}px）`, mOverflow <= 1);
await mp.screenshot({ path: `${SHOT_DIR}/login-theme-mobile.png`, fullPage: true });
await mp.click("#m-login-cta");
await mp.waitForTimeout(700);
check("[30] 手机端底部按钮能拉起登录卡", await mp.isVisible("#kc-login"));
await mp.screenshot({ path: `${SHOT_DIR}/login-theme-mobile-sheet.png` });
check("[31] 手机端没有 JS 报错", merrs.length === 0, merrs.join(" | "));
notes.push(`手机截图 ${SHOT_DIR}/login-theme-mobile.png`);
await mctx.close();

// ── 默认语言与"当前语言"高亮 ─────────────────────────────────────────────
//
// 两件事都在这里翻过车，所以单独立一组：
//   1. 默认语言必须是泰语（用户 2026-09-18 定："新租户默认泰语"）；
//   2. 语言切换上的"当前选中"必须真的高亮 —— 原来用 l.languageTag == locale.current
//      去比，两边类型不同恒为 false，四个标签全是空 class，**表面上完全看不出来**。
// 浏览器语言匹配优先于默认值（Keycloak 的行为）：泰/中/日/英浏览器各看各的，
// 匹配不上才回落到默认的泰语。这四条一起验，少一条都可能掩盖问题。
const LOCALE_CASES = [
	{ browser: "th-TH", expectLang: "th", expectActive: "TH", why: "泰语浏览器" },
	{ browser: "zh-CN", expectLang: "zh-CN", expectActive: "中", why: "中文浏览器" },
	{ browser: "ja-JP", expectLang: "ja", expectActive: "日", why: "日语浏览器" },
	{ browser: "en-US", expectLang: "en", expectActive: "EN", why: "英语浏览器" },
	{ browser: "fr-FR", expectLang: "th", expectActive: "TH", why: "匹配不上的浏览器回落到默认" },
];
let localeIdx = 32;
for (const c of LOCALE_CASES) {
	const lctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: c.browser });
	const lp = await lctx.newPage();
	await lp.goto(`${site}/`, { waitUntil: "domcontentloaded" });
	await lp.waitForSelector(".language-switcher a", { timeout: 25000 });
	await lp.waitForTimeout(400);
	const got = await lp.evaluate(() => ({
		lang: document.documentElement.lang,
		active: (document.querySelector(".language-switcher a.active") || {}).textContent?.trim() || "",
	}));
	check(`[${localeIdx++}] ${c.why}（${c.browser}）→ ${got.lang}，高亮 ${got.active || "无"}`,
		got.lang === c.expectLang && got.active === c.expectActive,
		`期望 lang=${c.expectLang} 高亮=${c.expectActive}`);
	await lctx.close();
}

await browser.close();

console.log("");
notes.forEach((n) => console.log("说明: " + n));
if (failures.length) {
	console.log(`\n失败 ${failures.length} 项:`);
	failures.forEach((f) => console.log(" - " + f));
	process.exit(1);
}
console.log("\n全部通过。");
