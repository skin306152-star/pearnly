// CC 画面比对：对一个构建产物，按环境 × 深浅色 × 场景各取「计算样式快照」与截图（docs/work/2026-10-17-cc-frontend-standards.md 5.2）。
//
//   node scripts/cc-visual/shoot.mjs --dist <产物目录> --out <输出目录>
//        [--env web,phone,…] [--scheme light,dark] [--scene welcome,…] [--jobs 4] [--chrome <路径>]
//
// · 本机 Google Chrome 无界面运行，直接走 CDP（Node 22 自带 WebSocket / fetch），不引入新依赖；
//   参数 --force-color-profile=srgb --hide-scrollbars，时区固定 Asia/Bangkok，页面 Date 固定到假服务端的 NOW，Math.random 定种子。
// · 每个场景：清空本机存储 → 预置偏好 → 打开 → 等网络安静与字体 → 做交互 → 停住时钟（再等一拍让按秒刷新的文字定下来）→
//   采样式快照（每个元素与 ::before / ::after 的外框与受管属性，含 transition / animation 的时长与曲线）→ 截图 → 再采一次快照，
//   两次不一样（触屏点按后补发的鼠标事件、菜单延时打开）就整组重来，最多 3 次。
//   过渡与动画在页面里一直归零（时序决定它们跑没跑、元素留不留在合成层，图层光栅会差），只在采快照那一刻放开并停住
//   （有限的结束、无限的停在 0），记下真实声明；减少动效场景不归零，记下截图前还在跑的动画（转圈要照常转）。
// · 环境设好后每个场景都从页面里读回宽度（matchMedia 精确到小数）、像素比、触屏与 UA，与环境表不一致整组失败（CDP 调用出错同样失败，
//   不再悄悄退回默认的 756×469）。小数宽度（web-767.5）CDP 不接受：外层整数视口里放一个 CSS zoom 的同源 iframe，里面的布局宽度正好是小数。
// · 每个场景可带「期望状态」（scenes.mjs 的 expect）：动作做完后要看得见的元素，没有就记进「动作没做完」；
//   点按 / 悬停的目标要看得见（透明度、visibility、pointer-events）且落点就是它，盖住或不接指针的同样记下。
// · Chrome 用 --remote-debugging-port=0，从本次新建的 profile 里的 DevToolsActivePort 读端口（连上的一定是自己起的那个）；
//   跑道出错、进程被 Ctrl+C / kill 时关掉所有已起的 Chrome；CDP 调用超时 60 秒。
// · 输出：<out>/<环境>-<深浅>/<场景>.json（快照）与 .png（截图），<out>/run.json（环境、每组的场景清单、动作没做完的、没模拟的接口）。
// · 构建产物：在 frontend/assistant 里 `./node_modules/.bin/vite build --outDir <目录> --emptyOutDir`（不写进仓库）。

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { NOW, start } from "./server.mjs";
import { ENVS, EXTRA_ENVS, SCHEMES, scenesFor } from "./scenes.mjs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

// 本进程起的所有 Chrome：出错退出、Ctrl+C、kill 时一并关掉，不留孤儿
const LIVE = new Set();
const killAll = () => {
  for (const proc of LIVE) try { proc.kill("SIGKILL"); } catch {}
  LIVE.clear();
};
process.on("exit", killAll);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => { killAll(); process.exit(130); });
process.on("uncaughtException", (error) => { killAll(); console.error(error); process.exit(1); });
process.on("unhandledRejection", (error) => { killAll(); console.error(error); process.exit(1); });

const CDP_TIMEOUT = 60000;

/** 起一个无界面 Chrome（profile 目录先清空），返回 { send, ev, shot, on, close }。send 遇到 CDP 报错或超时就 reject。 */
export async function launch(profile, chrome = process.env.CC_VISUAL_CHROME ?? CHROME) {
  rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  mkdirSync(profile, { recursive: true });
  const proc = spawn(chrome, [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars", "--force-color-profile=srgb",
    "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--disable-backgrounding-occluded-windows",
    "--font-render-hinting=none", "--disable-gpu", "about:blank",
  ], { stdio: "ignore" });
  LIVE.add(proc);
  const exited = new Promise((r) => proc.once("exit", r));
  // 杀掉并等主进程退出（再删 profile：子进程退出前还会往里写，立刻删会 ENOTEMPTY）
  const kill = async () => {
    try { proc.kill("SIGKILL"); } catch {}
    LIVE.delete(proc);
    if (proc.exitCode === null && proc.signalCode === null) await Promise.race([exited, sleep(5000)]);
  };
  let ws;
  try {
    // 端口由系统分配，Chrome 写进本 profile 的 DevToolsActivePort：profile 是刚清空的，读到的端口只能是这个进程的
    const portFile = join(profile, "DevToolsActivePort");
    for (let i = 0; i < 150 && !ws; i++) {
      if (proc.exitCode !== null) break;
      try {
        if (existsSync(portFile)) {
          const port = Number(readFileSync(portFile, "utf8").split("\n")[0]);
          const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
          const page = list.find((x) => x.type === "page");
          if (page) ws = new WebSocket(page.webSocketDebuggerUrl);
        }
      } catch {}
      if (!ws) await sleep(200);
    }
    if (!ws) throw new Error("Chrome 没起来：" + chrome);
    await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = () => fail(new Error("连不上 Chrome 的调试端口")); });
  } catch (error) {
    await kill();
    throw error;
  }
  let id = 0;
  const waits = new Map();
  const listeners = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waits.has(d.id)) {
      waits.get(d.id)(d);
      waits.delete(d.id);
    } else if (d.method) for (const l of listeners) l(d);
  };
  ws.onclose = () => {
    for (const [, done] of waits) done({ error: { message: "Chrome 的调试连接断了" } });
    waits.clear();
  };
  const send = (method, params = {}) => new Promise((ok, fail) => {
    const i = ++id;
    const timer = setTimeout(() => {
      waits.delete(i);
      fail(new Error(`CDP ${method} 超时（${CDP_TIMEOUT / 1000} 秒）`));
    }, CDP_TIMEOUT);
    waits.set(i, (d) => {
      clearTimeout(timer);
      if (d.error) fail(new Error(`CDP ${method}：${d.error.message}${d.error.data ? `（${d.error.data}）` : ""}`));
      else ok(d);
    });
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  const ev = async (expression, contextId) => {
    const out = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true, ...(contextId ? { contextId } : {}) });
    if (out.result?.exceptionDetails) throw new Error(JSON.stringify(out.result.exceptionDetails).slice(0, 600));
    return out.result?.result?.value;
  };
  // 连拍到两张一模一样为止（最多 6 张）：机器忙时合成器可能还没把图块画完（变换过的小图标抗锯齿会差 1），等它画稳再存
  const shot = async (path) => {
    let last = null;
    let data = null;
    for (let i = 0; i < 6; i++) {
      data = (await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false })).result.data;
      if (data === last) break;
      last = data;
      await sleep(250);
    }
    writeFileSync(path, Buffer.from(data, "base64"));
  };
  return {
    send, ev, shot, on: (f) => listeners.push(f),
    close: async () => {
      try { ws.close(); } catch {}
      await kill();
    },
  };
}

// 页面里先跑的：固定时钟、定种子的随机数、隐藏光标闪烁；快照与停动画的函数
const INIT = (now) => `(() => {
  const RealDate = Date;
  const t0 = performance.now();
  let frozen = null;
  const current = () => (frozen ?? (${now} + (performance.now() - t0)));
  class FixedDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(current()); else super(...a); }
    static now() { return current(); }
  }
  window.Date = FixedDate;
  window.__ccvFreezeClock = () => { frozen = ${now} + 60000; };
  let seed = 20261017;
  Math.random = () => { seed |= 0; seed = (seed + 0x6d2b79f5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  const style = () => { const s = document.createElement("style"); s.dataset.ccVisual = "";
    s.textContent = "*, *::before, *::after { caret-color: transparent !important; }"
      + " html:not([data-ccv-live]) *, html:not([data-ccv-live]) *::before, html:not([data-ccv-live]) *::after"
      + " { transition-duration: 0s !important; transition-delay: 0s !important; animation-duration: 0s !important; animation-delay: 0s !important; }";
    (document.head || document.documentElement).appendChild(s); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", style); else style();
})();`;

const PROPS = [
  "display", "position", "visibility", "opacity", "z-index", "box-sizing",
  "font-family", "font-size", "line-height", "font-weight", "font-style", "letter-spacing", "text-align", "white-space",
  "color", "background-color", "background-image",
  "border-top-width", "border-right-width", "border-bottom-width", "border-left-width", "border-top-style", "border-top-color",
  "border-top-left-radius", "border-top-right-radius", "border-bottom-right-radius", "border-bottom-left-radius",
  "box-shadow", "outline-width", "outline-style", "outline-color", "outline-offset",
  "padding-top", "padding-right", "padding-bottom", "padding-left",
  "margin-top", "margin-right", "margin-bottom", "margin-left", "row-gap", "column-gap",
  "transform", "translate", "scale", "rotate", "filter", "backdrop-filter",
  "transition-property", "transition-duration", "transition-timing-function", "transition-delay",
  "animation-name", "animation-duration", "animation-timing-function", "animation-delay", "animation-iteration-count",
  "overflow-x", "overflow-y", "text-overflow", "cursor", "stroke-width",
  "mask-image", "clip-path", "will-change", "contain", "isolation", "mix-blend-mode",
];

const SNAPSHOT = `window.__ccvSnapshot = () => {
  const PROPS = ${JSON.stringify(PROPS)};
  const r2 = (v) => Math.round(v * 100) / 100;
  const nodes = [];
  const label = (el) => {
    const cls = (typeof el.className === "string" ? el.className : el.getAttribute("class") || "").split(/\\s+/)
      .filter((c) => /^(cc-|aui-)/.test(c)).slice(0, 3).join(".");
    const slot = el.getAttribute("data-slot");
    return el.tagName.toLowerCase() + (cls ? "." + cls : "") + (slot ? "[" + slot + "]" : "");
  };
  const values = (cs) => PROPS.map((p) => cs.getPropertyValue(p));
  const walk = (el, path) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none") { nodes.push({ k: path, l: label(el), hidden: 1 }); return; }
    const r = el.getBoundingClientRect();
    // visibility:hidden 的只记样式不记外框：如输入框自动长高用的隐藏量尺（挂在 body 下，宽度跟最后量过的那个走，两次运行可能不同）
    const rect = cs.visibility === "hidden" ? undefined : [r2(r.left), r2(r.top), r2(r.width), r2(r.height)];
    nodes.push({ k: path, l: label(el), r: rect, v: values(cs) });
    for (const pseudo of ["::before", "::after"]) {
      const ps = getComputedStyle(el, pseudo);
      if (ps.content && ps.content !== "none" && ps.content !== "normal") nodes.push({ k: path + pseudo, l: label(el) + pseudo, v: values(ps), c: ps.content });
    }
    if (el.namespaceURI === "http://www.w3.org/2000/svg") return; // svg 只记根
    let i = 0;
    for (const child of el.children) {
      if (child.tagName === "SCRIPT" || child.tagName === "STYLE" || child.tagName === "LINK") continue;
      walk(child, path + ">" + child.tagName.toLowerCase() + ":" + i++);
    }
  };
  walk(document.body, "body");
  const root = document.documentElement;
  const attrs = {};
  for (const a of root.attributes) if ((a.name.startsWith("data-") && !a.name.startsWith("data-ccv")) || a.name === "class" || a.name === "lang") attrs[a.name] = a.value;
  return { props: PROPS, root: attrs, viewport: [innerWidth, innerHeight, devicePixelRatio], nodes };
};
window.__ccvFreezeMotion = () => {
  const running = [];
  for (const a of document.getAnimations()) {
    const name = a.animationName || a.transitionProperty || a.id || "animation";
    if (a.playState === "running") running.push(name);
    try {
      const timing = a.effect && a.effect.getComputedTiming();
      if (timing && timing.iterations === Infinity) { a.pause(); a.currentTime = 0; } else a.finish();
    } catch { try { a.pause(); a.currentTime = 0; } catch {} }
  }
  return running.sort();
};
window.__ccvEnv = () => ({
  variant: document.documentElement.dataset.ccVariant ?? null, w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
  coarse: matchMedia("(pointer: coarse)").matches, hoverNone: matchMedia("(hover: none)").matches,
  maxMd: matchMedia("(width < 768px)").matches, lang: document.documentElement.lang,
});`;

const QT_STUB = `window.qt = { webChannelTransport: {} };
const sig = () => ({ connect() {}, disconnect() {} });
window.tomoDesktop = {
  state(cb) { cb(JSON.stringify({ window: { frameless: true, maximized: false }, features: ["company_folders"], connected: true })); },
  call(method, json, cb) { cb(JSON.stringify({ ok: true })); },
  done: sig(), changed: sig(),
};`;

const MODIFIERS = { alt: 1, ctrl: 2, meta: 4, shift: 8 };

/** 一条跑道：一个环境 × 一种深浅色，自己一个假服务端、一个 Chrome；不论成败都关掉。 */
async function lane(job) {
  // 每条跑道自己一个假服务端（端口由系统分配）：挂起接口（加载骨架）、没模拟的接口互不干扰
  const server = await start({ dist: job.dist, port: 0 });
  let b = null;
  try {
    b = await launch(job.profile, job.chrome);
    return await runLane(job, server, b);
  } finally {
    await b?.close();
    await server.close();
  }
}

async function runLane({ envName, scheme, out, only }, server, b) {
  const env = ENVS[envName] ?? EXTRA_ENVS[envName];
  const base = `http://127.0.0.1:${server.port}`;
  // 小数宽度：外层整数视口 × zoom，页面放进同源 iframe（CSS zoom），iframe 里的布局宽度正好是 env.width
  const zoom = env.frame ?? 1;
  const notes = [];
  const logs = [];
  let inflight = 0;
  let lastNet = Date.now();
  const contexts = new Map(); // frameId → 默认执行上下文
  b.on((d) => {
    if (d.method === "Network.requestWillBeSent") { inflight++; lastNet = Date.now(); }
    if (d.method === "Network.loadingFinished" || d.method === "Network.loadingFailed") { inflight = Math.max(0, inflight - 1); lastNet = Date.now(); }
    if (d.method === "Runtime.exceptionThrown") logs.push("EXC " + JSON.stringify(d.params.exceptionDetails).slice(0, 300));
    if (d.method === "Runtime.executionContextCreated" && d.params.context.auxData?.isDefault) contexts.set(d.params.context.auxData.frameId, d.params.context.id);
    if (d.method === "Runtime.executionContextDestroyed") for (const [k, v] of contexts) if (v === d.params.executionContextId) contexts.delete(k);
    if (d.method === "Runtime.executionContextsCleared") contexts.clear();
  });
  await b.send("Runtime.enable");
  await b.send("Page.enable");
  await b.send("Network.enable");
  await b.send("Emulation.setDeviceMetricsOverride", {
    width: Math.ceil(env.width * zoom), height: Math.ceil(env.height * zoom), deviceScaleFactor: env.dpr / zoom, mobile: !!env.mobile,
  });
  if (env.touch) await b.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  if (env.ua) await b.send("Emulation.setUserAgentOverride", { userAgent: env.ua, platform: env.ua.includes("iPad") ? "iPad" : "iPhone" });
  await b.send("Emulation.setTimezoneOverride", { timezoneId: "Asia/Bangkok" });
  await b.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }, { name: "prefers-reduced-motion", value: "no-preference" }] });
  await b.send("Page.addScriptToEvaluateOnNewDocument", { source: INIT(NOW) + "\n" + SNAPSHOT });
  if (env.qt) await b.send("Page.addScriptToEvaluateOnNewDocument", { source: QT_STUB });

  // 页面里求值：iframe 环境进 iframe 的执行上下文
  const frameContext = async () => {
    for (let i = 0; i < 100; i++) {
      const tree = (await b.send("Page.getFrameTree")).result.frameTree;
      const child = tree.childFrames?.[0]?.frame.id;
      if (child && contexts.has(child)) return contexts.get(child);
      await sleep(100);
    }
    throw new Error("iframe 的执行上下文没出来");
  };
  const ev = async (expression) => (zoom === 1 ? b.ev(expression) : b.ev(expression, await frameContext()));
  const settle = async (min = 600, max = 8000) => {
    const begin = Date.now();
    await sleep(min);
    while (Date.now() - begin < max) {
      if (inflight === 0 && Date.now() - lastNet > 600) break;
      await sleep(100);
    }
    await ev("document.fonts.ready.then(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))").catch(() => {});
  };
  // 读回环境：宽度按 matchMedia 精确到小数，像素比、触屏、UA 都要对上，否则整组失败（不能悄悄退回默认视口）
  const checkEnv = async () => {
    const got = await ev(`({ width: matchMedia("(width: ${env.width}px)").matches, w: document.documentElement.getBoundingClientRect().width,
      h: innerHeight, dpr: devicePixelRatio, coarse: matchMedia("(pointer: coarse)").matches, ua: navigator.userAgent })`);
    const bad = [];
    if (!got.width) bad.push(`宽 ${got.w}（要 ${env.width}）`);
    if (got.h !== Math.floor(env.height)) bad.push(`高 ${got.h}（要 ${env.height}）`);
    if (got.dpr !== env.dpr) bad.push(`像素比 ${got.dpr}（要 ${env.dpr}）`);
    if (got.coarse !== !!env.touch) bad.push(`pointer: coarse ${got.coarse}（要 ${!!env.touch}）`);
    if (env.ua && got.ua !== env.ua) bad.push("UA 没换上");
    if (bad.length) throw new Error(`${envName}-${scheme} 环境没生效：${bad.join("，")}`);
  };
  // 点按 / 悬停的目标（第几个 = 外框非零的里面按文档顺序数）：还要看得见（visibility、整条祖先链的透明度）、接指针，
  // 而且中心点的落点就是它（或它里面）；否则记「点不到」——原来只按外框挑，触屏宽屏上透明不接指针的「···」也照点，点到的是行本身
  const find = (sel, n) => ev(`(() => {
    const els = [...document.querySelectorAll(${JSON.stringify(sel)})].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    const el = ${n} < 0 ? els[els.length + ${n}] : els[${n}];
    if (!el) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden") return { blocked: "它自己 visibility: hidden" };
    if (cs.pointerEvents === "none") return { blocked: "它自己 pointer-events: none" };
    for (let a = el; a; a = a.parentElement) if (Number(getComputedStyle(a).opacity) === 0) return { blocked: "透明度 0" };
    el.scrollIntoView({ block: "nearest", inline: "nearest" });
    const r = el.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    const hit = document.elementFromPoint(x, y);
    if (!hit || !(hit === el || el.contains(hit))) {
      const name = hit ? hit.tagName.toLowerCase() + (typeof hit.className === "string" && hit.className ? "." + hit.className.trim().split(/\\s+/).slice(0, 2).join(".") : "") : "空白";
      return { blocked: name };
    }
    return { x, y };
  })()`);
  const mouse = (type, x, y, modifiers = 0, buttons = 0) => b.send("Input.dispatchMouseEvent", {
    type, x, y, modifiers, button: type === "mouseMoved" ? (buttons ? "left" : "none") : "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1, pointerType: "mouse",
  });
  const touch = (type, x, y) => b.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y }] });

  const done = [];
  for (const scene of scenesFor(envName, scheme).filter((s) => !only || only.includes(s.name))) {
    const dir = join(out, `${envName}-${scheme}`);
    mkdirSync(dir, { recursive: true });
    await fetch(`${base}/__hold?clear=1`);
    await b.send("Page.navigate", { url: "about:blank" });
    // 光标挪出画面：上一个场景停在哪，不能带进这个场景成悬停
    await mouse("mouseMoved", -10, -10);
    await b.send("Storage.clearDataForOrigin", { origin: base, storageTypes: "all" });
    await b.send("Network.clearBrowserCache");
    const preset = Object.entries(scene.storage ?? {}).map(([k, v]) => `localStorage.setItem(${JSON.stringify(k)}, ${JSON.stringify(v)});`).join("");
    // 减少动效场景不做归零：转圈要照常转，报告里记下截图前还在跑的动画
    const live = scene.motion === "reduce" ? "document.documentElement.setAttribute('data-ccv-live', '');" : "";
    const presetId = preset || live || scene.init
      ? (await b.send("Page.addScriptToEvaluateOnNewDocument", { source: `try { if (location.origin === ${JSON.stringify(base)}) { ${preset} ${live} ${scene.init ?? ""} } } catch {}` })).result.identifier
      : null;
    // 先清再设（每个场景都从无覆盖开始；现在 CDP 出错会让整组失败）
    await b.send("Emulation.setLocaleOverride", {});
    await b.send("Emulation.setLocaleOverride", { locale: scene.lang === "th" ? "th-TH" : "zh-CN" });
    for (const [, method, ms] of scene.before ?? []) await fetch(`${base}/__hold?m=${encodeURIComponent(method)}&ms=${ms}`);
    const join_ = scene.path.includes("?") ? "&" : "?";
    const url = `${base}${scene.path}${join_}__lang=${scene.lang ?? "zh"}${scene.boot === "page" ? "&__boot=page" : ""}`;
    await b.send("Page.navigate", { url: zoom === 1 ? url : `${base}/__frame?w=${env.width}&h=${env.height}&z=${zoom}&src=${encodeURIComponent(url)}` });
    await settle(1500);
    await checkEnv();
    const missed = [];
    for (const action of scene.actions) {
      const [kind] = action;
      try {
        if (kind === "wait") { await sleep(action[1]); continue; }
        if (kind === "reload") { await b.send("Page.reload", {}); await settle(1200); continue; }
        if (kind === "key") {
          await b.send("Input.dispatchKeyEvent", { type: "keyDown", key: action[1], code: action[1] });
          await b.send("Input.dispatchKeyEvent", { type: "keyUp", key: action[1], code: action[1] });
          await sleep(300);
          continue;
        }
        if (kind === "attr") {
          await ev(`(() => { const r = document.documentElement; ${action[2] === null ? `r.removeAttribute(${JSON.stringify(action[1])})` : `r.setAttribute(${JSON.stringify(action[1])}, ${JSON.stringify(action[2])})`}; })()`);
          await sleep(300);
          continue;
        }
        if (kind === "hold") { await fetch(`${base}/__hold?m=${encodeURIComponent(action[1])}&ms=${action[2]}`); continue; }
        if (kind === "scroll") {
          const ok = await ev(`(() => { const el = document.querySelector(${JSON.stringify(action[1])}); if (!el) return false;
            el.scrollTop = ${action[2]} < 0 ? el.scrollHeight : ${action[2]}; el.dispatchEvent(new Event("scroll")); return true; })()`);
          if (!ok) missed.push(action.join(" "));
          await sleep(800);
          // 再滚一次：内容晚到时页面会自己跟到底，第二次之后位置才稳定
          await ev(`(() => { const el = document.querySelector(${JSON.stringify(action[1])}); if (el) el.scrollTop = ${action[2]} < 0 ? el.scrollHeight : ${action[2]}; })()`);
          await sleep(800);
          continue;
        }
        if (kind === "type") {
          const ok = await ev(`(() => { const el = document.querySelector(${JSON.stringify(action[1])}); if (!el) return false; el.focus(); return true; })()`);
          if (!ok) { missed.push(action.join(" ")); continue; }
          await b.send("Input.insertText", { text: action[2] });
          await sleep(400);
          continue;
        }
        if (kind === "paste") {
          // 往输入框粘贴一张图（输入框粘贴即加附件）：640×400 的两色块 PNG，在页面里用 canvas 画（纯色块，两次运行字节一样）
          const ok = await ev(`(async () => { const el = document.querySelector(${JSON.stringify(action[1])}); if (!el) return false; el.focus();
            const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 400;
            const g = canvas.getContext("2d"); g.fillStyle = "rgb(200,200,200)"; g.fillRect(0, 0, 640, 400); g.fillStyle = "rgb(90,120,160)"; g.fillRect(80, 80, 320, 200);
            const blob = await new Promise((r) => canvas.toBlob(r, "image/png"));
            const data = new DataTransfer(); data.items.add(new File([blob], ${JSON.stringify(action[2])}, { type: "image/png", lastModified: ${NOW} }));
            el.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true })); return true; })()`);
          if (!ok) { missed.push(action.join(" ")); break; }
          await settle(800, 3000);
          continue;
        }
        const found = await find(action[1], action[2] ?? 0);
        if (!found || found.blocked) {
          missed.push(`${kind} ${action[1]} [${action[2] ?? 0}]${found ? `：点不到（落在 ${found.blocked}）` : ""}`);
          break;
        }
        const c = { x: found.x * zoom, y: found.y * zoom };
        await sleep(120);
        if (kind === "hover") {
          await mouse("mouseMoved", c.x, c.y);
          await sleep(500);
        } else if (kind === "click") {
          if (env.touch && !action[3]) {
            await touch("touchStart", c.x, c.y);
            await sleep(40);
            await touch("touchEnd", c.x, c.y);
          } else {
            const mod = MODIFIERS[action[3]] ?? 0;
            await mouse("mouseMoved", c.x, c.y, mod);
            await mouse("mousePressed", c.x, c.y, mod, 1);
            await mouse("mouseReleased", c.x, c.y, mod);
          }
          await settle(700, 3000);
        } else if (kind === "longpress") {
          await touch("touchStart", c.x, c.y);
          await sleep(700);
          await touch("touchEnd", c.x, c.y);
          await settle(600, 3000);
        } else if (kind === "press" || kind === "drag") {
          const [, , , dx, dy] = action;
          await mouse("mouseMoved", c.x, c.y);
          await mouse("mousePressed", c.x, c.y, 0, 1);
          for (let i = 1; i <= 10; i++) {
            await mouse("mouseMoved", c.x + (dx * zoom * i) / 10, c.y + (dy * zoom * i) / 10, 0, 1);
            await sleep(30);
          }
          if (kind === "drag") await mouse("mouseReleased", c.x + dx * zoom, c.y + dy * zoom);
          await sleep(500);
        }
      } catch (error) {
        missed.push(`${action.join(" ")}：${String(error).slice(0, 120)}`);
      }
    }
    // 期望状态：动作「成功」不等于做到了（触屏宽屏点到行本身、菜单没开），看结果
    for (const sel of [scene.expect ?? []].flat()) {
      const ok = await ev(`(() => { const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return false;
        const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; })()`).catch(() => false);
      if (!ok) missed.push(`期望状态没出现：${sel}`);
    }
    // 停住时钟，再等一拍（按秒刷新的计时文字用停住的时刻重画），然后采快照、截图
    await ev("window.__ccvFreezeClock && window.__ccvFreezeClock()");
    await sleep(1100);
    await ev("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
    // 过渡与动画平时归零（跑没跑、停没停在合成层上取决于机器忙不忙，图层光栅会差）；采快照这一刻放开，记下真实的时长与曲线，
    // 放开后先把动画停住（有限的结束、无限的停在 0），快照里的透明度、变换才不随时刻变
    const capture = async () => {
      await ev("document.documentElement.setAttribute('data-ccv-live', '')");
      const running = await ev("window.__ccvFreezeMotion()");
      await ev("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
      const snap = await ev("window.__ccvSnapshot()");
      if (scene.motion !== "reduce") await ev("document.documentElement.removeAttribute('data-ccv-live')");
      if (scene.motion === "reduce") snap.running = running;
      return snap;
    };
    // 快照 → 截图 → 再快照：两次快照不一样（触屏点按后补发的鼠标事件、菜单延时打开、数据晚到）就等一会儿整组重来，最多 3 次；
    // 一直不稳的写进报告「动作没做完」一栏，场景要改得更稳
    let snapshot = null;
    for (let attempt = 0; attempt < 3; attempt++) {
      snapshot = await capture();
      await ev("window.__ccvFreezeMotion()");
      await sleep(300);
      await ev("new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))");
      await b.shot(join(dir, `${scene.name}.png`));
      const again = await capture();
      if (JSON.stringify(again.nodes) === JSON.stringify(snapshot.nodes)) break;
      if (attempt === 2) missed.push("快照与截图之间画面还在变（重试 3 次）");
      await sleep(800);
    }
    snapshot.scene = scene.name;
    snapshot.env = await ev("window.__ccvEnv()");
    snapshot.missed = missed;
    writeFileSync(join(dir, `${scene.name}.json`), JSON.stringify(snapshot));
    if (presetId) await b.send("Page.removeScriptToEvaluateOnNewDocument", { identifier: presetId });
    if (missed.length) notes.push({ lane: `${envName}-${scheme}`, scene: scene.name, missed });
    done.push(scene.name);
  }
  return { lane: `${envName}-${scheme}`, scenes: done, notes, logs: logs.slice(0, 20), unmocked: [...server.unmocked] };
}

/** 跑一遍：任一条跑道出错就不再开新跑道，等已开的收尾（各自关掉 Chrome 与假服务端）后报错，不写 run.json。 */
export async function shoot({ dist, out, envs, schemes, only, jobs = 4, chrome, quiet = false }) {
  out = resolve(out);
  rmSync(out, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  mkdirSync(out, { recursive: true });
  const lanes = [];
  for (const envName of envs) for (const scheme of schemes) lanes.push({ envName, scheme });
  const results = [];
  const errors = [];
  let next = 0;
  const profiles = join(out, ".profiles");
  const worker = async (slot) => {
    while (next < lanes.length && !errors.length) {
      const job = lanes[next++];
      const begin = Date.now();
      try {
        const result = await lane({ ...job, dist, out, profile: join(profiles, String(slot)), only, chrome });
        if (!quiet) console.log(`${result.lane}: ${result.scenes.length} 个场景，${((Date.now() - begin) / 1000).toFixed(0)} 秒${result.notes.length ? `，${result.notes.length} 个场景动作没做完` : ""}`);
        results.push(result);
      } catch (error) {
        errors.push(`${job.envName}-${job.scheme}：${error?.message ?? error}`);
      }
    }
  };
  try {
    await Promise.all(Array.from({ length: Math.min(jobs, lanes.length) }, (_, i) => worker(i)));
  } finally {
    rmSync(profiles, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
  if (errors.length) throw new Error(`有跑道失败，这一遍作废（没写 run.json）：\n${errors.join("\n")}`);
  const run = {
    dist: resolve(dist), at: new Date().toISOString(), envs, schemes, only,
    lanes: results.sort((a, b) => a.lane.localeCompare(b.lane)),
    unmocked: [...new Set(results.flatMap((r) => r.unmocked))].sort(),
  };
  writeFileSync(join(out, "run.json"), JSON.stringify(run, null, 1));
  return run;
}

function args(argv) {
  const get = (name, fallback) => {
    const i = argv.indexOf(name);
    return i >= 0 ? argv[i + 1] : fallback;
  };
  const list = (name, fallback) => (get(name) ? get(name).split(",").filter(Boolean) : fallback);
  return {
    dist: get("--dist"), out: get("--out"), envs: list("--env", Object.keys(ENVS)), schemes: list("--scheme", SCHEMES),
    only: get("--scene") ? list("--scene") : null, jobs: Number(get("--jobs", 4)), chrome: get("--chrome"),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const a = args(process.argv.slice(2));
  if (!a.dist || !a.out || !existsSync(join(a.dist, "index.html"))) {
    console.error("用法：node scripts/cc-visual/shoot.mjs --dist <产物目录> --out <输出目录> [--env …] [--scheme …] [--scene …] [--jobs 4]");
    process.exit(2);
  }
  const begin = Date.now();
  try {
    const run = await shoot(a);
    const shots = run.lanes.reduce((n, l) => n + l.scenes.length, 0);
    console.log(`完成：${run.lanes.length} 组 × 场景共 ${shots} 张，${((Date.now() - begin) / 1000).toFixed(0)} 秒；没模拟的接口 ${run.unmocked.length} 个：${run.unmocked.join(", ")}`);
    process.exit(0);
  } catch (error) {
    console.error(String(error?.message ?? error));
    process.exit(1);
  }
}
