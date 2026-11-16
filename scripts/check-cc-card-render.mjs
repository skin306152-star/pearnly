#!/usr/bin/env node
// CC 对话卡片渲染闸门（11-16 独立前端验收，主会话批）：把预览页 kit.html 里登记的卡片场景，按
// 四语 × 电脑 1280 / 手机 375 触屏 × 浅色 / 深色，逐个用真 Chrome 打开量一遍，量出来不对就失败。
// 起因：11-16 第 1 轮验收靠人看 348 张截图才发现手机泰文对方名被挤没、金额截成「54」越出卡外、卡没有标题栏、按钮不一套。
//
// 每个场景都在**新的浏览器上下文**里打开（同一页连开多个场景时触屏状态会串，11-16 两个窗口的截图都踩过）。
// 量什么（判据见 ~/pearnly-devtools/frontend-audit-1116/checklist.md 的编号）：
//   R1 页面报错（未捕获异常）                                   G 通用
//   R2 页面横向滚动                                             G13
//   R3 卡里的元素越出卡片右边                                   B78
//   R4 卡里的字被截断（省略号 / 内容比框宽）                    B78（名称、金额不截断）
//   R5 触屏下出现键盘快捷键提示                                 G11
//   R6 每张卡顶上是标题栏（.cc-ask-q 或问卷题目栏）             B73
//   R7 卡片按钮一行最多 2 个、主按钮在最右                      B74
//   R8 有「推荐」的那一题：推荐项排第一、打开时一个都没选        B77（写账前的题不预选，主会话 11-16）
//
//   node scripts/check-cc-card-render.mjs                              # 构建预览页（CC_KIT=1）后全量量
//   node scripts/check-cc-card-render.mjs --dist DIR                   # 用已构建好的产物（不再构建）
//   node scripts/check-cc-card-render.mjs --root DIR                   # 量别的仓库目录（默认本仓）
//   node scripts/check-cc-card-render.mjs --scenarios FILE             # 场景表（默认 scripts/cc-card-scenarios.json）
//   node scripts/check-cc-card-render.mjs --quick                      # 只量 zh / th 浅色两种视口（本地快查）
//   node scripts/check-cc-card-render.mjs --shots DIR                  # 顺带每页截一张全页图
//   node scripts/check-cc-card-render.mjs --json FILE                  # 结果写成 JSON（复跑比对用）
// Chrome：环境变量 SMOKE_CHROME（CI 是 /usr/bin/google-chrome；本机用 Playwright 自带的 chrome-headless-shell）。
// Node 22 起自带 WebSocket / fetch，不需要 Playwright。只读：不写仓库，产物与 Chrome 用户目录都在临时目录，结束删掉。
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const opt = (name, fallback = null) => {
  const i = argv.indexOf(name);
  return i >= 0 ? argv[i + 1] : fallback;
};
const root = resolve(opt("--root", join(here, "..")));
const scenarioFile = resolve(opt("--scenarios", join(root, "scripts", "cc-card-scenarios.json")));
const quick = argv.includes("--quick");
const shots = opt("--shots");
const jsonOut = opt("--json");
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const work = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "cc-card-render-"));
writeFileSync(join(work, ".owner-pid"), String(process.pid));

const RECOMMENDED = ["推荐", "แนะนำ", "Recommended", "おすすめ"];
const COMBOS = quick
  ? [["zh", "light", "pc"], ["zh", "light", "phone"], ["th", "light", "pc"], ["th", "light", "phone"]]
  : [
      ...["zh", "th"].flatMap((lang) => ["light", "dark"].flatMap((theme) => ["pc", "phone"].map((vp) => [lang, theme, vp]))),
      ...["en", "ja"].flatMap((lang) => ["pc", "phone"].map((vp) => [lang, "light", vp])),
    ];

let chrome;
let server;
const cleanup = () => {
  try { chrome?.kill("SIGKILL"); } catch { /* 已退 */ }
  try { server?.close(); } catch { /* 已关 */ }
  try { rmSync(work, { recursive: true, force: true }); } catch { /* 删不掉不影响结果 */ }
};
process.on("exit", cleanup);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => { cleanup(); process.exit(130); });

// ── 构建与静态服务 ──────────────────────────────────────────────────────────────────────────────────
function build() {
  const dist = opt("--dist");
  if (dist) return resolve(dist);
  const out = join(work, "dist");
  const app = join(root, "frontend", "assistant");
  const r = spawnSync("npx", ["vite", "build", "--outDir", out, "--base", "/", "--emptyOutDir"], {
    cwd: app, env: { ...process.env, CC_KIT: "1" }, encoding: "utf8",
  });
  if (r.status !== 0) throw new Error(`预览页构建失败：\n${(r.stdout || "").slice(-2000)}${(r.stderr || "").slice(-2000)}`);
  return out;
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
  ".woff2": "font/woff2", ".json": "application/json", ".webmanifest": "application/manifest+json", ".ico": "image/x-icon" };

function serve(dist) {
  return new Promise((ok) => {
    server = createServer((req, res) => {
      const path = decodeURIComponent(new URL(req.url, "http://x").pathname);
      const file = join(dist, path === "/" ? "index.html" : path);
      if (!file.startsWith(dist) || !existsSync(file) || !statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
      res.end(readFileSync(file));
    });
    server.listen(0, "127.0.0.1", () => ok(server.address().port));
  });
}

// ── Chrome 与 CDP ───────────────────────────────────────────────────────────────────────────────────
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let ws;
let seq = 0;
const pending = new Map();
const exceptions = new Map(); // sessionId → [文本]
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((ok, fail) => pending.set(message.id, { ok, fail }));
};

async function startChrome() {
  const port = 9300 + Math.floor(Math.random() * 600);
  chrome = spawn(chromePath, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${join(work, "profile")}`,
    "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
  let info;
  for (let i = 0; i < 150 && !info; i++) {
    try { info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { await sleep(200); }
  }
  if (!info) throw new Error(`Chrome 没起来：${chromePath}`);
  ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((ok) => ws.addEventListener("open", ok));
  ws.addEventListener("message", (event) => {
    const m = JSON.parse(event.data);
    if (m.id && pending.has(m.id)) {
      const { ok, fail } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? fail(new Error(JSON.stringify(m.error))) : ok(m.result);
    }
    if (m.method === "Runtime.exceptionThrown" && m.sessionId) {
      const d = m.params.exceptionDetails;
      (exceptions.get(m.sessionId) ?? exceptions.set(m.sessionId, []).get(m.sessionId))
        .push(`${d.text} ${d.exception?.description || ""}`.slice(0, 300));
    }
  });
}

// ── 页面上量（在页面里跑，返回一组问题）──────────────────────────────────────────────────────────────
const PROBE = (phone, recommended) => `(() => {
  const issues = [];
  const say = (rule, text) => issues.push({ rule, text: String(text).slice(0, 160) });
  const label = (el) => (el.getAttribute("data-slot") || el.tagName.toLowerCase()) + "「" + (el.textContent || "").trim().slice(0, 30) + "」";
  if (document.documentElement.scrollWidth > window.innerWidth + 1) say("R2", "页面横向滚动 " + document.documentElement.scrollWidth + " > " + window.innerWidth);
  const cards = [...document.querySelectorAll(".cc-steps-card")];
  for (const card of cards) {
    const box = card.getBoundingClientRect();
    if (!box.width) continue;
    for (const el of card.querySelectorAll("*")) {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      if (!r.height || cs.position === "fixed" || cs.visibility === "hidden" || el.closest("[hidden], [aria-hidden='true']")) continue;
      if (r.width && r.right > box.right + 1 && el.children.length === 0 && (el.textContent || "").trim()) say("R3", label(el) + " 越出卡片右边 " + Math.round(r.right - box.right) + "px");
      // 被挤成 0 宽的名称也算截断（11-16 第 1 轮：手机泰文对方名宽度被挤到 0，什么都看不见）
      const leaf = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (leaf && (cs.textOverflow === "ellipsis" || cs.overflowX === "hidden" || cs.overflow === "hidden") && el.scrollWidth > el.clientWidth + 1
          && el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") say("R4", label(el) + " 被截断");
    }
    const head = card.querySelector(".cc-ask-q, .cc-ui-questionnaire-title");
    const top = [...card.children][0];
    if (!head) say("R6", "卡片没有标题栏：" + label(card));
    else if (top && head.getBoundingClientRect().top - box.top > 4) say("R6", "标题栏不在卡片顶上：" + label(head));
    // 卡片脚的按钮行（卡片骨架 / 允许卡的 .cc-card-actions、提问卡的问卷按钮行）；行里的就地小按钮（如「填入差额」）不算
    const rows = new Set([...card.querySelectorAll(".cc-card-btn")].map((b) => b.parentElement)
      .filter((row) => row.matches(".cc-card-actions, [data-slot='questionnaire-actions']")));
    for (const row of rows) {
      const buttons = [...row.children].filter((b) => b.classList.contains("cc-card-btn") && !b.hasAttribute("data-icon") && b.getBoundingClientRect().width);
      if (buttons.length > 2) say("R7", "一行 " + buttons.length + " 个卡片按钮：" + buttons.map((b) => b.textContent.trim()).join(" / "));
      const names = buttons.map((b) => b.textContent.trim()).join(" / ");
      const primary = buttons.findIndex((b) => b.hasAttribute("data-primary"));
      if (primary >= 0 && primary !== buttons.length - 1) say("R7", "主按钮不在最右：" + names);
      // 次按钮只有灰底一种（Button variant="secondary"；不带 data-variant 的是线上手写旧卡，归 test_cc_card_buttons 的存量）
      for (const b of buttons) if (!b.hasAttribute("data-primary") && b.hasAttribute("data-variant") && b.getAttribute("data-variant") !== "secondary")
        say("R7", "次按钮不是灰底（" + b.getAttribute("data-variant") + "）：" + b.textContent.trim());
      // 一行按钮挨着、靠右（11-16 第 1 轮：「取消」在最左、「写入」在最右）
      for (let i = 1; i < buttons.length; i++) {
        const gap = buttons[i].getBoundingClientRect().left - buttons[i - 1].getBoundingClientRect().right;
        if (gap > 24) say("R7", "按钮没有挨着靠右（间隔 " + Math.round(gap) + "px）：" + names);
      }
      if (buttons.length && box.right - buttons[buttons.length - 1].getBoundingClientRect().right > 40) say("R7", "按钮行没有靠右：" + names);
    }
  }
  if (${phone}) for (const el of document.querySelectorAll(".cc-menu-key, kbd")) if (el.getBoundingClientRect().width) say("R5", "触屏下出现快捷键 " + el.textContent);
  const words = ${JSON.stringify(recommended)};
  for (const group of document.querySelectorAll("[data-slot='questionnaire-choices']")) {
    const choices = [...group.querySelectorAll("[data-slot='questionnaire-choice']")];
    const index = choices.findIndex((c) => [...c.querySelectorAll("[data-slot='badge']")].some((b) => words.includes(b.textContent.trim())));
    if (index > 0) say("R8", "推荐项排在第 " + (index + 1) + " 个：" + label(choices[index]));
    if (index >= 0 && choices.some((c) => c.hasAttribute("data-checked") || c.querySelector("input:checked"))) say("R8", "有「推荐」的题打开时已有选中项");
  }
  return issues;
})()`;

async function measure(base, scenario, [lang, theme, vp]) {
  const phone = vp === "phone";
  const { browserContextId } = await send("Target.createBrowserContext", { disposeOnDetach: true });
  const { targetId } = await send("Target.createTarget", { url: "about:blank", browserContextId });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  try {
    await send("Runtime.enable", {}, sessionId);
    await send("Page.enable", {}, sessionId);
    await send("Emulation.setDeviceMetricsOverride", phone
      ? { width: 375, height: 812, deviceScaleFactor: 2, mobile: true }
      : { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, sessionId);
    if (phone) await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, sessionId);
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: theme }] }, sessionId);
    const url = `${base}/kit.html?${scenario.q}&lang=${lang}&theme=${theme}`;
    await send("Page.navigate", { url }, sessionId);
    // 替身引擎带 120ms 延迟、卡片按版本再取：等到卡出现且 0.5 秒不再变化（最多 8 秒）
    let last = "", since = Date.now();
    const started = Date.now();
    while (Date.now() - started < 8000) {
      const now = (await send("Runtime.evaluate", { expression: "document.querySelector('main')?.innerHTML.length + ':' + document.querySelectorAll('.cc-steps-card').length", returnByValue: true }, sessionId)).result?.value ?? "";
      if (now !== last) { last = now; since = Date.now(); } else if (Date.now() - since > 500 && !String(now).endsWith(":0")) break;
      await sleep(150);
    }
    const issues = (await send("Runtime.evaluate", { expression: PROBE(phone, RECOMMENDED), returnByValue: true }, sessionId)).result?.value ?? [];
    for (const text of exceptions.get(sessionId) ?? []) issues.push({ rule: "R1", text });
    if (String(last).endsWith(":0") && !scenario.nocard) issues.push({ rule: "R6", text: "页面上一张卡都没有（场景没画出来）" });
    if (shots) {
      const metrics = await send("Page.getLayoutMetrics", {}, sessionId);
      const height = Math.ceil(metrics.cssContentSize?.height || 900);
      const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true,
        clip: { x: 0, y: 0, width: phone ? 375 : 1280, height, scale: 1 } }, sessionId);
      writeFileSync(join(shots, `${scenario.name}_${vp}_${theme}_${lang}.png`), Buffer.from(shot.data, "base64"));
    }
    return issues;
  } finally {
    exceptions.delete(sessionId);
    await send("Target.closeTarget", { targetId }).catch(() => {});
    await send("Target.disposeBrowserContext", { browserContextId }).catch(() => {});
  }
}

// ── 主流程 ──────────────────────────────────────────────────────────────────────────────────────────
const scenarios = JSON.parse(readFileSync(scenarioFile, "utf8")).scenarios;
if (shots) mkdirSync(shots, { recursive: true });
let exit = 0;
try {
  const dist = build();
  const base = `http://127.0.0.1:${await serve(dist)}`;
  await startChrome();
  const results = [];
  for (const combo of COMBOS) {
    for (const scenario of scenarios) {
      const issues = await measure(base, scenario, combo);
      results.push({ scenario: scenario.name, combo: combo.join("/"), issues });
    }
  }
  const bad = results.filter((r) => r.issues.length);
  if (jsonOut) writeFileSync(jsonOut, JSON.stringify(results, null, 1));
  console.log(`量了 ${results.length} 页（${scenarios.length} 个场景 × ${COMBOS.length} 种语言 / 主题 / 视口）`);
  if (!bad.length) {
    console.log("PASS: 无报错、无横滚、无越界与截断、触屏无快捷键、卡片有标题栏、按钮行与推荐项正确");
  } else {
    exit = 1;
    console.log(`FAIL: ${bad.length} 页有问题`);
    for (const r of bad) for (const i of r.issues) console.log(`  ${i.rule}  ${r.scenario} [${r.combo}]  ${i.text}`);
  }
} catch (error) {
  console.error(`check-cc-card-render: ${error.message}`);
  exit = 2;
}
cleanup();
process.exit(exit);
