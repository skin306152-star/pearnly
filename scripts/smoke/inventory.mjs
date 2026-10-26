// 页面入口盘点驱动（回归基线用）：一个无头 Chrome 带同一个会话依次打开多页，每页跑 probes/inventory.js，逐行输出 JSON。
// 用法：node scripts/smoke/inventory.mjs '<JSON>'，JSON = {base, pages: [路径], cookie_domain, sid, secure, crawl, max_pages, ua}
//   crawl：顺着每页侧栏 / 顶栏下拉 / 主区里的站内 /desk、/app 链接继续打开（只读，不点按钮），最多 max_pages 页。
// Chrome 路径：环境变量 SMOKE_CHROME，默认 macOS 的 Google Chrome。Node 22 起自带 WebSocket / fetch，不需要 Playwright。
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const job = JSON.parse(process.argv[2] || "{}");
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9300 + Math.floor(Math.random() * 600);
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "smoke-"));
const chrome = spawn(chromePath, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
  ...(job.video ? [`--use-file-for-fake-video-capture=${job.video}`] : []),
  // 实验栈经 Mac 侧端口转发打开，不切域名：测试租户的域名在本机 Chrome 里解析到回环（SMOKE_CHROME_RULES，如 "MAP testcowork.pearnly.com 127.0.0.1"）
  ...(process.env.SMOKE_CHROME_RULES ? [`--host-resolver-rules=${process.env.SMOKE_CHROME_RULES}`] : []),
  "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logs = [];
let ws, seq = 0;
const pending = new Map();
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => pending.set(message.id, { resolve, reject }));
};

const ready = `(() => { if (!(window.frappe && frappe.boot && frappe.get_route && document.readyState === 'complete')) return -1;
  const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
  if (!page) return -1;
  if ([...page.querySelectorAll(".freeze")].some((el) => el.offsetParent !== null)) return -1;
  const length = (page.innerText || "").trim().length;
  const native = page.querySelector(".message-page-image, .message-page-icon") || [...page.querySelectorAll(".no-result")].some((el) => el.offsetParent !== null);
  return native ? Math.max(length, 21) : length; })()`;
const probeBody = readFileSync(join(here, "probes", "inventory.js"), "utf8");
const origin = new URL(job.base).origin;
const queue = [...(job.pages || ["/desk"])];
const seen = new Set();
const max = job.max_pages || 80;
const norm = (href) => {
  try {
    const url = new URL(href, origin);
    if (url.origin !== origin) return null;
    if (!/^\/(desk|app)(\/|$)/.test(url.pathname)) return null;
    return url.pathname.replace(/\/+$/, "") + (url.search || "");
  } catch { return null; }
};
try {
  let info;
  for (let i = 0; i < 150 && !info; i++) {
    try { info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { await sleep(200); }
  }
  if (!info) throw new Error(`Chrome did not start: ${chromePath}`);
  ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((resolve) => ws.addEventListener("open", resolve));
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result);
    }
    if (message.method === "Runtime.exceptionThrown") {
      const details = message.params.exceptionDetails;
      logs.push({ kind: "exception", text: `${details.text} ${details.exception?.description || ""}`.slice(0, 300) });
    }
    if (message.method === "Page.javascriptDialogOpening") {
      logs.push({ kind: "exception", text: `native dialog: ${message.params.type} ${String(message.params.message).slice(0, 200)}` });
      send("Page.handleJavaScriptDialog", { accept: false }, message.sessionId).catch(() => {});
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Network.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  if (job.ua) await send("Emulation.setUserAgentOverride", { userAgent: job.ua }, sessionId);
  if (job.sid) await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: job.secure !== false }, sessionId);
  while (queue.length && seen.size < max) {
    const path = norm(queue.shift());
    if (!path || seen.has(path)) continue;
    seen.add(path);
    logs.length = 0;
    const started = Date.now();
    // 第一页整页打开（取 boot），之后在页内换路由（frappe.router.push_state）：不重载框架，一页从十几秒降到一两秒；
    // 弹窗 / 菜单不跨页残留（每页先按 Esc、关掉打开的弹窗）
    const before = (await send("Runtime.evaluate", { expression: "location.pathname", returnByValue: true }, sessionId).catch(() => ({})))?.result?.value || "";
    const booted = (await send("Runtime.evaluate", { expression: "!!(window.frappe && frappe.router && frappe.boot)", returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
    if (booted && job.spa !== false) {
      // 换路由前给当前所有页面容器打标记：换完如果露出来的还是带标记的旧容器，说明新页没渲染出来（多是无权限的框架路由），记「没打开」
      await send("Runtime.evaluate", { expression: `document.querySelectorAll(".page-container").forEach((el) => { el.dataset.invOld = "1"; })` }, sessionId).catch(() => {});
      await send("Runtime.evaluate", { expression: `(() => { document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        (window.cur_dialog && cur_dialog.hide && cur_dialog.hide()); frappe.router.push_state(${JSON.stringify(path)}); })()` }, sessionId).catch(() => {});
    } else {
      await send("Page.navigate", { url: origin + path }, sessionId);
    }
    let last = -1, since = Date.now(), readyMs = null;
    const want = path.split("?")[0];
    // 页内换路由时最多等 8 秒：没权限的框架路由（getpage 403）页面根本不出来，等满了记「打不开」
    const limit = booted && job.spa !== false ? 8000 : 20000;
    while (Date.now() - started < limit) {
      const now = (await send("Runtime.evaluate", { expression: `(location.pathname.replace(/\\/+$/, "") === ${JSON.stringify(want)} || location.pathname !== ${JSON.stringify(before)} || ${!booted}) ? ${ready} : -1`, returnByValue: true }, sessionId).catch(() => ({})))?.result?.value ?? -1;
      if (now > 20 && now === last) { if (Date.now() - since >= 800) { readyMs = since - started; break; } }
      else { last = now; since = Date.now(); }
      await sleep(200);
    }
    const stale = booted && job.spa !== false && (await send("Runtime.evaluate", { expression: `(() => { const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container"); return !!(page && page.dataset.invOld); })()`, returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
    if (stale) {
      console.log(JSON.stringify({ path, final: null, ready_ms: null, error: null, logs: [...logs], inventory: null, not_rendered: true }));
      continue;
    }
    const expression = `(async (args) => {\n${probeBody}\n})({})`;
    const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: 30000 }, sessionId).catch((error) => ({ exceptionDetails: { exception: { description: String(error) } } }));
    const value = result.result?.value ?? null;
    const finalUrl = (await send("Runtime.evaluate", { expression: "location.pathname + location.search", returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
    console.log(JSON.stringify({ path, final: finalUrl, ready_ms: readyMs, error: result.exceptionDetails?.exception?.description || null, logs: [...logs], inventory: value }));
    if (job.crawl && value) {
      const hrefs = [...(value.desk_sidebar || []).map((row) => row.href), ...(value.navbar?.items || []).map((row) => row.href), ...(value.links || []).map((row) => row.href)];
      // 同一类页（/desk/<页或 DocType>）最多看 2 个实例：记录表单每种看两张就够，不把整张列表的记录都打开
      for (const href of hrefs) {
        const next = href && norm(href);
        if (!next || seen.has(next) || queue.includes(next)) continue;
        const kind = next.split("/").slice(0, 3).join("/");
        const same = [...seen, ...queue].filter((p) => p && p.split("/").slice(0, 3).join("/") === kind).length;
        if (same < 2 || next.split("/").length <= 3) queue.push(next);
      }
    }
  }
} catch (error) {
  console.log(JSON.stringify({ error: `driver: ${error.message}` }));
} finally {
  chrome.kill("SIGKILL");
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 临时目录删不掉不影响结果 */ }
}
process.exit(0);
