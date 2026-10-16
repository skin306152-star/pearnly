// 侧栏手排的顺序（用户 10-16 选「公司可拖 + 置顶可拖」；thread-list.tsx 拖动或 Alt+↑/↓ 改）：
//
//   公司组 — 按公司分组时公司组可拖成任意顺序；拖过一次以后各组按手排的顺序，新出现的公司（手排时还没有的）排最上面
//            （按排序方式排在它们自己之间）。组里的对话照排序方式排，不能在组里调序。
//   置顶   — 已置顶的对话可拖着排先后（三种分组方式都一样）；新置顶的排最上面。
//
// 按人记住：同「分组和排序」、折叠（sidebar-view.ts、sidebar-fold.ts）与右侧栏卡片顺序（lib/company-panel.ts）一样，每台设备、
// 每个用户各记各的（localStorage，键带用户；不跨设备同步）；存不了（无痕窗口）时本次照样生效。

import { create } from "zustand";

export type SidebarOrder = { companies: string[]; pinned: string[] };

const EMPTY: SidebarOrder = { companies: [], pinned: [] };
const USER_KEY = "cc.sidebar.user"; // sidebar-view.ts 登录后写的「这台设备上次是谁」
export const orderKey = (user: string) => `cc.sidebar.order:${user}`;

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

const ids = (value: unknown): string[] =>
  Array.isArray(value) ? [...new Set(value.filter((id): id is string => typeof id === "string" && !!id))] : [];

/** 记下的那份（JSON）；读不出、形状不对的部分是空的（按排序方式）。 */
export function parseOrder(raw: string | null | undefined): SidebarOrder {
  try {
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (!value || typeof value !== "object") return EMPTY;
    const saved = value as Record<string, unknown>;
    return { companies: ids(saved.companies), pinned: ids(saved.pinned) };
  } catch {
    return EMPTY;
  }
}

function read(user: string): SidebarOrder {
  try {
    return user ? parseOrder(storage()?.getItem(orderKey(user))) : EMPTY;
  } catch {
    return EMPTY;
  }
}

let owner = (() => {
  try {
    return storage()?.getItem(USER_KEY) ?? "";
  } catch {
    return "";
  }
})();

export const useSidebarOrder = create<SidebarOrder>(() => read(owner));

/** 知道当前用户了：换成这个人记下的（同一人不变）。 */
export function bindOrderUser(user: string) {
  if (!user || user === owner) return;
  owner = user;
  useSidebarOrder.setState(read(user), true);
}

function save(patch: Partial<SidebarOrder>) {
  const next = { ...useSidebarOrder.getState(), ...patch };
  useSidebarOrder.setState(next, true);
  if (!owner) return; // 还不知道是谁：本次照样生效，不写进不带人的键
  try {
    storage()?.setItem(orderKey(owner), JSON.stringify(next));
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
}

/** 手排后的公司组顺序（shown：拖完眼前的先后；之前记下、这次没显示的接在后面，相对先后不变）。 */
export function setCompanyOrder(shown: readonly string[]) {
  save({ companies: mergeOrder(shown, useSidebarOrder.getState().companies) });
}

/** 手排后的置顶顺序（对话编号）。 */
export function setPinnedOrder(shown: readonly string[]) {
  save({ pinned: mergeOrder(shown, useSidebarOrder.getState().pinned) });
}

// ---------------------------------------------------------------- 纯函数
/** 眼前的新先后 + 之前记下、这次没显示的（接在后面，相对先后不变）。 */
export function mergeOrder(shown: readonly string[], saved: readonly string[]): string[] {
  const head = [...new Set(shown)];
  return [...head, ...saved.filter((id) => !head.includes(id))];
}

/** 按手排的顺序排：没记过顺序原样返回；记过的按记下的先后，新出现的（记下时还没有的）排最上面，它们之间保持原来的先后。 */
export function arrange<T>(items: readonly T[], idOf: (item: T) => string, saved: readonly string[] | undefined): T[] {
  if (!saved?.length) return [...items];
  const at = new Map(saved.map((id, i) => [id, i]));
  const fresh = items.filter((item) => !at.has(idOf(item)));
  const known = items.filter((item) => at.has(idOf(item))).sort((a, b) => at.get(idOf(a))! - at.get(idOf(b))!);
  return [...fresh, ...known];
}

/** 把 id 在 list 里挪到 to 的位置（拖动放下）；挪 delta 格用 moveBy。没动、认不出回 null。 */
export function moveTo(list: readonly string[], id: string, to: string): string[] | null {
  const from = list.indexOf(id);
  const index = list.indexOf(to);
  if (from < 0 || index < 0 || from === index) return null;
  const next = list.filter((x) => x !== id);
  next.splice(index, 0, id);
  return next;
}

/** Alt+↑ / Alt+↓：挪一格；到头了回 null。 */
export function moveBy(list: readonly string[], id: string, delta: number): string[] | null {
  const from = list.indexOf(id);
  const to = from + delta;
  if (from < 0 || !delta || to < 0 || to >= list.length) return null;
  return moveTo(list, id, list[to]!);
}
