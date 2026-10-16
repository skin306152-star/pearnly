// 桌面端（companion/ 里的 Tomo 窗口）和网页一起用的三件事，浏览器里打开时也照常生效：
//
//   window.tomo.ask(text) — 桌面端快捷键呼出的输入条把文字交给这里：开一个新对话发出去（空文字只开新对话）；
//                           Ctrl+N、托盘「新对话」、快捷提问都走这里，新对话一律开在「最近」
//   回复完成通知          — 窗口不在前台时回复完成，弹系统通知（桌面端已授权；浏览器只在用户授权过时弹，经后台脚本弹，
//                           见 lib/notify.ts），点了打开这段对话
//   整窗拖文件            — 文件拖到对话页任何地方都加进输入框，不会被浏览器当成网页打开
//   window.tomo.settings  — 桌面端托盘「连接与设置」/ Ctrl+, 打开设置
//   window.tomo.open(id)  — 桌面端托盘通知点开：打开那段对话（空串 = 定时任务页），由 lib/nav.ts 的深链接接手
//   提醒通知              — 本站提醒（定时任务提醒、完成简报）：桌面端窗口里弹系统通知（同一条与托盘按 notice:编号 去重）；
//                           浏览器里开了推送就由后台脚本弹，没开才在页面不在前台时弹

import { useAui } from "@assistant-ui/react";
import { useEffect, useRef, useState } from "react";
import { lang } from "@/i18n";
import { onNotice } from "@/lib/managed-agents/activity";
import { setDraft } from "@/lib/managed-agents/projects";
import { deepLink, showNote } from "@/lib/notify";
import { APP_NAME } from "@/lib/profile";

type Message = { role?: string; content?: unknown };

declare global {
  interface Window {
    tomo?: { ask: (text: string) => void; settings: (section?: string) => void; open: (target: string) => void };
  }
}

/** 打开设置（section：general / connections / system）；设置窗口在 components/settings.tsx 里听这个事件。 */
export function openSettings(section = "") {
  window.dispatchEvent(new CustomEvent("tomo-settings", { detail: section }));
}

/** 打开某段对话（target 是对话编号，空串 = 定时任务页，「/assistant?…」是深链接）；lib/nav.ts 的 useDeepLinks 听这个事件。 */
export function openTarget(target: string) {
  window.dispatchEvent(new CustomEvent("tomo-open", { detail: String(target ?? "") }));
}

function lastReply(messages: readonly Message[]): string {
  const last = [...messages].reverse().find((m) => m.role === "assistant");
  const parts = Array.isArray(last?.content) ? (last.content as Array<{ type?: string; text?: string }>) : [];
  const text = parts.filter((p) => p.type === "text").map((p) => p.text ?? "").join(" ");
  const flat = text.replace(/[#*_`>|-]+/g, " ").replace(/\s+/g, " ").trim();
  return flat.length > 120 ? `${flat.slice(0, 119)}…` : flat;
}

/** pushOn：这台浏览器开着推送通知（lib/push.ts），提醒交给后台脚本弹；sessionId：当前对话（回复完成通知点开它）。 */
export function useDesktopBridge(
  isRunning: boolean,
  messages: readonly Message[],
  title: string,
  pushOn = false,
  sessionId: string | null = null,
) {
  const aui = useAui();
  const pushOnRef = useRef(pushOn);
  useEffect(() => {
    pushOnRef.current = pushOn;
  }, [pushOn]);

  useEffect(() => {
    window.tomo = {
      ask(text: string) {
        const value = String(text ?? "").trim();
        setDraft({});
        aui.threads().switchToNewThread();
        if (!value) return;   // 只开新对话（桌面端菜单「新对话」）
        // 新对话切过去后下一帧才是当前对话，再把文字放进输入框发出
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const composer = aui.thread().composer();
            composer.setText(value);
            composer.send();
          }),
        );
      },
      settings: (section?: string) => openSettings(section ?? ""),
      open: (target: string) => openTarget(target),
    };
    return () => {
      delete window.tomo;
    };
  }, [aui]);

  const wasRunning = useRef(isRunning);
  useEffect(() => {
    const finished = wasRunning.current && !isRunning;
    wasRunning.current = isRunning;
    if (!finished || document.hasFocus()) return;
    void showNote(title || APP_NAME, lastReply(messages), "tomo-reply", sessionId ? deepLink(sessionId) : "/assistant");
  }, [isRunning, messages, title, sessionId]);

  useEffect(
    () =>
      onNotice((notice) => {
        // 桌面端：一律弹（托盘同一条按 tag 只弹一次）；浏览器：开了推送由后台脚本弹，没开且页面不在前台才弹
        const desktopApp = isDesktopApp();
        if (!desktopApp && (pushOnRef.current || document.hasFocus())) return;
        const link = deepLink(notice.session || "", notice.view || "");
        void showNote(notice.title || APP_NAME, notice.text, `notice:${notice.id}`, link, () => openTarget(notice.session || link));
      }),
    [],
  );

  useEffect(() => {
    // 拖着文件进窗口时整窗出提示（同 claude.ai，样式 .cc-dropzone）；进出子元素会成对触发 enter / leave，按计数判断真正离开
    let depth = 0;
    const root = document.documentElement;
    const show = (on: boolean) => (on ? root.setAttribute("data-cc-dropping", "") : root.removeAttribute("data-cc-dropping"));
    const enter = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      depth += 1;
      show(true);
    };
    const leave = (e: DragEvent) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      depth = Math.max(0, depth - 1);
      if (!depth) show(false);
    };
    const over = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    };
    const drop = (e: DragEvent) => {
      depth = 0;
      show(false);
      if (!e.dataTransfer?.files.length) return;
      // 拖到输入框上的已由输入框自己收下（它会标记 defaultPrevented），这里只收其他地方的
      const handled = e.defaultPrevented;
      e.preventDefault();
      if (handled) return;
      for (const file of Array.from(e.dataTransfer.files)) void aui.thread().composer().addAttachment(file);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      show(false);
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, [aui]);
}

// ---------------------------------------------------------------------------
// 桌面端的本机设置（设置里「此电脑」一栏）：Tomo 窗口经 QWebChannel 放进 window.tomoDesktop
// （companion/src/companion/tomo_bridge.py）；浏览器里没有，这一栏就不显示。

export type MemberStatus = "online" | "processing" | "attention" | "offline" | "connecting" | "paused";
/** device_state（CC 桌面版 1.7.0，ADR-021）：服务端说这个成员的密钥不再用——superseded（这台电脑在这条连接上已有自己的
 *  密钥）、claimed_elsewhere（同一配对码抄到了另一台电脑）；这个成员停着，等对话页重新登记。旧版没有这一项。 */
export type DesktopMember = {
  id: string; name: string; kind: string; paused: boolean; status: MemberStatus; folders: string[] | null; device_state?: string;
};
export type DesktopState = {
  version: string;
  site: string; // 这台电脑连的公司（站点域名），没连是空
  express_roots: string[];
  members: DesktopMember[];
  autostart: boolean;
  hotkey: boolean;
  update: { state: string; version: string };
  features?: string[]; // "company_folders"：文件夹随公司选（pick_folder）；1.6.0 以前的桌面端没有；"devices"：多台电脑（1.7.0）
  legacy_roots?: LegacyRoot[];
  device?: { key?: string; name?: string }; // 这台电脑的设备密钥与电脑名（1.7.0，features 含 devices），对话页登记时带上
};
/** 以前在设置里授权、现在没有公司和任务在用的根目录（设置里「以前授权的文件夹」一行清理用）。 */
export type LegacyRoot = { member: string; id: string; path: string };
export type DesktopResult = { ok: boolean; message?: string; cancelled?: boolean; roots?: string[]; other_site?: string };

type Signal<A extends unknown[]> = { connect(cb: (...args: A) => void): void; disconnect(cb: (...args: A) => void): void };
type Channel = {
  state(cb: (json: string) => void): void;
  call(method: string, args: string, cb: (json: string) => void): void;
  changed: Signal<[]>;
  done: Signal<[string, string]>;
};

declare global {
  interface Window {
    qt?: { webChannelTransport?: unknown };
    tomoDesktop?: Channel;
  }
}

export type Desktop = {
  state(): Promise<DesktopState>;
  call(method: string, args?: Record<string, unknown>): Promise<DesktopResult>;
  onChanged(cb: () => void): () => void;
};

function wrap(channel: Channel): Desktop {
  // 慢的操作（配对、换 Express 文件夹、找 Express）：先回 job，做完发 done。done 可能比 job 编号先到（很快做完时），
  // 所以一直听着：先到的结果先存起来，拿到编号再取（2026-10-10 测试机选本地小文件夹后一直「正在查找…」）。
  const early = new Map<string, string>();
  const waiting = new Map<string, (result: string) => void>();
  channel.done.connect((job, result) => {
    const deliver = waiting.get(job);
    if (!deliver) return void early.set(job, result);
    waiting.delete(job);
    deliver(result);
  });
  return {
    state: () => new Promise((resolve) => channel.state((json) => resolve(JSON.parse(json)))),
    call: (method, args = {}) =>
      new Promise((resolve) => {
        channel.call(method, JSON.stringify({ ...args, lang }), (json) => {
          const first = JSON.parse(json) as DesktopResult & { job?: string };
          if (!first.job) return resolve(first);
          const result = early.get(first.job);
          if (result !== undefined) {
            early.delete(first.job);
            return resolve(JSON.parse(result));
          }
          waiting.set(first.job, (text) => resolve(JSON.parse(text)));
        });
      }),
    onChanged(cb) {
      channel.changed.connect(cb);
      return () => channel.changed.disconnect(cb);
    },
  };
}

let pending: Promise<Desktop | null> | null = null;

/** 在 Tomo 桌面端里返回本机设置的接口；浏览器里返回 null。 */
export function desktop(): Promise<Desktop | null> {
  if (!window.qt?.webChannelTransport) return Promise.resolve(null);
  pending ??= new Promise((resolve) => {
    if (window.tomoDesktop) return resolve(wrap(window.tomoDesktop));
    const ready = () => window.tomoDesktop && resolve(wrap(window.tomoDesktop));
    window.addEventListener("tomo-desktop", ready, { once: true });
    setTimeout(() => resolve(window.tomoDesktop ? wrap(window.tomoDesktop) : null), 5000);
  });
  return pending;
}

// ---------------------------------------------------------------------------
// 公司的文件夹（ADR-020）：只在 CC 桌面版里经 Windows 原生选择框选，页面不传任何路径（桥也不收）。选好的 binding
// 原样交给服务端（lib/companies.ts 的 bindFolder → companies.api.folder_bind）。1.6.0 以前的桌面端没有 company_folders，
// 页面提示更新。

export const COMPANY_FOLDERS = "company_folders";
/** 多台电脑（ADR-021，CC 桌面版 1.7.0）：state().device 有设备密钥、成员带 device_state、remove 先吊销本机这把密钥（回 job）。 */
export const DEVICES = "devices";

/** 桥给的绑定（reader / root_id / folder / label / share / device），页面不读也不改，原样转交。 */
export type FolderBinding = { reader: string; root_id: string; folder: string; label: string; share: string; device: string };
export type PickCode = "not_connected" | "other_site" | "program_folder" | "too_broad" | "contains_existing" | "roots_full";
export type PickResult =
  | { ok: true; binding: FolderBinding; name: string; created: boolean; reported: boolean }
  | { ok: false; cancelled: true }
  | { ok: false; cancelled?: false; code: PickCode; message: string };

/** 弹原生选择框选一个公司文件夹；浏览器里回 null。 */
export async function pickFolder(): Promise<PickResult | null> {
  const api = await desktop();
  if (!api) return null;
  return (await api.call("pick_folder")) as unknown as PickResult;
}

/** 在不在 CC 桌面版里；这个桌面版能不能随公司选文件夹（state().features）；这台电脑的电脑名（1.7.0，同绑定里带的 device，
 *  主档页认「本机这一行」：同一条连接上几台电脑各有一行时，连接相同还要电脑名相同）。 */
export type DesktopFeatures = { app: boolean; companyFolders: boolean; deviceName?: string };

let features: DesktopFeatures | null = null;
let readingFeatures: Promise<DesktopFeatures> | null = null;

/** 读一次桥的 state().features（浏览器里直接回不在桌面版；读不到当旧桌面版）。读过就记住。 */
export function loadDesktopFeatures(): Promise<DesktopFeatures> {
  if (features) return Promise.resolve(features);
  if (!isDesktopApp()) return Promise.resolve((features = { app: false, companyFolders: false }));
  readingFeatures ??= desktop()
    .then((api) => api?.state())
    .then((state) => ({
      app: true,
      companyFolders: !!state?.features?.includes(COMPANY_FOLDERS),
      ...(state?.features?.includes(DEVICES) ? { deviceName: String(state.device?.name ?? "") } : {}),
    }))
    .catch(() => ({ app: true, companyFolders: false }))
    .then((value) => (features = value));
  return readingFeatures;
}

// 本机读文件夹用的连接（桥给的 binding.reader）：选过一次就记在这台电脑上，主档页据此认出「本机这一行」
// （服务端 detail 给了 mine 就以它为准）。只是界面上认行，权限仍由服务端按连接判。
const READERS_KEY = "cc.desktop.readers";

export function knownReaders(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(READERS_KEY) || "[]") as unknown;
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

export function rememberReader(reader: string) {
  if (!reader || knownReaders().includes(reader)) return;
  try {
    localStorage.setItem(READERS_KEY, JSON.stringify([...knownReaders(), reader].slice(-10)));
  } catch {
    // 存不了：这次照样能用，下次靠服务端的 mine
  }
}

export function useDesktopFeatures(): DesktopFeatures {
  const [value, setValue] = useState<DesktopFeatures>(features ?? { app: isDesktopApp(), companyFolders: false });
  useEffect(() => {
    let alive = true;
    void loadDesktopFeatures().then((found) => alive && setValue(found));
    return () => {
      alive = false;
    };
  }, []);
  return value;
}

// ---------------------------------------------------------------------------
// 用登录的账号连上这台电脑（2026-10-10 起取代手抄配对码）：桌面端打开对话页时自动做一次，
// 本站按账号权限发一把只交给本机的密钥（pearnly_cowork.companion.api.register），桌面端拿它静默配对。
// 只在 Cowork 站点上做；已连着这家公司不重复做；已连着别家、或这条连接正被另一台电脑用着，
// 都不自动抢，等用户在设置里点。

export type Link =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "in_use"; generation: number }
  | { kind: "other_site"; site: string }
  | { kind: "not_admin" }
  | { kind: "no_connection" }
  | { kind: "update_needed" }
  | { kind: "removed" }
  | { kind: "failed"; message: string };

let link: Link = { kind: "idle" };
const listeners = new Set<(value: Link) => void>();

function setLink(value: Link) {
  link = value;
  for (const cb of listeners) cb(value);
}

export function currentLink(): Link {
  return link;
}

export function onLink(cb: (value: Link) => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

type Registered =
  | { status: "created"; token: string }
  | { status: "in_use"; generation: number }
  | { status: "no_connection" }
  | { status: "update_needed" | "exists" | "removed"; token?: undefined };

/** 这台电脑（CC 桌面版 1.7.0 起，state().features 含 devices）：设备密钥与电脑名；旧版、取不到是 null（走一条连接一个槽位的旧路）。 */
export type ThisComputer = { key: string; name: string } | null;

export function thisComputer(state: Pick<DesktopState, "features" | "device"> | null | undefined): ThisComputer {
  const key = String(state?.device?.key ?? "");
  return state?.features?.includes(DEVICES) && key ? { key, name: String(state.device?.name ?? "") } : null;
}

type RegisterOptions = { replace?: number; device?: ThisComputer; rekey?: boolean; rejoin?: boolean };

async function register(express: boolean, { replace, device, rekey, rejoin }: RegisterOptions = {}): Promise<Registered | Link> {
  // register v2（ADR-021）：带设备密钥时每台电脑在池里各有自己的密钥、彼此不顶替（replace 只属于旧路）；rekey = 这台电脑
  // 已有密钥、本机却没有能用的成员（重装过、被服务端说不再用），换一把新的。每次都用新的请求号：同一个请求号重放 rekey
  // 只回 exists、不带新密钥（第 1 批审查小 4）。rejoin = 这台电脑被「电脑」列表移除过，人在设置里亲手点了「连接」：
  // 只有这时服务端才让它作为新电脑回来，打开页面的自动登记只回 removed（第 6 批审查第 2 条）。设备密钥只进请求体，不进地址栏。
  const res = await fetch("/api/method/pearnly_cowork.companion.api.register", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Frappe-CSRF-Token": window.csrf_token ?? "" },
    body: JSON.stringify({
      request_key: crypto.randomUUID(),
      express,
      ...(device
        ? { device_key: device.key, device_name: device.name, ...(rekey ? { rekey: true } : {}), ...(rejoin ? { rejoin: true } : {}) }
        : replace
          ? { replace: true, expected_generation: replace }
          : {}),
    }),
  });
  if (res.status === 403) return { kind: "not_admin" };
  const body = (await res.json().catch(() => null)) as { message?: Registered } | null;
  if (!res.ok || !body?.message) return { kind: "failed", message: "" };
  return body.message;
}

/** 本机要不要一把新密钥：一个成员都没有（第一次、重装过），或有成员被服务端说密钥不再用（device_state）。 */
export function needsKey(state: Pick<DesktopState, "members">): boolean {
  return !state.members.length || state.members.some((member) => !!member.device_state);
}

/** 连接这台电脑。replace：这条连接正被另一台电脑用着时，用户确认改用这台（带那把密钥的代数，只属于旧版）；switchSite：断开原来连的别家公司。
 *
 * CC 桌面版 1.7.0 起（多台电脑，ADR-021）每次都带设备密钥登记（服务端记下这台电脑上最近登录 CC 的人，写账按它分派）：
 * - created：新电脑、或 rekey 换了新密钥 → 交给桌面端配对（同一条连接的成员换上新密钥，device_state 随之清掉）；
 * - exists：这台电脑在池里已有密钥。本机成员都好着 → 什么都不做；本机没有能用的成员（needsKey）→ 再带 rekey 登记一次
 *   换新密钥（不是登记人也不是管理员时服务端回 403，显示现有「需要管理员账号」）；
 * - update_needed：只回给不带设备密钥的旧版（池里已有新版认领过的电脑），提示更新 CC 桌面版；
 * - removed：这台电脑被「电脑」列表移除过，设置里写「电脑已移除」+「连接」；人点了「连接」（rejoin）才作为新电脑回来。
 * 签发人已用不了这条连接（名单收窄）时服务端自己换成本人签发的新钥、回 created（第 6 批审查第 1 条），页面照常配对。 */
export async function connectComputer(api: Desktop, options: { replace?: number; switchSite?: boolean; rejoin?: boolean } = {}) {
  if (link.kind === "connecting") return;
  setLink({ kind: "connecting" });
  try {
    const state = await api.state();
    let roots = state.express_roots ?? [];
    if (!roots.length) roots = (await api.call("detect")).roots ?? [];
    const device = thisComputer(state);
    const express = roots.length > 0;
    const stale = state.members.some((member) => member.device_state === "superseded");
    let found = await register(express, { replace: options.replace, device, rekey: !!device && stale, rejoin: options.rejoin });
    if (!("kind" in found) && found.status === "exists" && device && needsKey(state)) {
      found = await register(express, { device, rekey: true });
    }
    if ("kind" in found) return setLink(found);
    if (found.status === "in_use") return setLink({ kind: "in_use", generation: found.generation });
    if (found.status === "no_connection") return setLink({ kind: "no_connection" });
    if (found.status === "update_needed") return setLink({ kind: "update_needed" });
    if (found.status === "removed") return setLink({ kind: "removed" });
    if (found.status === "exists") return setLink({ kind: "idle" });
    // 只有新发的密钥才交给桌面端；服务端将来的其他状态不带密钥，绝不拿 undefined 去配对
    if (found.status !== "created" || !found.token) return setLink({ kind: "failed", message: "" });
    const paired = await api.call("pair", { code: `${found.token}@${location.host}`, switch: !!options.switchSite });
    if (paired.other_site) return setLink({ kind: "other_site", site: paired.other_site });
    setLink(paired.ok ? { kind: "idle" } : { kind: "failed", message: paired.message ?? "" });
  } catch {
    setLink({ kind: "failed", message: "" });
  }
}

/** 对话页打开时（Cowork 站点、在桌面端里）：这台电脑还没连别家公司就登记一次。
 *
 * - 旧版（没有 devices）：照旧只在一个成员都没有时自动连；
 * - 1.7.0 起：每次打开都带设备密钥登记（成员都好着时服务端只记最近登录的人，回 exists，不换密钥）；之后本机状态变了
 *   （state().changed）、某个成员新被服务端说密钥不再用（device_state：superseded / claimed_elsewhere），马上再登记一次换新
 *   密钥——窗口开着就自己恢复，不等人点（第 5 批审查第 3 条留给第 6 批的二选一）。同一个成员的同一种
 *   状态只自动试一次，不来回重试；失败的留在设置「连接」里（需处理 + 重试）。 */
export function useAutoConnect(product: string | undefined) {
  useEffect(() => {
    if (product !== "cowork") return;
    let alive = true;
    let off = () => {};
    const tried = new Set<string>();
    void desktop().then(async (api) => {
      if (!api || !alive) return;
      const state = await api.state();
      if (!state || !("members" in state)) return;
      if (state.site && state.site !== location.host) return setLink({ kind: "other_site", site: state.site });
      for (const member of state.members) if (member.device_state) tried.add(`${member.id}:${member.device_state}`);
      if (!state.members.length || thisComputer(state)) await connectComputer(api);
      if (!alive || !thisComputer(state)) return;
      off = api.onChanged(() => {
        void api.state().then((next) => {
          if (!alive || !next || !("members" in next) || (next.site && next.site !== location.host)) return;
          const fresh = next.members.filter((m) => m.device_state && !tried.has(`${m.id}:${m.device_state}`));
          if (!fresh.length) return;
          for (const member of fresh) tried.add(`${member.id}:${member.device_state}`);
          void connectComputer(api);
        });
      });
    });
    return () => {
      alive = false;
      off();
    };
  }, [product]);
}

// 对话文字大小（设置 → 常规），只存在本机浏览器里
export type TextSize = "small" | "medium" | "large";
const TEXT_SIZE_KEY = "tomo-text-size";

export function textSize(): TextSize {
  try {
    const value = localStorage.getItem(TEXT_SIZE_KEY);
    return value === "small" || value === "large" ? value : "medium";
  } catch {
    return "medium";
  }
}

export function setTextSize(size: TextSize) {
  document.documentElement.dataset.textSize = size;
  try {
    localStorage.setItem(TEXT_SIZE_KEY, size);
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
}

document.documentElement.dataset.textSize = textSize();

// ---------------------------------------------------------------------------
// 桌面端窗口（同 Claude 桌面端：没有系统标题栏，菜单 / 侧栏 / 后退 / 前进与最小化 / 最大化 / 关闭都画在页面里）。
// 按钮经桥接调本机窗口（companion tomo_bridge 的 window 方法）；浏览器里这些都不出现。

/** 是否在 CC 桌面端窗口里。 */
export function isDesktopApp(): boolean {
  return Boolean(window.qt?.webChannelTransport);
}

// 要不要自己画标题栏：只有声明了无边框窗口的桌面端（1.4.1 起，桥接状态 window.frameless）。旧版桌面端仍有系统标题栏，
// 页面不画，免得两层标题栏、按钮点了没反应。
let frameless: boolean | null = isDesktopApp() ? null : false;
const framelessListeners = new Set<(value: boolean) => void>();
if (frameless === null) {
  void desktop()
    .then((api) => api?.state())
    .then((state) => (state as { window?: { frameless?: boolean } } | undefined)?.window?.frameless === true)
    .catch(() => false)
    .then((value) => {
      frameless = value;
      for (const cb of framelessListeners) cb(value);
    });
}

export function useFramelessWindow(): boolean {
  const [value, setValue] = useState(frameless === true);
  useEffect(() => {
    if (frameless !== null) return setValue(frameless);
    framelessListeners.add(setValue);
    return () => void framelessListeners.delete(setValue);
  }, []);
  return value;
}

export type WindowAction = "minimize" | "maximize" | "close" | "move" | "zoom" | "state";

/** 窗口操作；回 { maximized } 给最大化按钮换图标。 */
export async function windowAction(action: WindowAction, args: Record<string, unknown> = {}): Promise<{ maximized?: boolean }> {
  const api = await desktop();
  if (!api) return {};
  const result = (await api.call("window", { action, ...args })) as { maximized?: boolean };
  return result ?? {};
}
