// 冒烟驱动：本机无头 Chrome（假摄像头放测试条码、自动允许相机）打开一页，跑一个探针，回 JSON。
// 用法：node scripts/smoke/drive.mjs '<JSON>'，JSON = {url, cookie_domain, sid, ua, probe, args, wait_ms, video}
//   probe：scripts/smoke/probes/<名>.js，内容是一个 async 函数体，拿得到 `args`，回一个对象（含 ok: true/false）。
// 页面上的未捕获异常、console.error 都收进 logs；探针本身不判异常，汇总时一律算不通过。
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
let output;
try {
  let info;
  for (let i = 0; i < 60 && !info; i++) {
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
      logs.push({ kind: "exception", text: String(details.exception?.description || details.text).slice(0, 600) });
    }
    if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") {
      logs.push({ kind: "console.error", text: message.params.args.map((a) => a.value ?? a.description).join(" ").slice(0, 400) });
    }
  });
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Runtime.enable", {}, sessionId);
  await send("Network.enable", {}, sessionId);
  if (job.ua) await send("Emulation.setUserAgentOverride", { userAgent: job.ua }, sessionId);
  if (job.sid) await send("Network.setCookie", { name: "sid", value: job.sid, domain: job.cookie_domain, path: "/", secure: true }, sessionId);
  await send("Page.navigate", { url: job.url }, sessionId);
  await sleep(job.wait_ms || 6000);
  const body = readFileSync(join(here, "probes", `${job.probe || "page"}.js`), "utf8");
  const expression = `(async (args) => {\n${body}\n})(${JSON.stringify(job.args || {})})`;
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, timeout: 90000 }, sessionId);
  output = { result: result.result?.value ?? null, error: result.exceptionDetails?.exception?.description || null, logs };
} catch (error) {
  output = { result: null, error: `driver: ${error.message}`, logs };
} finally {
  chrome.kill("SIGKILL");
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* 临时目录删不掉不影响结果 */ }
}
console.log(JSON.stringify(output));
process.exit(0);
