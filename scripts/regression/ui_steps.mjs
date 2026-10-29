// 页面操作步骤驱动（回归实验室复核用）：一个无头 Chrome 带会话打开起始页，按顺序在页面里执行若干段 JS（可 await），
// 每步输出一行 JSON（步骤名、返回值、这一步里的页面报错），可选截图。不点真实鼠标，步骤里直接调框架 / 产品的同一套函数。
// 用法：node scripts/regression/ui_steps.mjs '<JSON>'，JSON = {base, cookie_domain, sid, start, shots, mobile, video, steps: [{name, js, wait_ms, shot, file}]}
// 实验室站点的域名：SMOKE_CHROME_RULES="MAP erp.regression.localhost 127.0.0.1"
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const job = JSON.parse(process.argv[2] || "{}");
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const port = 9900 + Math.floor(Math.random() * 90);
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "steps-"));
// 假摄像头：job.video 是 make_barcode_video.py 写的 y4m（条码画面）；不给就用 Chrome 自带的测试画面
const camera = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", ...(job.video ? [`--use-file-for-fake-video-capture=${job.video}`] : [])];
const chrome = spawn(chromePath, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, ...camera,
  ...(process.env.SMOKE_CHROME_RULES ? [`--host-resolver-rules=${process.env.SMOKE_CHROME_RULES}`] : []),
  "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logs = [];
let ws, seq = 0;
const pending = new Map();
const send = (method, params = {}, sessionId) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => pending.set(message.id, { resolve, reject }));
};
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
      logs.push(`${details.text} ${details.exception?.description || ""}`.slice(0, 300));
    }
    if (message.method === "Page.javascriptDialogOpening") {
      logs.push(`native dialog: ${message.params.type} ${String(message.params.message).slice(0, 200)}`);
      send("Page.handleJavaScriptDialog", { accept: false }, message.sessionId).catch(() => {});
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Page.enable", {}, sessionId);
  await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: false }, sessionId);
  // 手机宽度：job.mobile 为真时按安卓手机模拟（390 × 844、触屏、安卓 Chrome 的 UA），页面按手机判
  if (job.mobile) {
    await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, sessionId);
    await send("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0 (Linux; Android 14; SM-A556E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36" }, sessionId);
  }
  await send("Page.navigate", { url: new URL(job.start || "/desk", job.base).href }, sessionId);
  for (let i = 0; i < 100; i++) {
    const ok = (await send("Runtime.evaluate", { expression: "!!(window.frappe && frappe.boot && frappe.router && document.readyState === 'complete' && window.frappe.pearnly)", returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
    if (ok) break;
    await sleep(300);
  }
  await sleep(1500);
  for (const [index, step] of (job.steps || []).entries()) {
    logs.length = 0;
    const helper = `window.__unmark = () => document.querySelectorAll('.__mark').forEach((e) => e.remove());
      window.__mark = (el, n) => { if (!el) return false; const r = el.getBoundingClientRect(); const pad = 4;
        const box = document.createElement('div'); box.className = '__mark';
        Object.assign(box.style, {position: 'fixed', left: (r.left - pad) + 'px', top: (r.top - pad) + 'px', width: (r.width + pad * 2) + 'px',
          height: (r.height + pad * 2) + 'px', border: '3px solid #e00', borderRadius: '6px', zIndex: 2147483647, pointerEvents: 'none'});
        const tag = document.createElement('div'); tag.className = '__mark'; tag.textContent = String(n);
        Object.assign(tag.style, {position: 'fixed', left: Math.max(0, r.left - pad - 14) + 'px', top: Math.max(0, r.top - pad - 14) + 'px',
          width: '26px', height: '26px', borderRadius: '13px', background: '#e00', color: '#fff', font: 'bold 15px/26px sans-serif',
          textAlign: 'center', zIndex: 2147483647, pointerEvents: 'none'});
        document.body.append(box, tag); return true; };
      window.__find = (text, root = document) => [...root.querySelectorAll('a, button, .btn, span, div, li, label')].filter((e) => e.offsetParent
        && (e.innerText || '').trim() === text).sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)[0] || null;`;
    const result = await send("Runtime.evaluate", { expression: `(async () => { ${helper} const p = window.frappe && frappe.pearnly; ${step.js} })()`, awaitPromise: true, returnByValue: true, timeout: 60000 }, sessionId)
      .catch((error) => ({ exceptionDetails: { exception: { description: String(error) } } }));
    if (step.wait_ms) await sleep(step.wait_ms);
    if (step.shot && job.shots) {
      const shot = await send("Page.captureScreenshot", { format: "png" }, sessionId).catch(() => null);
      if (shot?.data) writeFileSync(join(job.shots, step.file ? `${step.file}.png` : `${index + 1}-${step.name.replace(/[^\w.-]+/g, "_").slice(0, 60)}.png`), Buffer.from(shot.data, "base64"));
    }
    console.log(JSON.stringify({ step: step.name, value: result.result?.value ?? null,
      error: result.exceptionDetails?.exception?.description?.slice(0, 300) || null, page_errors: [...logs] }));
  }
} catch (error) {
  console.log(JSON.stringify({ error: `driver: ${error.message}` }));
} finally {
  try { ws?.close(); } catch {}
  chrome.kill("SIGKILL");
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
