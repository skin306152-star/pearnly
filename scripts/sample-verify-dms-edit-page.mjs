// 浏览器编辑器页的**真实点击验收**：真开页面 → 读草稿 → 改一格 → 保存。
//
// 为什么要有它：后端那一层（`sample-verify-dms-edit-browser.sh`）验的是接口与守卫；
// 这一层验的是"用户真的能用"—— 表单铺得出来、下拉有选项、语言开关能动、
// 点保存之后页面进到"已保存"并且**服务端真的把新卡发出去了**。
//
// 页面认的是**一次性票据**（不是站点会话），所以脚本把票据塞进 sessionStorage 再打开 ——
// 与 `/liff/dms-booking` 那一页交接的做法完全一致（同一个键）。
//
// 用法:
//   node scripts/sample-verify-dms-edit-page.mjs --base <站点地址> --draft <nonce> --ticket <票据>
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const requireFrom = createRequire(
  (process.env.PEARNLY_PLAYWRIGHT_BASE || "/Users/skin/Developer/Pearnly/pearnly-app/") +
    "package.json",
);
const { chromium } = requireFrom("playwright");

const SHOT_DIR = process.env.PEARNLY_SHOT_DIR || "docs/p4/evidence";
mkdirSync(SHOT_DIR, { recursive: true });

function arg(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] || "" : "";
}

const BASE = arg("base") || "http://one761900.localhost:8103";
const DRAFT = arg("draft");
const TICKET = arg("ticket");
const MODE = arg("mode") === "customer" ? "customer" : "booking";
const APP = `${BASE}/assets/pearnly_core/pearnly-app/index.html`;

const results = [];
function check(label, ok, detail = "") {
  results.push([label, !!ok]);
  console.log(`${ok ? "PASS" : "FAIL"} ${label}${detail ? " — " + detail : ""}`);
}

/**
 * 按 label 文本定位那一格里的控件。
 *
 * 官方 FormControl 把 `<label>` 与控件放在**同一个容器**里（label 的父节点就是它），
 * 所以 `label → xpath=.. → input` 是最稳的定位方式。
 * `hasText` 用子串匹配：必填字段的标签实际是「Full name * (required)」。
 */
function wrapOf(page, label) {
  return page.locator("label", { hasText: label }).first().locator("xpath=..");
}

function field(page, label) {
  return wrapOf(page, label).locator("input, textarea").first();
}

/** reka-ui 的下拉**是按钮不是 `<select>`**：读它触发按钮上的文字。 */
function selectTrigger(page, label) {
  return wrapOf(page, label).locator("button").first();
}

async function main() {
  if (!DRAFT || !TICKET) {
    console.error("用法: --base <站点> --draft <nonce> --ticket <票据>");
    process.exit(2);
  }
  const browser = await chromium.launch();
  // 手机视口：这一页本来就是手机上的 LINE 页面
  const context = await browser.newContext({ viewport: { width: 414, height: 896 } });
  const page = await context.newPage();

  // 票据与语言（`pearnly_lang`）都要在页面脚本跑之前就位。
  await page.addInitScript(
    ([ticket]) => {
      try {
        sessionStorage.setItem("pearnly_dms_ticket", ticket);
        localStorage.setItem("pearnly_lang", "en"); // 断言用英文文案，稳且可读
      } catch (e) {
        /* 无所谓 */
      }
    },
    [TICKET],
  );

  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(msg.text());
  });

  const editorQuery = MODE === "customer" ? "&editor=customer" : "";
  await page.goto(`${APP}#/dms/editor?draft=${encodeURIComponent(DRAFT)}${editorQuery}`, {
    waitUntil: "networkidle",
  });

  // ── 1. 独立页：没有产品外壳，只有编辑器 ─────────────────────────────
  const shell = await page.locator("text=录入工作台").count();
  check("这一页**不套产品外壳**（LINE 里打开的，不是后台）",
    (await page.locator(".pn-editor").count()) === 1 && shell === 0, `外壳项 ${shell}`);

  // 本机的 DMS 是**一个测试账号只允许一个活跃会话**（老产品文档写明）。夹具刚刚登过一次，
  // 紧接着页面又登一次，偶尔会被判成"并发登录" → 这一页如实报"主档暂时读不到"。
  // 这是环境的真实性质，不是页面 bug，所以这里刷新重试几次；真机上没有这个问题。
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    const ready = await page
      .waitForSelector("text=Save and send new preview", { timeout: 30000 })
      .then(() => true)
      .catch(() => false);
    if (ready) break;
    const body = await page.locator("body").innerText();
    if (attempt === 4 || !/unavailable|ไม่สามารถ|不可以使用|无法/.test(body)) {
      check("草稿加载完成（表单出了保存按钮）", false, body.slice(0, 120));
      await page.screenshot({ path: `${SHOT_DIR}/dms-editor-00-load-failed.png`, fullPage: true });
      await browser.close();
      console.log("RESULT: FAIL");
      process.exit(1);
    }
    console.log(`  （DMS 单会话限制撞上了，第 ${attempt} 次重试）`);
    await page.waitForTimeout(2000);
    await page.reload({ waitUntil: "networkidle" });
  }
  check("草稿加载完成（表单出了保存按钮）", true);

  // ── 2. 草稿真的铺进表单 ─────────────────────────────────────────────
  const name = await field(page, "Full name").inputValue();
  const phone = await field(page, "Phone").inputValue();
  const peopleId = await field(page, "National ID").inputValue();
  check("客户姓名来自草稿", name === "คุณสมชาย", name);
  check("电话来自草稿", phone === "0811111111", phone);
  check("身份证号来自草稿", peopleId === "1101700998118", peopleId);

  const title = await selectTrigger(page, "Title").textContent();
  check("称谓下拉已选中（主档解出来的）", (title || "").includes("นาย"), title || "");

  // 地址：两种模式都有（地址级联首屏就该带回来）
  const province = await selectTrigger(page, "Province").textContent();
  check("府已选中（地址级联首屏就有）", (province || "").trim().length > 0, province || "");

  if (MODE === "customer") {
    // 客户档模式：只有客户资料那一节（订车 / 付款 / 附件都不该出现）
    const headings = await page.locator(".pn-editor-section h2").allInnerTexts();
    check("客户档模式只出「客户资料」一节",
      headings.length === 1 && headings[0] === "Customer", headings.join(" / "));
    check("客户档模式的标题是「改客户资料」",
      (await page.locator("h1").first().textContent()).includes("Edit customer details"));
    check("客户档模式没有付款那一节", (await page.locator("text=Payment methods").count()) === 0);
    check("客户档模式没有附件那一节", (await page.locator("text=Attachments").count()) === 0);
  } else {
    const car = await selectTrigger(page, "Vehicle model").textContent();
    check("车型下拉已选中（真实 DMS 主档里的那台）", /123/.test(car || ""), car || "");

    const paint = await selectTrigger(page, "Colour").textContent();
    check("颜色跟着车型一起带出来", (paint || "").trim().length > 0, paint || "");

    const channel = await selectTrigger(page, "Method").textContent();
    const amount = await field(page, "Amount").inputValue();
    check("付款那一行有渠道和金额", (channel || "").includes("Cash") && amount === "5000.00",
      `${channel} / ${amount}`);
    const total = await field(page, "Booking deposit total").inputValue();
    check("合计按金额算出来", total.replace(/,/g, "") === "5000.00", total);

    const keepIdCard = await page.locator('input[type="checkbox"]').first().isChecked();
    check("身份证附件默认保留", keepIdCard === true);
  }

  await page.screenshot({ path: `${SHOT_DIR}/dms-editor-01-loaded-${MODE}.png`, fullPage: true });

  // ── 4. 语言开关真的换文案（文案来自服务端语言包）────────────────────
  await page.selectOption(".pn-editor-lang", "zh");
  await page.waitForSelector("text=保存并发送新版预览", { timeout: 5000 });
  const zhTitle = await page.locator("h2").first().textContent();
  check("切中文 → 文案变了（服务端语言包四语齐全）", zhTitle.includes("客户资料"), zhTitle || "");
  await page.selectOption(".pn-editor-lang", "en");
  await page.waitForSelector("text=Save and send new preview", { timeout: 5000 });

  // ── 5. 改一格 → 保存 ────────────────────────────────────────────────
  const newPhone = "0866666666";
  await field(page, "Phone").fill(newPhone);
  // 本机 DMS 是**一个测试账号只允许一个活跃会话**：连着登几次时偶尔会被判成并发登录，
  // 服务端如实回"主档暂时读不到"、草稿原样回滚。这正是用户会点"再来一次"的情形，
  // 所以这里也重试（**只对这一类可重试的错**重试；别的错立刻失败）。
  let saved = false;
  let lastShown = "";
  for (let attempt = 1; attempt <= 4 && !saved; attempt += 1) {
    await page.click("text=Save and send new preview");
    saved = await page
      .waitForSelector("text=A revised preview was sent to LINE", { timeout: 60000 })
      .then(() => true)
      .catch(() => false);
    if (saved) break;
    lastShown = await page.locator(".pn-editor-error").innerText().catch(() => "");
    const retryable = /unavailable|Try again|ไม่สามารถ|不可以|无法/i.test(lastShown);
    if (!retryable || attempt === 4) break;
    console.log(`  （DMS 单会话限制撞上了，保存第 ${attempt} 次重试）`);
    await page.waitForTimeout(3000);
  }
  if (!saved) {
    // 失败时把页面**自己**说的话带出来：是"卡片没发出去"还是"选项过期了"，
    // 只有页面知道（服务端只回一个错误码，页面把它翻成了人话）。
    check("保存成功 → 页面进到「已保存」那一屏（服务端确认卡片已送出）", false,
      lastShown || "(页面没报错)");
    await page.screenshot({ path: `${SHOT_DIR}/dms-editor-03-save-failed-${MODE}.png`, fullPage: true });
    await browser.close();
    console.log("RESULT: FAIL");
    process.exit(1);
  }
  check("保存成功 → 页面进到「已保存」那一屏（服务端确认卡片已送出）", true);
  await page.screenshot({ path: `${SHOT_DIR}/dms-editor-02-saved-${MODE}.png`, fullPage: true });

  check("过程中没有页面级 JS 报错", errors.length === 0, errors.slice(0, 2).join(" | "));

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
