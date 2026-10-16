// 外壳的个人排列按人存到服务端、跨设备同步（用户 2026-10-16 选「公司可拖 + 置顶可拖」时说「按人记住」；测试窗口第 7 轮：
// 原来只记在这台设备上）。同步的三份：
//
//   order — 侧栏手排的公司组与置顶顺序（lib/managed-agents/sidebar-order.ts）
//   view  — 侧栏「分组和排序」（lib/managed-agents/sidebar-view.ts）
//   cards — 右侧会话栏卡片的顺序与开合（lib/company-panel.ts）
//
// 侧栏各组的折叠（sidebar-fold.ts）、会话栏停靠时开着没有仍按设备记，不同步。
//
//   存在哪 — 服务端当前用户自己的一份 JSON（pearnly_core.assistant.prefs，接口 layout / save_layout，只读写自己的，32 KB 上限）。
//   取     — 知道是谁了（syncPrefs）、回到前台（隔一会儿才再取）时取；服务端有就换成它（后写的为准），各份照样写进本机缓存。
//   存     — 改了就存（三份合成整份，稍等一下合并连着的几次改动）；存不上（离线）记一个「本机有没存上的」，回到前台、联网时补存，
//            这时本机的是后写的，不被服务端旧的盖掉。
//   迁移   — 服务端还没有（第一次用新版本）：本机原来记下的（不是默认的）整份存上去。
//   本机缓存 — 各份仍记在 localStorage（键带用户）：打开页面时立刻按它显示，离线照样能用。
//
// 各份由自己的模块登记（registerPref），这里不导入它们（不绕成循环依赖）。

import { get, post } from "@/lib/frappe";

/** 一份要同步的偏好：read 此刻的值（整份，默认值也给）；changed 跟默认不一样（迁移时据此决定要不要存）；apply 换成服务端的
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
// 回到前台时，离上次取过这么久才再取
export const REFETCH_AFTER = 10_000;

let user = "";
let applying = false;
let edits = 0; // 本页改过几次（取的时候又改了：本机的后写，为准）
let timer: ReturnType<typeof setTimeout> | null = null;
let fetchedAt = -Infinity;
let listening = false;
let fetching = false;

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

function unsaved(who: string): boolean {
  try {
    return !!who && storage()?.getItem(DIRTY_KEY(who)) === "1";
  } catch {
    return false;
  }
}

function markUnsaved(who: string, on: boolean) {
  if (!who) return;
  try {
    if (on) storage()?.setItem(DIRTY_KEY(who), "1");
    else storage()?.removeItem(DIRTY_KEY(who));
  } catch {
    // 存不了：只是离线时改的下次打开可能被服务端的盖掉
  }
}

/** 三份合成的整份（存到服务端的那份）。 */
export function collectPrefs(): Record<string, Record<string, unknown>> {
  return Object.fromEntries([...parts].map(([name, part]) => [name, part.read()]));
}

/** 换成服务端的那份：认识的各份交给各自的模块；没有的那份不动。 */
export function applyPrefs(prefs: Record<string, unknown>, who: string) {
  applying = true;
  try {
    for (const [name, part] of parts) if (name in prefs) part.apply(prefs[name], who);
  } finally {
    applying = false;
  }
}

/** 某一份改了（各模块存本机时调）：稍等一下存到服务端。换成服务端的那份时不算。
 *  tidy：按数据自动收拾的（取消置顶了从手排顺序里去掉），不是人改的：正在取服务端的那份时不算（不因此把本机的当成后写的、
 *  盖掉别的设备改的；换成服务端的以后会照新的那份再收拾一遍）。 */
export function prefChanged(tidy = false) {
  if (applying || (tidy && fetching)) return;
  edits += 1;
  markUnsaved(user, true);
  if (timer !== null) clearTimeout(timer);
  timer = setTimeout(() => void flush(), SAVE_DELAY);
}

/** 现在就存（等着的那次一并存掉）；回存上了没有。还不知道是谁时不存（知道了 syncPrefs 会补）。 */
export async function flush(): Promise<boolean> {
  if (timer !== null) clearTimeout(timer);
  timer = null;
  const who = user;
  if (!who) return false;
  const at = edits;
  try {
    await post("save_layout", { prefs: collectPrefs() });
  } catch {
    return false; // 离线等：记着没存上，回到前台、联网时补
  }
  if (who === user && at === edits) markUnsaved(who, false);
  return true;
}

/** 知道当前用户了（或回到前台）：取服务端的那份。本机有没存上的（离线时改的、取的时候又改了）就把本机的存上去；
 *  服务端还没有就把本机原来记下的迁上去；否则换成服务端的。 */
export async function syncPrefs(who: string): Promise<void> {
  if (!who) return;
  if (who !== user) {
    user = who;
    fetchedAt = -Infinity;
    if (edits) markUnsaved(who, true); // 知道是谁之前就改过：算这个人的、本机为准
  }
  listen();
  if (unsaved(who)) {
    await flush();
    return;
  }
  const at = edits;
  fetchedAt = now();
  let prefs: Record<string, unknown> | null;
  fetching = true;
  try {
    prefs = (await get<{ prefs: Record<string, unknown> | null }>("layout"))?.prefs ?? null;
  } catch {
    return; // 取不到：照本机的
  } finally {
    fetching = false;
  }
  if (who !== user) return;
  if (at !== edits || unsaved(who)) {
    await flush();
    return;
  }
  if (prefs) applyPrefs(prefs, who);
  else if ([...parts.values()].some((part) => part.changed())) await flush();
}

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** 回到前台时再取（隔 REFETCH_AFTER 以上；手机主屏幕 app 常开着好几天），联网时补存没存上的。 */
function listen() {
  if (listening || typeof document?.addEventListener !== "function" || typeof window?.addEventListener !== "function") return;
  listening = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !user) return;
    if (unsaved(user) || now() - fetchedAt >= REFETCH_AFTER) void syncPrefs(user);
  });
  window.addEventListener("online", () => {
    if (user && unsaved(user)) void flush();
  });
}
