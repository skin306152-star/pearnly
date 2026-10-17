// 外壳的个人排列按人存到服务端、跨设备同步（用户 2026-10-16 选「公司可拖 + 置顶可拖」时说「按人记住」；测试窗口第 7 轮：
// 原来只记在这台设备上）。同步的三份，各份由几项组成：
//
//   order — 侧栏手排的公司组、置顶与组里对话的顺序（companies / pinned / groups；lib/managed-agents/sidebar-order.ts）
//   view  — 侧栏「分组和排序」（by / sort / desc / meta / empty；lib/managed-agents/sidebar-view.ts）
//   cards — 右侧会话栏卡片的顺序与开合（order / collapsed；lib/company-panel.ts）
//
// 侧栏各组的折叠（sidebar-fold.ts）、会话栏停靠时开着没有仍按设备记，不同步。
//
//   存在哪 — 服务端当前用户自己的一份 JSON（pearnly_core.assistant.prefs，接口 layout / save_layout，只读写自己的，32 KB 上限）。
//   按项记 — 改了哪项只存哪项（第 7 轮审查：整份覆盖时，一直开在前台、没重取过的设备改一项就把别的设备改过的几项打回旧值）；
//            服务端按项并进已有的那份。各项后写的为准。
//   取     — 知道是谁了、回到前台或窗口拿到焦点、联网时（离上次取过 REFETCH_AFTER 以上）取；服务端的各项换进来，本机改了还没
//            存上的那几项留本机的、随后存上去。取不到不记「取过」，下次照样取。同一时刻只取一次。
//   存     — 改了就存（稍等一下合并连着的几次改动）；存不上（离线）记着哪几项没存上，下次取完补存，这时本机的是后写的。
//   迁移   — 服务端还没有（第一次用新版本）：本机原来记下过（有一份不是默认的）就把三份整份存上去。
//   收拾   — 按数据自动收拾的（取消置顶了从手排顺序里去掉，tidy）不是人改的：刚取过（TIDY_FRESH 内）才存上去；否则先取一次，
//            换成服务端的以后会照新的那份再收拾一遍、那时再存；正在取、还不知道是谁时只改本机。不拿本机可能过时的值盖服务端。
//   本机缓存 — 各份仍记在 localStorage（键带用户）：打开时立刻按它显示，离线照样能用。
//
// 各份由自己的模块登记（registerPref），这里不导入它们（不绕成循环依赖）。

import { get, post } from "@/lib/frappe";

/** 一份要同步的偏好：read 此刻的值（整份各项，默认值也给）；changed 跟默认不一样（迁移时据此决定要不要存）；apply 换成服务端的
 *  （先换到这个人，再按值改并写本机缓存；不回调 prefChanged）。 */
export type PrefPart = {
  read: () => Record<string, unknown>;
  changed: () => boolean;
  apply: (value: unknown, user: string) => void;
};

const parts = new Map<string, PrefPart>();
const DIRTY_KEY = (user: string) => `cc.prefs.unsaved:${user}`;
// 连着改（拖完一下、连点几项）合成一次存
export const SAVE_DELAY = 400;
// 回到前台、拿到焦点、联网时，离上次取过这么久才再取
export const REFETCH_AFTER = 10_000;
// 自动收拾时，离上次取过这么久以内才算「刚取过」（换成服务端的以后侧栏马上会再收拾一遍）
export const TIDY_FRESH = 2_000;

let user = "";
let applying = false;
let stamp = 0;
// 本机改了还没存上的项：「份.项」→ 第几次改动（存上时只清没再改过的）
const dirty = new Map<string, number>();
let timer: ReturnType<typeof setTimeout> | null = null;
let fetchedAt = -Infinity;
let listening = false;
let fetching = false;
let syncing: { who: string; done: Promise<void> } | null = null;

export function registerPref(name: string, part: PrefPart) {
  parts.set(name, part);
}

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

/** 这个人在这台设备上记着没存上的项（旧版本只记 "1"：当作各份整份）。 */
function storedDirty(who: string): string[] {
  try {
    const raw = who ? storage()?.getItem(DIRTY_KEY(who)) : null;
    if (!raw) return [];
    if (raw === "1") return [...parts].flatMap(([name, part]) => Object.keys(part.read()).map((field) => `${name}.${field}`));
    const list: unknown = JSON.parse(raw);
    return Array.isArray(list) ? list.filter((key): key is string => typeof key === "string" && key.includes(".")) : [];
  } catch {
    return [];
  }
}

function keepDirty(who: string) {
  if (!who) return;
  try {
    if (dirty.size) storage()?.setItem(DIRTY_KEY(who), JSON.stringify([...dirty.keys()]));
    else storage()?.removeItem(DIRTY_KEY(who));
  } catch {
    // 存不了：只是离线时改的下次打开可能被服务端的盖掉
  }
}

function mark(name: string, fields: readonly string[]) {
  const all = fields.length ? fields : Object.keys(parts.get(name)?.read() ?? {});
  stamp += 1;
  for (const field of all) dirty.set(`${name}.${field}`, stamp);
  keepDirty(user);
}

/** 三份合成的整份（迁移时存上去的那份）。 */
export function collectPrefs(): Record<string, Record<string, unknown>> {
  return Object.fromEntries([...parts].map(([name, part]) => [name, part.read()]));
}

/** 本机改了还没存上的那几项（按份分好）。 */
function dirtyPrefs(): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const key of dirty.keys()) {
    const [name, field] = key.split(".", 2);
    const part = parts.get(name);
    if (!part) continue;
    const value = part.read();
    if (field in value) (out[name] ??= {})[field] = value[field];
  }
  return out;
}

/** 换成服务端的那份：各份交给各自的模块；本机改了还没存上的项留本机的；服务端没有的份、项不动。 */
export function applyPrefs(prefs: Record<string, unknown>, who: string) {
  const local = dirtyPrefs();
  applying = true;
  try {
    for (const [name, part] of parts) {
      if (!(name in prefs)) continue;
      const server = prefs[name] && typeof prefs[name] === "object" ? (prefs[name] as Record<string, unknown>) : {};
      part.apply({ ...part.read(), ...server, ...local[name] }, who);
    }
  } finally {
    applying = false;
  }
}

const schedule = (run: () => void) => {
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(run, SAVE_DELAY);
};

/** 某一份的几项改了（各模块存本机时调；fields 空 = 整份）：稍等一下存到服务端。换成服务端的那份时不算。
 *  tidy：按数据自动收拾的，不是人改的（见文件头「收拾」）。 */
export function prefChanged(name: string, fields: readonly string[] = [], tidy = false) {
  if (applying) return;
  if (tidy) {
    if (!user || fetching) return; // 只改本机：取回来、知道是谁以后侧栏会照新的那份再收拾
    if (now() - fetchedAt > TIDY_FRESH) {
      const who = user;
      schedule(() => void syncPrefs(who)); // 先取最新的，换进来以后再收拾、再存
      return;
    }
  }
  mark(name, fields);
  schedule(() => void flush());
}

/** 现在就存本机改了还没存上的几项；回存上了没有（没有要存的算存上了）。还不知道是谁时不存（知道了 syncPrefs 会补）。 */
export async function flush(): Promise<boolean> {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  const who = user;
  if (!who) return false;
  if (!dirty.size) return true;
  const sent = new Map(dirty);
  try {
    await post("save_layout", { prefs: dirtyPrefs() });
  } catch {
    return false; // 离线等：记着没存上，下次取完补
  }
  if (who !== user) return true;
  for (const [key, at] of sent) if (dirty.get(key) === at) dirty.delete(key);
  keepDirty(who);
  return true;
}

/** 知道当前用户了（或回到前台、拿到焦点、联网）：取服务端的那份换进来（本机没存上的项留本机的），再把没存上的存上去；
 *  服务端还没有就把本机原来记下的迁上去。同一个人同时只跑一次。 */
export function syncPrefs(who: string): Promise<void> {
  if (!who) return Promise.resolve();
  if (syncing?.who === who) return syncing.done;
  const done = run(who).finally(() => {
    if (syncing?.done === done) syncing = null;
  });
  syncing = { who, done };
  return done;
}

async function run(who: string): Promise<void> {
  if (who !== user) {
    if (user) dirty.clear(); // 换了人：上一个人没存上的记在他自己的键下，这里不带过去
    user = who;
    fetchedAt = -Infinity;
    // 知道是谁之前就改过的算这个人的；这台设备上次没存上的接着存
    for (const key of storedDirty(who)) if (!dirty.has(key)) dirty.set(key, ++stamp);
    keepDirty(who);
  }
  listen();
  let prefs: Record<string, unknown> | null;
  fetching = true;
  try {
    prefs = (await get<{ prefs: Record<string, unknown> | null }>("layout"))?.prefs ?? null;
  } catch {
    if (who === user) await flush(); // 取不到（离线、出错）：照本机的；存得上就先存
    return;
  } finally {
    fetching = false;
  }
  if (who !== user) return;
  fetchedAt = now();
  if (prefs) applyPrefs(prefs, who);
  else if ([...parts.values()].some((part) => part.changed())) for (const name of parts.keys()) mark(name, []);
  await flush();
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** 回到前台、拿到焦点（电脑上一直开着的窗口不触发 visibilitychange）、联网时：没存上的补存，隔 REFETCH_AFTER 以上再取。 */
function listen() {
  if (listening || typeof document?.addEventListener !== "function" || typeof window?.addEventListener !== "function") return;
  listening = true;
  const again = () => {
    if (!user || document.visibilityState === "hidden") return;
    if (dirty.size || now() - fetchedAt >= REFETCH_AFTER) void syncPrefs(user);
  };
  document.addEventListener("visibilitychange", again);
  window.addEventListener("focus", again);
  window.addEventListener("online", again);
}
