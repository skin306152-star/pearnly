// 性能体检的页面驱动：无头 Chrome 带一个会话依次打开多页，量「页面可用」用时，并把这一页发出的接口记下来（浏览器侧用时）。
// 每页打开前把请求头 `X-Perf-Page` 设成该页路径，服务端 `trace_server.py` 按它把查询条数归到页。只读：不点按钮。
// 用法：node scripts/perf/pages.mjs '<JSON>'，JSON = {base, pages: [路径], cookie_domain, sid, settle_ms}
// 每页一行 JSON：{path, ready_ms, requests: [{cmd, ms, status}], error}。第一页整页打开（含框架启动），之后页内换路由（同用户点侧栏）。
// 默认先把全部页面预热一遍（请求头记 `warm:<路径>`，不输出），第二遍才记；`warm: false` 不预热。
import { DEVTOOLS_PORT_FLAG, devtoolsVersion } from "../smoke/devtools.mjs";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const job = JSON.parse(process.argv[2] || "{}");
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "perf-"));
const chrome = spawn(chromePath, [
  "--headless=new", DEVTOOLS_PORT_FLAG, `--user-data-dir=${profile}`,
  ...(process.env.SMOKE_CHROME_RULES ? [`--host-resolver-rules=${process.env.SMOKE_CHROME_RULES}`] : []),
  "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank",
], { stdio: "ignore" });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let ws, seq = 0;
const pending = new Map();
const inflight = new Map();
let finished = [];
let lastNet = Date.now();
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => pending.set(message.id, { resolve, reject }));
};
const ready = `(() => { if (!(window.frappe && frappe.boot && frappe.get_route && document.readyState === 'complete')) return -1;
  const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
  if (!page) return -1;
  if ([...page.querySelectorAll(".freeze, .skeleton, .list-loading")].some((el) => el.offsetParent !== null)) return -1;
  const length = (page.innerText || "").trim().length;
  const native = page.querySelector(".message-page-image, .message-page-icon") || [...page.querySelectorAll(".no-result")].some((el) => el.offsetParent !== null);
  return native ? Math.max(length, 21) : length; })()`;
const api = (url) => { try { const u = new URL(url); return u.pathname.startsWith("/api/") ? u.pathname.replace(/^\/api\/method\//, "") : null; } catch { return null; } };
const origin = new URL(job.base).origin;
try {
  const info = await devtoolsVersion(profile, chrome);
  if (!info) throw new Error(`Chrome did not start: ${chromePath}`);
  ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((resolve) => ws.addEventListener("open", resolve));
  ws.addEventListener("message", (event) => {
    const m = JSON.parse(event.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    }
    if (m.method === "Network.requestWillBeSent") {
      const cmd = api(m.params.request.url);
      if (cmd) { inflight.set(m.params.requestId, { cmd, start: m.params.timestamp }); lastNet = Date.now(); }
    }
    if (m.method === "Network.responseReceived" && inflight.has(m.params.requestId)) inflight.get(m.params.requestId).status = m.params.response.status;
    if ((m.method === "Network.loadingFinished" || m.method === "Network.loadingFailed") && inflight.has(m.params.requestId)) {
      const row = inflight.get(m.params.requestId);
      inflight.delete(m.params.requestId);
      finished.push({ cmd: row.cmd, ms: Math.round((m.params.timestamp - row.start) * 1000), status: row.status || "failed" });
      lastNet = Date.now();
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Network.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: false }, sessionId);
  let booted = false;
  // 先按同样顺序预热一遍（模块与元数据缓存，线上常驻进程本来就是热的），第二遍才记
  const passes = [...(job.warm === false ? [] : job.pages.map((path) => [path, false])), ...job.pages.map((path) => [path, true])];
  let recording = false;
  for (const [path, record] of passes) {
    // 预热完第一次记的那页整页重新打开：服务端是热的，前端各页的缓存清空（不然再进同一页不再取数，量不到接口）
    if (record && !recording) { recording = true; booted = false; }
    await send("Network.setExtraHTTPHeaders", { headers: { "X-Perf-Page": record ? path : `warm:${path}` } }, sessionId);
    finished = []; inflight.clear();
    const started = Date.now();
    if (booted) {
      await send("Runtime.evaluate", { expression: `(() => { document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
        (window.cur_dialog && cur_dialog.hide && cur_dialog.hide()); frappe.router.push_state(${JSON.stringify(path)}); })()` }, sessionId).catch(() => {});
    } else {
      await send("Page.navigate", { url: origin + path }, sessionId);
    }
    // 可用：页面内容稳定 600 毫秒，且接口都回来了（没有在途请求）
    let last = -1, since = Date.now(), readyMs = null;
    const want = path.split("?")[0];
    while (Date.now() - started < 30000) {
      const now = (await send("Runtime.evaluate", { expression: `location.pathname.replace(/\\/+$/, "").startsWith(${JSON.stringify(want)}) ? ${ready} : -1`, returnByValue: true }, sessionId).catch(() => ({})))?.result?.value ?? -1;
      if (now > 20 && now === last && inflight.size === 0) { if (Date.now() - since >= 600) { readyMs = since - started; break; } }
      else { last = now; since = Date.now(); }
      await sleep(100);
    }
    // 再等接口静下来（页面可用后才发的接口也记上）
    const settle = job.settle_ms || 1500;
    while (Date.now() - lastNet < settle || inflight.size) { if (Date.now() - started > 40000) break; await sleep(100); }
    booted = true;
    const final = readyMs === null ? (await send("Runtime.evaluate", { expression: "location.pathname", returnByValue: true }, sessionId).catch(() => ({})))?.result?.value : undefined;
    if (record) console.log(JSON.stringify({ path, ready_ms: readyMs, final, requests: finished }));
  }
} catch (error) {
  console.log(JSON.stringify({ error: `driver: ${error.message}` }));
} finally {
  chrome.kill("SIGKILL");
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 临时目录删不掉不影响结果 */ }
}
process.exit(0);
