// 验证"文案走翻译包"这条规矩在**界面上**是真生效的，不是只改了代码。
//
// 用法:
//   node scripts/sample-verify-i18n-ui.mjs
//
// 验两件事（一正一反）：
//   1. 控制面（界面语言中文）的"功能目录"里，功能名显示**中文**：
//      `Pearnly Feature.feature_label` 现在存英文 msgid（"Intake Workbench"），
//      中文是翻译包给的。列表里看到中文 = msgid + 翻译包这条链是通的。
//   2. 同一页里**不该**出现那串英文原样 —— 出现就说明有一处没过 `__()`。
//
// 为什么单独有这一条：`Data` 字段框架不会自动翻译（只有 Link / Select /
// Autocomplete 会），所以"存英文 + 翻译包"必须在列表上补一次 `__()`
// （见 pearnly_feature_list.js）。这条断言就是盯住那次补充还在不在。
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const CONTROL = process.env.PEARNLY_CONTROL_URL || "http://control.localhost:8104";
const USER = process.env.PEARNLY_OPS_USER || "earn";
const PASS = process.env.PEARNLY_OPS_PASS || "earn";

// 挑一条同时有中英两种写法的：库里存英文，界面该显示中文
const MSGID = "Intake Workbench";
const EXPECT_ZH = "录入工作台";

const failures = [];
let seq = 0;

function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 }, locale: "zh-CN" });
const page = await ctx.newPage();

await page.goto(`${CONTROL}/login`, { waitUntil: "domcontentloaded" });
await page.fill("#login_email", USER);
await page.fill("#login_password", PASS);
await page.click(".btn-login");
await page.waitForTimeout(4000);

await page.goto(`${CONTROL}/desk/pearnly-feature`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3500);

const text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, " "));
check(`控制面功能目录显示中文「${EXPECT_ZH}」`, text.includes(EXPECT_ZH));
check(`控制面功能目录不再原样显示英文 msgid「${MSGID}」`, !text.includes(MSGID));

await page.screenshot({ path: `${SHOT_DIR}/i18n-control-feature-list.png`, fullPage: false });
console.log(`说明: 截图 ${SHOT_DIR}/i18n-control-feature-list.png`);

await ctx.close();
await browser.close();

if (failures.length) {
	console.log(`\n失败 ${failures.length} 项：\n  - ` + failures.join("\n  - "));
	process.exit(1);
}
console.log(`\n全部通过（${seq} 项）。`);
