// 验租户侧首页（订阅与计费）：**充值申请 → 平台审核到账 → 余额变了 → 订阅套餐 → 扣月费**。
//
// 浏览器走租户真实那条路（点「充值」提交申请），平台审核那一下用控制面的接口
// （管理员在 Desk 上点「同意并到账」，这里调同一个后端方法，等价）。
//
// 用法: node scripts/sample-verify-tenant-home.mjs <域名> <端口> <登录名> <密码> [充值金额] <租户号>
//   node scripts/sample-verify-tenant-home.mjs one761900.localhost 8103 onestop761900 abc123 1000 T-2026-0116
//
// 会先把这家租户**重置成"新开出来"的样子**（余额归零、订阅清掉）——
// 否则第二次跑的前提就不成立（余额已经不是 0 了），失败原因跟被测的东西无关。
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
	(process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json"
);
const { chromium } = requireFrom("playwright");

const [host, port, user, pass, amountArg, tenant] = process.argv.slice(2);
if (!host || !port || !user || !pass || !tenant) {
	console.error("用法: node scripts/sample-verify-tenant-home.mjs <域名> <端口> <登录名> <密码> [金额] <租户号>");
	process.exit(2);
}
const TOPUP = Number(amountArg || 1000);
const SHOT_DIR = process.env.PEARLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const CONTROL = "http://127.0.0.1:8104";
const SITE = `http://${host}:${port}`;
const APP = `${SITE}/assets/pearnly_core/pearnly_app/index.html`.replace("pearnly_app", "pearnly-app");

const failures = [];
let seq = 0;
function check(label, ok, detail = "") {
	console.log(`${ok ? "PASS" : "FAIL"} [${++seq}] ${label}${detail ? " — " + detail : ""}`);
	if (!ok) failures.push(label + (detail ? " — " + detail : ""));
}

// 控制面（管理员会话）—— 用 curl 走，绕开 node fetch 不能自定 Host 的限制
const JAR = "/tmp/pearnly-home-jar.txt";
function ctl(method, body) {
	const args = ["-sS", "-b", JAR, "-H", "Host: control.localhost"];
	if (body) {
		args.push("-H", "Content-Type: application/json", "-X", "POST", "--data-binary", body);
	}
	args.push(`${CONTROL}/api/method/${method}`);
	return JSON.parse(execFileSync("curl", args, { encoding: "utf8" }) || "{}");
}

execFileSync("curl", [
	"-sS", "-c", JAR, "-o", "/dev/null", "-X", "POST", `${CONTROL}/api/method/login`,
	"-H", "Host: control.localhost", "-H", "Content-Type: application/json",
	"--data-binary", JSON.stringify({ usr: "earn", pwd: "earn" }),
]);

// ── 把这家租户摆回"刚开出来"的状态 ────────────────────────────────────────
function resetTenant() {
	// 余额归零（走人工调账，会留台账 —— 不用改库那套脏办法）
	const sum = ctl(`pearnly_control.billing_api.admin_summary?tenant=${tenant}`);
	const bal = Number(sum.message?.balance_thb || 0);
	if (Math.abs(bal) > 0.001) {
		ctl("pearnly_control.billing_api.admin_adjust",
			JSON.stringify({ tenant, amount_thb: -bal, note: "首页验证前置：余额归零" }));
	}
	// 订阅清掉（这一步只在测试脚本里做 —— 删订阅不是产品该提供的动作）
	execFileSync("docker", [
		"--context", "colima-pearnly", "exec", "pearnly-sample-control-bench-1", "bash", "-lc",
		`printf 'import frappe\\nfor n in frappe.get_all("Pearnly Subscription", filters={"tenant":"${tenant}"}, pluck="name"):\\n    frappe.delete_doc("Pearnly Subscription", n, force=1, ignore_permissions=True)\\nfrappe.db.commit()\\nprint("RESET-OK")\\n' | bench --site control.localhost console`,
	], { encoding: "utf8" });
}
resetTenant();

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
const errs = [];
page.on("pageerror", (e) => errs.push(String(e).slice(0, 160)));

// ── 登录租户站点 ────────────────────────────────────────────────────────────
await page.goto(`${SITE}/`, { waitUntil: "domcontentloaded" });
await page.waitForSelector("#kc-form-login", { timeout: 30000 });
await page.fill("#username", user);
await page.fill("#password", pass);
await page.click("#kc-login");
await page.waitForURL((u) => !u.href.includes("keycloak"), { timeout: 30000 });

const text = async () => ((await page.textContent("body")) || "").replace(/\s+/g, " ");
const bahtIn = (s, re) => {
	const m = s.match(re);
	return m ? m[1] : "";
};

async function openHome() {
	await page.goto(`${APP}#/home`, { waitUntil: "domcontentloaded" });
	await page.waitForSelector(".pn-card", { timeout: 20000 });
	// ⚠️ 必须显式 reload：hash 路由下 goto 到**同一个地址**浏览器不重新加载文档，
	// 页面会一直显示上一次取到的数据（我第一版就是这么读到旧余额的）。
	await page.reload({ waitUntil: "domcontentloaded" });
	await page.waitForSelector(".pn-card", { timeout: 20000 });
	await page.waitForTimeout(1800);
}
await openHome();
const first = await text();
check("首页是「订阅与计费」", first.includes("当前套餐") && first.includes("账户余额"));
check("新租户余额为 0", /账户余额฿0\.00/.test(first), bahtIn(first, /账户余额(฿[\d.]+)/));
check("未订阅", first.includes("未订阅"));
check("有套餐目录（S/M/L 三张卡）",
	["Package S", "Package M", "Package L"].every((x) => first.includes(x)));

// ── 点「充值」提交申请 ──────────────────────────────────────────────────────
await page.click("button:has-text('充值')");
await page.waitForSelector("[role='dialog'], .modal-container", { timeout: 10000 });
await page.waitForTimeout(500);
await page.fill("[role='dialog'] input[type='number'], .modal-container input[type='number']", String(TOPUP));
await page.click("button:has-text('提交申请')");
await page.waitForTimeout(2500);
const afterReq = await text();
check("提交后有回执（等平台确认）", /充值申请已提交/.test(afterReq), afterReq.slice(0, 120));
check("余额**没有**立刻变（只落申请）", /账户余额฿0\.00/.test(afterReq));

// ── 平台审核到账 ────────────────────────────────────────────────────────────
const pend = ctl("pearnly_control.billing_api.admin_pending_topups");
const items = pend.message?.items || [];
// ⚠️ 审核台的列表是**最早的在前**（审核队列就该这样排队）。刚提交的那笔在最后 ——
// 拿 items[0] 会审到别人家的申请（我第一版就是这么错的，还把另一家租户充了 ฿1000）。
const mine = items.filter((i) => Number(i.requested_thb) === TOPUP);
const target = mine[mine.length - 1];
check(`审核台看得到这笔申请（฿${TOPUP}）`, !!target,
	JSON.stringify(items.map((i) => `${i.name}:${i.tenant}:${i.requested_thb}`)));
const reqName = target?.name;
const appr = ctl("pearnly_control.billing_api.admin_approve",
	JSON.stringify({ request: reqName, actual_amount_thb: TOPUP, note: "首页验证到账" }));
check("审核通过并到账", !!appr.message?.ok, JSON.stringify(appr.message || appr).slice(0, 160));

// ── 回首页看余额 ────────────────────────────────────────────────────────────
await openHome();
const afterTopup = await text();
check(`余额变成 ฿${TOPUP}.00`, new RegExp(`账户余额฿${TOPUP}\\.00`).test(afterTopup),
	bahtIn(afterTopup, /账户余额(฿[\d.]+)/));
check("记录明细里有这一笔充值", /充值/.test(afterTopup) && afterTopup.includes(`+฿${TOPUP}.00`));

// ── 订阅套餐 S（฿150）──────────────────────────────────────────────────────
await page.click("button:has-text('订阅此套餐')");
await page.waitForTimeout(3000);
const afterSub = await text();
check("订阅后当前套餐变成 S", /当前套餐\s*套餐 S/.test(afterSub.replace(/\s+/g, " ")) || afterSub.includes("套餐 S"),
	afterSub.slice(0, 150));
const expect = TOPUP - 150;
check(`月费从余额扣掉了（应为 ฿${expect}.00）`,
	new RegExp(`账户余额฿${expect}\\.00`).test(afterSub),
	bahtIn(afterSub, /账户余额(฿[\d.]+)/));
check("本周期额度显示出来了", /100 张/.test(afterSub));
check("记录了订阅这笔扣费", afterSub.includes("订阅套餐 S"));

await page.screenshot({ path: `${SHOT_DIR}/tenant-home-billing.png` });

const finalErrs = errs.filter((e) => !/ResizeObserver/.test(e));
check("没有 JS 报错", finalErrs.length === 0, finalErrs.slice(0, 2).join(" | "));

await browser.close();
console.log("");
if (failures.length) {
	console.log(`结果：${seq - failures.length} / ${seq} 通过，${failures.length} 项失败`);
	failures.forEach((f) => console.log("  ✗ " + f));
	process.exit(1);
}
console.log(`结果：${seq} / ${seq} 全过`);
