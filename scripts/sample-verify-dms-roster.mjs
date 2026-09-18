// 验证 DMS「操作员」花名册（老产品里这一页仅老板可见，含每行「发绑定码」）。
//
// 同时验**导航形态**对不对。老产品 DMS 只有四项：
//   身份证录入 / 推送记录 / 操作员 / 套餐与余额
// 本产品砍掉「身份证录入」（业务在 LINE 上做），所以侧栏应当是：
//   首页（套餐与余额并过来） / 推送记录 / 操作员
// 而且**不该有**「连接第三方 DMS」「LINE 绑定码」——
// 那两个是之前自己编出来的页面（老产品里前者是录入页里的一张卡，
// 后者是操作员行内的按钮），留着一个都算没改干净。
//
// 「推送记录」的后端（推送日志）新平台还没有，所以那一页必须是**诚实占位**，
// 不能拿别的数据硬凑 —— 这一条也在验。
//
// 用法: node scripts/sample-verify-dms-roster.mjs
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

const EXPECTED_NAV = ["首页", "推送记录", "操作员"];
const FORBIDDEN_NAV = ["连接第三方 DMS", "LINE 绑定码", "身份证录入"];

// 绑定码的字母表（后端 line_binding.CODE_ALPHABET 去掉了 0/O、1/I/l）
const CODE_RE = /^[ACDEFGHJKLMNPQRSTUVWXY23456789]{6}$/;

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, locale: "zh-CN" });
const page = await ctx.newPage();

// 模态框文字：取最后一个可见的对话框，避免读到页面正文
async function modalText() {
	return page.evaluate(() => {
		const list = [...document.querySelectorAll("[role='dialog'], .modal-container")];
		const last = list[list.length - 1];
		return ((last?.textContent || "") + "").replace(/\s+/g, " ");
	});
}
// 弹窗里那个大字号码
async function modalCode() {
	return page.evaluate(() => {
		const list = [...document.querySelectorAll("[role='dialog'], .modal-container")];
		const last = list[list.length - 1];
		if (!last) return "";
		const el = [...last.querySelectorAll("div")].find((d) =>
			/^[A-Z0-9]{6}$/.test((d.textContent || "").trim()),
		);
		return el ? el.textContent.trim() : "";
	});
}
async function clickModal(label) {
	await page
		.locator("[role='dialog'], .modal-container")
		.last()
		.locator(`button:has-text('${label}')`)
		.click();
}

await page.goto(`http://${SITE.host}:${SITE.port}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("#kc-form-login", { timeout: 30000 });
await page.fill("#username", SITE.user);
await page.fill("#password", PASS);
await page.click("#kc-login");
await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });

// ── 1. 侧栏形态 ────────────────────────────────────────────────────────────
await page.goto(`${APP}#/home`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".bg-surface-menu-bar", { timeout: 20000 });
const sidebar = ((await page.textContent(".bg-surface-menu-bar")) || "").replace(/\s+/g, " ");
console.log("  侧栏：", sidebar.slice(0, 140));
for (const label of EXPECTED_NAV) check(`侧栏有「${label}」`, sidebar.includes(label));
for (const label of FORBIDDEN_NAV) check(`侧栏没有「${label}」`, !sidebar.includes(label));

// ── 2. 操作员页：新增 → 发绑定码 → 编辑 → 停用/启用 → 删除 ─────────────────
await page.goto(`${APP}#/roster`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".pn-card", { timeout: 20000 });
await page.waitForTimeout(1500);

const NAME = `验证操作员${Date.now() % 100000}`;
await page.click("button:has-text('新增操作员')");
await page.waitForTimeout(500);
await page.fill("input[placeholder='如：阿明（销售）']", NAME);
await page.fill("input[placeholder='该操作员的 DMS 账号']", "verify-dms-user");
await page.fill("input[placeholder='该操作员的 DMS 密码']", "verify-dms-pass");
// 表单里那个"新增操作员"按钮是最后一个同名按钮（第一个是展开用的）
await page.locator("button:has-text('新增操作员')").last().click();
await page.waitForTimeout(2500);

const afterCreate = ((await page.textContent("body")) || "").replace(/\s+/g, " ");
check("新增后有「操作员已新增」回执", afterCreate.includes("操作员已新增"), afterCreate.slice(0, 90));
check(`列表里出现了新操作员「${NAME}」`, afterCreate.includes(NAME));

const rowText = ((await page.locator(".pn-table tbody tr", { hasText: NAME }).textContent()) || "").replace(
	/\s+/g,
	" ",
);
check(`新行默认「未绑」+「启用」（实际 ${rowText.slice(0, 60)}）`, rowText.includes("未绑") && rowText.includes("启用"));
check("新行角色默认「销售」", rowText.includes("销售"));
check("新行查询权限默认「未允许」", rowText.includes("未允许"));

// 发绑定码：码由后端生成，点「重新生成」会换一个（证明不是前端写死的）
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('发绑定码')").click();
await page.waitForTimeout(1800);
const codeText = await modalText();
check("绑定码弹窗出现，两步话术齐", codeText.includes("添加 Pearnly DMS 好友") && codeText.includes("发送此绑定码"));
const code1 = await modalCode();
check(`弹窗里的码是 6 位且不含易混字符（${code1 || "没拿到"}）`, CODE_RE.test(code1));
check("弹窗显示了剩余有效期或过期提示", /剩余有效期|已过期/.test(codeText));
// 回归：刚生成的码**必须**显示剩余有效期，不能显示"已过期"。
// 这条曾经真的坏过 —— 倒计时去解析站点时区的裸时间字符串，站点时区 IST、
// 浏览器 +07，算出来立刻过期。现在后端给的是剩余秒数。
check(
	`刚生成的码没有显示「已过期」（${(codeText.match(/剩余有效期[^ ]*|已过期/) || [""])[0]}）`,
	!codeText.includes("已过期"),
);
await page.screenshot({ path: `${SHOT_DIR}/frontend-dms-roster-code.png` });

await clickModal("重新生成");
await page.waitForTimeout(1800);
const code2 = await modalCode();
check(`点「重新生成」换了一个新码（${code1} → ${code2}）`, CODE_RE.test(code2) && code2 !== code1);
await clickModal("关闭");
await page.waitForTimeout(500);

// 编辑：两个框都留空 = 不修改（老产品原话）
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('编辑')").click();
await page.waitForTimeout(600);
await clickModal("保存");
await page.waitForTimeout(1200);
check("编辑框留空时提示「没有要保存的改动」", (await modalText()).includes("没有要保存的改动"));
await clickModal("取消");
await page.waitForTimeout(500);

// 停用 → 确认文案照老产品
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('停用')").click();
await page.waitForTimeout(700);
const disableText = await modalText();
check("停用确认文案说明会解除 LINE 绑定", disableText.includes("解除 LINE 绑定"), disableText.slice(0, 110));
await clickModal("确定");
await page.waitForTimeout(2500);
const afterDisable = (
	(await page.locator(".pn-table tbody tr", { hasText: NAME }).textContent()) || ""
).replace(/\s+/g, " ");
check(`停用后这一行变「停用」`, afterDisable.includes("停用"), afterDisable.slice(0, 70));

// 启用 → 确认文案照老产品
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('启用')").click();
await page.waitForTimeout(700);
const enableText = await modalText();
check("启用确认文案说明需重新发绑定码", enableText.includes("重新发绑定码"), enableText.slice(0, 110));
await clickModal("确定");
await page.waitForTimeout(2500);
const afterEnable = (
	(await page.locator(".pn-table tbody tr", { hasText: NAME }).textContent()) || ""
).replace(/\s+/g, " ");
check("启用后又回到「启用」", afterEnable.includes("启用"));

// 停用的操作员不该能发码（老产品里停用后推送停、绑定解除）
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('停用')").click();
await page.waitForTimeout(700);
await clickModal("确定");
await page.waitForTimeout(2000);
const disabledCodeBtn = page
	.locator(".pn-table tbody tr", { hasText: NAME })
	.locator("button:has-text('发绑定码')");
check("停用后「发绑定码」按钮不可点", await disabledCodeBtn.isDisabled());
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('启用')").click();
await page.waitForTimeout(700);
await clickModal("确定");
await page.waitForTimeout(2000);
await page.screenshot({ path: `${SHOT_DIR}/frontend-dms-roster.png` });

// 删除：清掉这次造的测试数据
await page.locator(".pn-table tbody tr", { hasText: NAME }).locator("button:has-text('删除')").click();
await page.waitForTimeout(700);
const delText = await modalText();
check("删除确认文案说明推送记录会保留", delText.includes("推送记录将保留"), delText.slice(0, 110));
await clickModal("确定");
await page.waitForTimeout(2500);
check("删除后列表里没有它了", !((await page.textContent("body")) || "").includes(NAME));

// ── 3. 推送记录：后端还没做，页面必须诚实占位 ──────────────────────────────
await page.goto(`${APP}#/records`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
const recordsText = ((await page.textContent("body")) || "").replace(/\s+/g, " ");
check("推送记录页如实显示「这一页还没做」", recordsText.includes("这一页还没做"), recordsText.slice(0, 100));

await browser.close();

console.log("");
if (failures.length) {
	console.log(`失败 ${failures.length} 项:`);
	failures.forEach((f) => console.log(" - " + f));
	process.exit(1);
}
console.log(`全部通过（${seq} 项）。`);
