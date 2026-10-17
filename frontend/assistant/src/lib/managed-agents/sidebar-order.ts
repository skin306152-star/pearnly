// 侧栏手排的顺序（用户 10-16 选「公司可拖 + 置顶可拖」，10-17 加「组里的对话可拖」；thread-list.tsx 拖动或 Alt+↑/↓ 改）：
//
//   公司组 — 按公司分组时公司组可拖成任意顺序；拖过一次以后各组按手排的顺序，新出现的公司（手排时还没有的）排最上面
//            （按排序方式排在它们自己之间）。
//   组里   — 按公司分组时对话只能在它自己那一组（某家公司，或「最近」）里拖着排先后（用户 10-17：不能跨公司）。按组记（键 =
//            公司编号，最近用 ""）：对话编号的先后 + 存下的时刻。显示时（sidebar-groups.ts 的 arrangeGroup）手排过的按手排的先后；
//            不在手排列表里的，比存下那一刻新（按当前排序方式用的时间）的排最上面，更旧的（「显示更多」后才取到的）接在后面，
//            各自按排序方式排。置顶、删除的对话从手排列表里去掉（forgetInGroups）。
//   置顶   — 已置顶的对话可拖着排先后（三种分组方式都一样）；新置顶的排最上面（取消置顶就从记下的顺序里去掉，再置顶算新的）。
//            对话拖进「已置顶」= 置顶它，按落点排进这份顺序。
//   恢复默认设置（「分组和排序」菜单）清掉这三份手排的顺序。在「分组和排序」里选排序方式（含点当前已选的那一项）只清组里的
//   （clearGroupOrders，回到按排序方式排）；手排过的公司组、置顶仍按手排的先后。
//
// 按人记住、跨设备同步（同「分组和排序」sidebar-view.ts 与右侧栏卡片 lib/company-panel.ts，见 lib/prefs-sync.ts）：存在服务端
// 当前用户的那份里，本机仍记一份（localStorage，键带用户）让打开时立刻生效、离线照样能用；存不了（无痕窗口）时本次照样生效。
// 各项后写的为准（三项各自是一项：公司组、置顶、组里）。侧栏各组的折叠（sidebar-fold.ts）仍按设备记。

import { create } from "zustand";
import { prefChanged, registerPref } from "@/lib/prefs-sync";

/** 一组里手排的先后：对话编号（keys），存下的时刻（at，毫秒）。 */
export type GroupOrder = { keys: string[]; at: number };
export type SidebarOrder = { companies: string[]; pinned: string[]; groups: Record<string, GroupOrder> };

const EMPTY: SidebarOrder = { companies: [], pinned: [], groups: {} };
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

/** 各组的手排先后（键 = 公司编号，最近 ""）；形状不对、空的组不要。 */
function groupsOf(value: unknown): Record<string, GroupOrder> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  const out: Record<string, GroupOrder> = {};
  for (const [group, entry] of Object.entries(value as Record<string, unknown>)) {
    const saved = (entry && typeof entry === "object" ? entry : {}) as Record<string, unknown>;
    const keys = ids(saved.keys);
    const at = typeof saved.at === "number" && Number.isFinite(saved.at) ? saved.at : 0;
    if (keys.length) out[group] = { keys, at };
  }
  return out;
}

/** 记下的那份（JSON）；读不出、形状不对的部分是空的（按排序方式）。 */
export function parseOrder(raw: string | null | undefined): SidebarOrder {
  try {
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (!value || typeof value !== "object") return EMPTY;
    const saved = value as Record<string, unknown>;
    return { companies: ids(saved.companies), pinned: ids(saved.pinned), groups: groupsOf(saved.groups) };
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

/** 换成 next 并记进本机（不同步）。 */
function keep(next: SidebarOrder) {
  useSidebarOrder.setState(next, true);
  if (!owner) return; // 还不知道是谁：本次照样生效，不写进不带人的键
  try {
    if (hasManualOrder(next)) storage()?.setItem(orderKey(owner), JSON.stringify(next));
    else storage()?.removeItem(orderKey(owner));
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
}

function save(patch: Partial<SidebarOrder>, tidy = false) {
  keep({ ...useSidebarOrder.getState(), ...patch });
  prefChanged("order", Object.keys(patch), tidy);
}

// 存到服务端的那份：各列表最多记这么多（手排时没显示的接在后面、一直留着；最旧的先丢），整份不超过服务端的上限（32 KB）。
// 组里的：每组最多 GROUP_LIMIT 条，各组合起来最多 GROUPS_LIMIT 条（最近存下的组先留）
export const SYNC_LIMIT = 300;
export const GROUP_LIMIT = 60;
export const GROUPS_LIMIT = 240;

/** 组里的手排先后按上限截好（最近存下的组先留；超出的组整组不要，回到按排序方式排）。 */
export function capGroups(groups: Readonly<Record<string, GroupOrder>>): Record<string, GroupOrder> {
  const out: Record<string, GroupOrder> = {};
  let left = GROUPS_LIMIT;
  for (const [group, order] of Object.entries(groups).sort((a, b) => b[1].at - a[1].at)) {
    const keys = order.keys.slice(0, Math.min(GROUP_LIMIT, left));
    if (!keys.length) break;
    out[group] = { keys, at: order.at };
    left -= keys.length;
  }
  return out;
}

registerPref("order", {
  read: () => {
    const { companies, pinned, groups } = useSidebarOrder.getState();
    return { companies: companies.slice(0, SYNC_LIMIT), pinned: pinned.slice(0, SYNC_LIMIT), groups: capGroups(groups) };
  },
  changed: () => hasManualOrder(useSidebarOrder.getState()),
  apply: (value, user) => {
    bindOrderUser(user);
    keep(parseOrder(JSON.stringify(value ?? null)));
  },
});

/** 手排后的公司组顺序（shown：拖完眼前的先后；之前记下、这次没显示的接在后面，相对先后不变）。 */
export function setCompanyOrder(shown: readonly string[]) {
  save({ companies: mergeOrder(shown, useSidebarOrder.getState().companies) });
}

/** 手排后的置顶顺序（对话编号）。 */
export function setPinnedOrder(shown: readonly string[]) {
  save({ pinned: mergeOrder(shown, useSidebarOrder.getState().pinned) });
}

/** 手排后一组里的先后（group：公司编号，最近 ""；shown：拖完眼前这组的先后，之前记下、这次没显示的接在后面）。
 *  存下的时刻按这一次算：之后才有动静（新建、新消息）的对话排在这组最上面。 */
export function setGroupOrder(group: string, shown: readonly string[], at = Date.now()) {
  const { groups } = useSidebarOrder.getState();
  const keys = mergeOrder(shown, groups[group]?.keys ?? []).slice(0, GROUP_LIMIT);
  save({ groups: capGroups({ ...groups, [group]: { keys, at } }) });
}

/** 在「分组和排序」里选了排序方式（含点当前已选的那一项）：各组里手排的先后都不要了，回到按排序方式排。 */
export function clearGroupOrders() {
  if (Object.keys(useSidebarOrder.getState().groups).length) save({ groups: {} });
}

/** 这些对话不在原来那一组里排了（置顶了、删了、所在的公司没了）：从各组手排的先后里去掉。tidy：按数据收拾的，不是人改的
 *  （lib/prefs-sync.ts）。 */
export function forgetInGroups(keys: readonly string[], tidy = false) {
  const { groups } = useSidebarOrder.getState();
  let changed = false;
  const next: Record<string, GroupOrder> = {};
  for (const [group, order] of Object.entries(groups)) {
    const kept = order.keys.filter((key) => !keys.includes(key));
    changed ||= kept.length !== order.keys.length;
    if (kept.length) next[group] = { keys: kept, at: order.at };
  }
  if (changed) save({ groups: next }, tidy);
}

/** 这些对话不再置顶了：从手排的置顶顺序里去掉，以后再置顶时算新的、排最上面（第 5 轮审查：只增不减时再置顶会回到旧位置）。 */
export function forgetPinned(keys: readonly string[]) {
  const { pinned } = useSidebarOrder.getState();
  const next = pinned.filter((key) => !keys.includes(key));
  if (next.length !== pinned.length) save({ pinned: next }, true); // 按数据收拾的，不是人改的（lib/prefs-sync.ts）
}

/** 「恢复默认设置」：手排的公司组、置顶与组里的顺序都不要了，回到按排序方式排。 */
export function clearSidebarOrder() {
  if (hasManualOrder(useSidebarOrder.getState())) save(EMPTY);
}

/** 手排过（「恢复默认设置」可点）。 */
export const hasManualOrder = (order: SidebarOrder) =>
  order.companies.length > 0 || order.pinned.length > 0 || Object.keys(order.groups).length > 0;

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
