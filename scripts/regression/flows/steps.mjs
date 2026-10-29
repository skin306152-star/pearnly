// 业务流程用例驱动：一个无头 Chrome 带会话打开起始页，按顺序执行用例里的每一步（页面里的 async JS），每步回 {ok, ...}，
// 输出一行 JSON（步骤名、ok、返回值、这一步的页面报错）。可选手机宽度、假摄像头（y4m 视频）、每步截图。
// 不点真实鼠标时直接调框架 / 产品的同一套函数；要验交互的步骤自己在 DOM 上点（`click(selector)` 辅助）。
//
//   node scripts/regression/flows/steps.mjs <用例.json>
//   用例：{base, cookie_domain, sid, start, mobile, video, shots, steps: [{name, js, wait_ms, shot, start, exec}]}
//   `exec`：这一步之前在本机跑的一条命令（argv，`run.py` 展开成实验室容器里的 `labctl.py`），最后一行 `JSON …` 作为 `exec_out` 交给这一步
//   实验室域名：FLOWS_CHROME_RULES="MAP *.flows.localhost 127.0.0.1:18791"
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const job = JSON.parse(readFileSync(process.argv[2], "utf8"));
const chromePath = process.env.FLOWS_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9700 + Math.floor(Math.random() * 190);
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "flows-"));
const chrome = spawn(chromePath, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  ...(process.env.FLOWS_CHROME_RULES ? [`--host-resolver-rules=${process.env.FLOWS_CHROME_RULES}`] : []),
  "--mute-audio", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
  ...(job.video ? [`--use-file-for-fake-video-capture=${job.video}`] : []),
  "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank"], { stdio: "ignore", detached: true });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logs = [];
let ws, seq = 0;
const pending = new Map();
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => pending.set(message.id, { resolve, reject }));
};
// 页面里能用的辅助：等条件、点元素、填原生控件、取可见文字
const HELPERS = `
  const wait = async (fn, ms = 15000, step = 200) => { const end = Date.now() + ms; let v;
    while (Date.now() < end) { try { v = await fn(); } catch (e) { v = null; } if (v) return v; await new Promise(r => setTimeout(r, step)); } return v; };
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden"
    && !el.closest(".hide, [hidden]");
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)].filter(visible);
  const byText = (sel, text, root = document) => $$(sel, root).find(el => el.textContent.trim() === text || el.textContent.trim().includes(text));
  const click = el => { if (!el) return false; el.scrollIntoView({block: "center"}); el.click(); return true; };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const msg = () => $$(".modal.show .msgprint, .modal.show .modal-body").map(e => e.textContent.trim()).join(" | ").slice(0, 300);
  const alerts = () => $$(".alert-message, .desk-alert .alert-message").map(e => e.textContent.trim()).join(" | ").slice(0, 300);
  const call = (method, args, type) => frappe.call({method, args, type: type || "POST"}).then(r => r.message);
`;
let failed = 0;
try {
  let info;
  for (let i = 0; i < 150 && !info; i++) {
    try { info = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); } catch { await sleep(200); }
  }
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
      logs.push(`${details.text} ${details.exception?.description || ""}`.slice(0, 400));
    }
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error")
      logs.push("console.error " + message.params.args.map((a) => a.value ?? a.description).join(" ").slice(0, 300));
    if (message.method === "Page.javascriptDialogOpening") {
      logs.push(`native dialog: ${message.params.type} ${String(message.params.message).slice(0, 200)}`);
      send("Page.handleJavaScriptDialog", { accept: true }, message.sessionId).catch(() => {});
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  if (job.mobile) {
    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await send("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36 Line/14.0.0" }, sessionId);
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, sessionId);
  }
  await send("Browser.grantPermissions", { permissions: ["videoCapture"], origin: new URL(job.base).origin }).catch(() => {});
  if (job.sid) await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: false }, sessionId);
  // 打开一页并等它就绪：/desk 下要等到框架路由起来（网页进程刚重起时会先回空白页，半程还没起来就重开一次）
  const open = async (path) => {
    const url = new URL(path || "/desk", job.base).href;
    const ready = "document.readyState === 'complete' && (location.pathname.startsWith('/desk') ? !!(window.frappe && frappe.boot && frappe.router)"
      + " : (!window.frappe || !window.frappe.boot || !!window.frappe.router))";
    await send("Page.navigate", { url }, sessionId);
    let ok = false;
    for (let i = 0; i < 200 && !ok; i++) {
      ok = (await send("Runtime.evaluate", { expression: ready, returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
      if (!ok && i === 100) await send("Page.navigate", { url }, sessionId);
      if (!ok) await sleep(300);
    }
    // 一分钟还没起来是环境（网页进程没响应），不当成产品问题往下跑
    if (!ok) throw new Error(`page not ready: ${path} (web process not responding)`);
    await sleep(2000);
  };
  await open(job.start);
  if (job.shots) mkdirSync(job.shots, { recursive: true });
  for (const [index, step] of (job.steps || []).entries()) {
    logs.length = 0;
    let execOut = null;
    if (step.exec) {
      const run = spawnSync(step.exec[0], step.exec.slice(1), { encoding: "utf8", timeout: 300000 });
      const line = (run.stdout || "").split("\n").reverse().find((row) => row.startsWith("JSON "));
      execOut = line ? JSON.parse(line.slice(5)) : { exec_error: ((run.stderr || "") + (run.stdout || "")).slice(-400) };
    }
    if (step.start) await open(step.start);
    const result = await send("Runtime.evaluate", { expression: `(async () => { const p = window.frappe && frappe.pearnly; const exec_out = ${JSON.stringify(execOut)}; ${HELPERS}\n${step.js} })()`,
      awaitPromise: true, returnByValue: true, timeout: step.timeout_ms || 90000 }, sessionId)
      .catch((error) => ({ exceptionDetails: { exception: { description: String(error) } } }));
    if (step.wait_ms) await sleep(step.wait_ms);
    const value = result.result?.value ?? null;
    const error = result.exceptionDetails?.exception?.description?.slice(0, 400) || null;
    const ok = !error && !!(value && value.ok);
    if (!ok) failed += 1;
    if (job.shots && (step.shot || !ok)) {
      const shot = await send("Page.captureScreenshot", { format: "png" }, sessionId).catch(() => null);
      if (shot?.data) writeFileSync(join(job.shots, `${String(index + 1).padStart(2, "0")}-${step.name.replace(/[^\w.-]+/g, "_").slice(0, 60)}.png`), Buffer.from(shot.data, "base64"));
    }
    console.log(JSON.stringify({ step: step.name, ok, value, error, page_errors: [...logs] }));
  }
} catch (error) {
  failed += 1;
  console.log(JSON.stringify({ error: `driver: ${error.message}` }));
} finally {
  try { ws?.close(); } catch {}
  try { process.kill(-chrome.pid, "SIGKILL"); } catch { chrome.kill("SIGKILL"); }
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
  process.exit(failed ? 1 : 0);
}
