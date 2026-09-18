// 验「删除租户」**按钮本身**：在 Desk 上真点一下，看确认框、提示与状态。
//
// 后台链路（排步骤 → 拆站点 → 删账号与记录）由 `sample-verify-tenant-teardown.sh` 验；
// 这一层只管"人点得到、看得懂、看得见结果"。
//
// 用法: node scripts/sample-verify-tenant-delete-ui.mjs <租户名> <站点域名>
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
  (process.env.PEARnly_PLAYWRIGHT_BASE ||
    process.env.PEARNLY_PLAYWRIGHT_BASE ||
    "/Users/skin/Developer/Pearnly/pearnly-app/") + "package.json",
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

const TENANT = process.argv[2] || "";
const SITE = process.argv[3] || "";
const BASE = "http://control.localhost:8104";

const results = [];
function check(label, ok, detail = "") {
  results.push([label, !!ok]);
  console.log(`${ok ? "PASS" : "FAIL"} ${label}${detail ? " — " + detail : ""}`);
}

async function main() {
  if (!TENANT) {
    console.error("用法: node sample-verify-tenant-delete-ui.mjs <租户名> <站点域名>");
    process.exit(2);
  }
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();

  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.fill("#login_email", "earn");
  await page.fill("#login_password", "earn");
  await page.click("button[type=submit]");
  await page.waitForTimeout(3500);

  await page.goto(`${BASE}/desk/pearnly-tenant/${TENANT}`, { waitUntil: "networkidle" });
  // 按钮有两种文案：没删过是 "Delete tenant"，已经在删（deleting）是 "Retry deletion"。
  // 两个都认 —— 这一步验的是"点得到、看得懂"，不是文案本身。
  const button = page.getByText(/Delete tenant|Retry deletion/).first();
  const ready = await button
    .waitFor({ timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  if (!ready) {
    // 表单没出来时把"页面自己说的话"带出来：是 404（租户已经没了）还是 JS 没加载
    const body = await page.locator("body").innerText();
    check("租户表单上出现了「Delete tenant」按钮", false, body.slice(0, 140).replace(/\n/g, " "));
    await page.screenshot({ path: `${SHOT_DIR}/tenant-delete-ui-failed.png`, fullPage: true });
    await browser.close();
    console.log("RESULT: FAIL");
    process.exit(1);
  }
  check("租户表单上出现了「Delete tenant」按钮", true);

  await button.click();
  // 确认框必须**把要删的东西列出来**（站点/账号/记录），不然谁也不敢点
  const dialog = page.locator(".modal-content").first();
  await dialog.waitFor({ timeout: 15000 });
  const text = await dialog.innerText();
  check("弹出了确认框，并说明不可恢复",
    /cannot be undone/i.test(text), text.slice(0, 80).replace(/\n/g, " "));
  check("确认框里列出了站点域名", !SITE || text.includes(SITE), SITE);
  check("确认框里按类别列了控制面记录",
    /Issues|Deployments|Sites and databases/i.test(text));
  await page.screenshot({ path: `${SHOT_DIR}/tenant-delete-confirm.png`, fullPage: true });

  // 确认（frappe.confirm 的按钮文案是 Yes / 确定，按位置取最后一个按钮更稳）
  await dialog.locator("button").last().click();
  const alert = await page
    .waitForSelector("text=Deletion queued", { timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  check("点确认后如实提示「已排入队列」，不是假装删完了", alert);

  await page.waitForTimeout(2500);
  const body = await page.locator("body").innerText();
  check("表单状态变成「删除中」",
    /deleting|删除中|Deleting/i.test(body), (body.match(/deleting|删除中|Deleting/gi) || []).slice(0, 2).join(" "));
  check("按钮变成「重试删除」（删失败了还能再来一次）",
    /Retry deletion|重试删除/i.test(body));
  await page.screenshot({ path: `${SHOT_DIR}/tenant-delete-queued.png`, fullPage: true });

  await browser.close();
  const failed = results.filter(([, ok]) => !ok);
  console.log("");
  console.log(`合计: 通过 ${results.length - failed.length} / 失败 ${failed.length}`);
  console.log(`RESULT: ${failed.length ? "FAIL" : "ALL PASS"}`);
  process.exit(failed.length ? 1 : 0);
}

main().catch((err) => {
  console.error("FAIL 跑挂了:", err);
  console.log("RESULT: FAIL");
  process.exit(1);
});
