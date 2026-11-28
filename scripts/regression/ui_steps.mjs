// 页面操作步骤驱动（回归实验室复核用）：一个无头 Chrome 带会话打开起始页，按顺序在页面里执行若干段 JS（可 await），
// 每步输出一行 JSON（步骤名、返回值、这一步里的页面报错），可选截图。不点真实鼠标，步骤里直接调框架 / 产品的同一套函数。
// 用法：node scripts/regression/ui_steps.mjs '<JSON>'，JSON = {base, cookie_domain, sid, start, ready, preload, shots, mobile, width, video, steps: [{name, js, wait_ms, shot, full, file}]}
// width：手机宽度（默认 390）；full：截图时把整张表单截全；height：手机宽度时这一步之前把视口高度改成它（模拟安卓键盘弹出整页变矮，0 = 还原 844）
// 实验室站点的域名：SMOKE_CHROME_RULES="MAP erp.regression.localhost 127.0.0.1"
import { DEVTOOLS_PORT_FLAG, devtoolsVersion } from "../smoke/devtools.mjs";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// `@<文件>`：JSON 放在文件里（run.py 这样传）。Linux 单个命令行参数最长 128 KB，步骤多的流程（cowork.recon.run）直接传会报
// Argument list too long（11-16 GitHub 机器上；Mac 没有单参数上限才没暴露）
const arg = process.argv[2] || "{}";
const job = JSON.parse(arg.startsWith("@") ? readFileSync(arg.slice(1), "utf8") : arg);
const chromePath = process.env.SMOKE_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const profile = mkdtempSync(join(process.env.TMPDIR || tmpdir(), "steps-"));
// 假摄像头：job.video 是 make_barcode_video.py 写的 y4m（条码画面）；不给就用 Chrome 自带的测试画面
const camera = ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", ...(job.video ? [`--use-file-for-fake-video-capture=${job.video}`] : [])];
const chrome = spawn(chromePath, ["--headless=new", DEVTOOLS_PORT_FLAG, `--user-data-dir=${profile}`, ...camera,
  ...(process.env.SMOKE_CHROME_RULES ? [`--host-resolver-rules=${process.env.SMOKE_CHROME_RULES}`] : []),
  // 静音但允许页面自己放提示音：步骤里直接调函数、脚本点按钮都不算真人手势，否则保存成功那一声 play() 被浏览器拦下记成页面报错。
  // 只为「脚本点击不算手势」服务：它也会放过「不是用户触发的提示音（例如实时推送来的）被浏览器拦下」这类真问题，那类要在真机上看。
  "--mute-audio", "--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check", "--window-size=1280,900", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const logs = [];
let ws, seq = 0;
const pending = new Map();
// 每个调试调用都有上限：Chrome 卡住或调试连接断开时，挂起的调用不再无限等下去（11-12 满载时整格卡满 run.py 的 900 秒，
// 杀掉时也看不出卡在哪一步）。超时 / 断开就带着调用名失败，外面按步骤记下、整格停下
const send = (method, params = {}, sessionId, ms = 60000) => {
  const message = { id: ++seq, method, params, ...(sessionId ? { sessionId } : {}) };
  if (!ws || ws.readyState !== 1) return Promise.reject(new Error(`debug connection closed before ${method}`));
  ws.send(JSON.stringify(message));
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(message.id); reject(new Error(`${method} timed out after ${ms / 1000}s`)); }, ms);
    pending.set(message.id, { resolve: (value) => { clearTimeout(timer); resolve(value); },
      reject: (error) => { clearTimeout(timer); reject(error); } });
  });
};
const STEP_MS = 300000;  // 一步页面脚本最多跑这么久（最慢的流程步骤约 100 秒）
try {
  const info = await devtoolsVersion(profile, chrome);
  if (!info) throw new Error(`Chrome did not start: ${chromePath}`);
  ws = new WebSocket(info.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener("open", resolve); setTimeout(() => reject(new Error("debug connection did not open")), 30000); });
  // 连接断开（Chrome 崩溃 / 被杀）：挂着的调用全部立即失败
  ws.addEventListener("close", () => { for (const [, call] of pending) call.reject(new Error("debug connection closed")); pending.clear(); });
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
  // 页面自己的脚本跑之前先注入的一段（流程模块的 PRELOAD）：例如把某个接口放慢、从第一帧起采样页面，看加载途中的样子
  if (job.preload) await send("Page.addScriptToEvaluateOnNewDocument", { source: job.preload }, sessionId);
  await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: false }, sessionId);
  // 手机宽度：job.mobile 为真时按安卓手机模拟（390 × 844、触屏、安卓 Chrome 的 UA），页面按手机判
  if (job.mobile) {
    await send("Emulation.setDeviceMetricsOverride", { width: job.width || 390, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }, sessionId);
    await send("Emulation.setUserAgentOverride", { userAgent: "Mozilla/5.0 (Linux; Android 14; SM-A556E) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36" }, sessionId);
  }
  await send("Page.navigate", { url: new URL(job.start || "/desk", job.base).href }, sessionId);
  for (let i = 0; i < 100; i++) {
    // 页面就绪：默认等 Desk 起好；不是 Desk 的页面（LINE 手机盘点页等）由 job.ready 给自己的条件
    const ready = job.ready || "!!(window.frappe && frappe.boot && frappe.router && document.readyState === 'complete' && window.frappe.pearnly)";
    const ok = (await send("Runtime.evaluate", { expression: ready, returnByValue: true }, sessionId).catch(() => ({})))?.result?.value;
    if (ok) break;
    await sleep(300);
  }
  await sleep(1500);
  for (const [index, step] of (job.steps || []).entries()) {
    logs.length = 0;
    console.log(JSON.stringify({ progress: step.name }));  // 被外面整格杀掉时，run.py 据此说卡在哪一步
    if (job.mobile && step.height !== undefined) {
      await send("Emulation.setDeviceMetricsOverride", { width: job.width || 390, height: step.height || 844, deviceScaleFactor: 2, mobile: true }, sessionId);
    }
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
    const result = await send("Runtime.evaluate", { expression: `(async () => { ${helper} const p = window.frappe && frappe.pearnly; ${step.js} })()`, awaitPromise: true, returnByValue: true, timeout: 60000 }, sessionId, STEP_MS)
      .catch((error) => ({ exceptionDetails: { exception: { description: String(error) } }, driverError: /timed out|connection/.test(String(error)) }));
    if (step.wait_ms) await sleep(step.wait_ms);
    if (step.shot && job.shots) {
      // step.full：整张表单截全（弹窗 / 页面比一屏长时把视口临时拉到内容的高度，截完还原）
      const tall = step.full ? (await send("Runtime.evaluate", { expression: `(() => { const m = [...document.querySelectorAll('.modal.show .modal-dialog')].pop();
        const main = document.querySelector('.main-section');  // Desk 页面在这一层里滚
        return Math.ceil(Math.max(document.documentElement.scrollHeight, m ? m.getBoundingClientRect().bottom + 40 : 0,
          main ? main.getBoundingClientRect().top + main.scrollHeight : 0)); })()`, returnByValue: true }, sessionId)
        .catch(() => ({})))?.result?.value : 0;
      const width = job.mobile ? (job.width || 390) : 1280;
      if (tall > (job.mobile ? 844 : 900)) {
        await send("Emulation.setDeviceMetricsOverride", { width, height: Math.min(tall, 6000), deviceScaleFactor: job.mobile ? 2 : 1, mobile: !!job.mobile }, sessionId);
        await sleep(400);
      }
      const shot = await send("Page.captureScreenshot", { format: "png" }, sessionId).catch(() => null);
      if (tall > (job.mobile ? 844 : 900)) {
        if (job.mobile) await send("Emulation.setDeviceMetricsOverride", { width, height: 844, deviceScaleFactor: 2, mobile: true }, sessionId);
        else await send("Emulation.clearDeviceMetricsOverride", {}, sessionId);
      }
      if (shot?.data) writeFileSync(join(job.shots, step.file ? `${step.file}.png` : `${index + 1}-${step.name.replace(/[^\w.-]+/g, "_").slice(0, 60)}.png`), Buffer.from(shot.data, "base64"));
    }
    // ERP / Cowork 的字段说明收在标题旁的说明图标里（`pearnly_core/public/desk/field-help.js`）：每一步后看还有没有写在字段下方的，
    // 登记的例外（`data-pearnly-description="inline"`）与密码强度提示不算；顺带记下已在图标里的（运行时清单）
    const help = await send("Runtime.evaluate", { expression: `(() => { if (!(window.frappe && frappe.boot && frappe.boot.pearnly_field_help === "icon")) return {};
      const seen = (e) => e.getClientRects().length > 0;
      const name = (e) => { const c = e.closest(".frappe-control"); return (c && c.dataset.fieldname) || "?"; };
      return {below: [...document.querySelectorAll(".frappe-control .help-box, .grid-description, .form-section-description")]
          .filter((e) => seen(e) && (e.innerText || "").trim() && !e.closest('[data-pearnly-description="inline"], [data-fieldtype="Password"]'))
          .map((e) => name(e) + ": " + (e.innerText || "").trim().slice(0, 80)),
        icons: [...document.querySelectorAll(".pearnly-field-help")].filter(seen).map((e) => name(e) + ": " + (e.getAttribute("aria-label") || ""))}; })()`,
      returnByValue: true }, sessionId).catch(() => ({}));
    // CC 手机页任何画面都不许横向滚动（11-28 iPhone：看不见的测量层把对话区撑到 1000 多 px，整页能左滑走）：根节点，以及
    // 占半屏以上、能横滚的滚动区（对话区这类整页区域；标签条这类刻意横滚的窄条、回复里的表格与代码块不算），可滚宽度都不得大于自身宽度
    const hscroll = job.mobile ? await send("Runtime.evaluate", { expression: `(() => { if (!location.pathname.startsWith("/assistant")) return [];
      const out = []; const root = document.documentElement;
      if (root.scrollWidth > root.clientWidth + 1) out.push("document " + root.scrollWidth + " > " + root.clientWidth);
      for (const el of document.querySelectorAll("body *")) {
        if (el.clientHeight < innerHeight / 2 || el.scrollWidth <= el.clientWidth + 1) continue;
        if (!/auto|scroll/.test(getComputedStyle(el).overflowX) || el.closest(".aui-md, pre")) continue;  // 回复里的表格、代码块本就横滚
        out.push((el.getAttribute("data-slot") || el.tagName.toLowerCase() + "." + String(el.className).split(" ")[0]) + " " + el.scrollWidth + " > " + el.clientWidth);
      }
      return out; })()`, returnByValue: true }, sessionId).catch(() => ({})) : null;
    console.log(JSON.stringify({ step: step.name, value: result.result?.value ?? null,
      error: result.exceptionDetails?.exception?.description?.slice(0, 300) || null, page_errors: [...logs],
      field_help: help?.result?.value?.below || [], field_icons: help?.result?.value?.icons || [],
      hscroll: hscroll?.result?.value || [] }));
    // 页面脚本本身超时或调试连接断了：后面的步骤都跑不了，整格停下（不再耗到外面的总超时）
    // 顺带探一下卡在哪一层：页面还能不能跑一句最简单的脚本（不能 = 页面自己忙死，例如脚本死循环，算产品问题）、浏览器本身还应不应答
    if (result.driverError) {
      const page = await send("Runtime.evaluate", { expression: "1", returnByValue: true }, sessionId, 5000).then(() => "page answers").catch(() => "page hung");
      const browser = await send("Browser.getVersion", {}, undefined, 5000).then(() => "browser answers").catch(() => "browser hung");
      throw new Error(`step "${step.name}": ${result.exceptionDetails.exception.description} (${page}, ${browser})`);
    }
  }
} catch (error) {
  console.log(JSON.stringify({ error: `driver: ${error.message}` }));
} finally {
  try { ws?.close(); } catch {}
  chrome.kill("SIGKILL");
  await sleep(300);
  rmSync(profile, { recursive: true, force: true });
}
