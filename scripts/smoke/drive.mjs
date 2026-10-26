// 冒烟驱动：本机无头 Chrome（假摄像头放测试条码、自动允许相机）打开一页，跑一个探针，回 JSON。
// 用法：node scripts/smoke/drive.mjs '<JSON>'，JSON = {url, cookie_domain, sid, ua, probe, args, wait_ms, video}
//   probe：scripts/smoke/probes/<名>.js，内容是一个 async 函数体，拿得到 `args`，回一个对象（含 ok: true/false）。
// 页面上的未捕获异常、console.error 都收进 logs；探针本身不判异常，汇总时一律算不通过。
// Chrome 路径：环境变量 SMOKE_CHROME，默认 macOS 的 Google Chrome。Node 22 起自带 WebSocket / fetch，不需要 Playwright。
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const job = JSON.parse(process.argv[2] || "{}");
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9300 + Math.floor(Math.random() * 600);
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "smoke-"));
// 本次运行的标记：profile 里记下自己的进程号。以后的冒烟启动时只清「标记的进程已经结束」的（chrome_reaper），不按进程名或父进程猜
writeFileSync(join(profile, ".owner-pid"), String(process.pid));
const chrome = spawn(chromePath, [
  "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
  ...(job.video ? [`--use-file-for-fake-video-capture=${job.video}`] : []),
  "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank",
], { stdio: "ignore" });
// 不管正常结束、出错、被 SIGINT / SIGTERM / SIGHUP 打断还是超时，都关掉 Chrome、删临时 profile（主会话 10-26：留下过孤儿 Chrome）
let cleaned = false;
const cleanup = () => {
  if (cleaned) return;
  cleaned = true;
  try { chrome.kill("SIGKILL"); } catch { /* 已经退了 */ }
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 临时目录删不掉不影响结果 */ }
};
process.on("exit", cleanup);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => { cleanup(); process.exit(128); });
// 自己的总时限比 run.py 的 3 分钟短：到点先收尾、回超时，不等外面来杀
const deadline = setTimeout(() => {
  cleanup();
  console.log(JSON.stringify({ result: null, error: "driver: deadline", logs: [] }));
  process.exit(0);
}, Number(job.deadline_ms || 170000));
deadline.unref();
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logs = [];
let ws, seq = 0;
const pending = new Map();
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => pending.set(message.id, { resolve, reject }));
};
let output;
try {
  let info;
  for (let i = 0; i < 150 && !info; i++) {  // 最多等 30 秒：假摄像头文件要先读进来
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
      const frame = details.stackTrace?.callFrames?.[0];
      const value = details.exception?.description || JSON.stringify(details.exception?.value ?? details.exception?.preview ?? null);
      logs.push({ kind: "exception", text: `${details.text} ${value} ${frame ? `@ ${frame.url}:${frame.lineNumber + 1}` : ""}`.slice(0, 600) });
    }
    if (message.method === "Page.javascriptDialogOpening") {
      // 原生 alert / confirm / beforeunload 会把页面卡住：记下来并关掉（记进 logs，汇总时算不通过）
      logs.push({ kind: "exception", text: `native dialog: ${message.params.type} ${String(message.params.message).slice(0, 200)}` });
      send("Page.handleJavaScriptDialog", { accept: true }, message.sessionId).catch(() => {});
    }
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      logs.push({ kind: "console.error", text: message.params.args.map((a) => a.value ?? a.description).join(" ").slice(0, 400) });
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Network.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  if (job.ua) await send("Emulation.setUserAgentOverride", { userAgent: job.ua }, sessionId);
  if (job.sid) await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: true }, sessionId);
  const started = Date.now();
  await send("Page.navigate", { url: job.url }, sessionId);
  // Desk 页面：轮询到 frappe 就绪、页面主区有内容且 0.6 秒不再变化，记下打开用时（ready_ms，大数据量页面的时限按它判）；
  // 最多等 wait_ms + 30 秒。对话页等非 Desk 页面传 wait_for: "none"，照旧固定等 wait_ms。
  let readyMs = null;
  if ((job.wait_for || "frappe") === "frappe") {
    const probe = `(() => { if (!(window.frappe && frappe.boot && frappe.get_route && document.readyState === 'complete')) return -1;
      const page = document.querySelector(".page-container[data-page-route]:not([style*='display: none'])") || document.querySelector(".page-container");
      if (!page) return -1;
      // 列表取数时框架的 BaseList.freeze 会露出 .freeze 遮罩：还在取数就不算打开
      if ([...page.querySelectorAll(".freeze")].some((el) => el.offsetParent !== null)) return -1;
      // 框架原生的无权限页 / 404 页（show_message_page）与列表空状态（.no-result「没有可显示的内容」）字很少，
      // 认到它们就按「有内容」算（10-25 线上冒烟：中文无权限页 17 个字、当前公司没有收银数据的空列表都被误判没打开）
      const length = (page.innerText || "").trim().length;
      const native = page.querySelector(".message-page-image, .message-page-icon")
        || [...page.querySelectorAll(".no-result")].some((el) => el.offsetParent !== null);
      return native ? Math.max(length, 21) : length; })()`;
    let last = -1, since = 0;
    while (Date.now() - started < (job.wait_ms || 6000) + 30000) {
      const now = (await send("Runtime.evaluate", { expression: probe, returnByValue: true }, sessionId)).result?.value ?? -1;
      if (now > 20 && now === last) {
        if (Date.now() - since >= 600) { readyMs = since - started; break; }
      } else { last = now; since = Date.now(); }
      await sleep(200);
    }
  } else {
    await sleep(job.wait_ms || 6000);
  }
  const body = readFileSync(join(here, "probes", `${job.probe || "page"}.js`), "utf8");
  const expression = `(async (args) => {\n${body}\n})(${JSON.stringify(job.args || {})})`;
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: 90000 }, sessionId);
  output = { result: result.result?.value ?? null, error: result.exceptionDetails?.exception?.description || null, logs, ready_ms: readyMs };
} catch (error) {
  output = { result: null, error: `driver: ${error.message}`, logs };
} finally {
  cleanup();
}
console.log(JSON.stringify(output));
process.exit(0);
