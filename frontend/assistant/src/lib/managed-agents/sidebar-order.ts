// 侧栏手排的顺序（用户 10-16 选「公司可拖 + 置顶可拖」，10-17 加「组里的对话可拖」；thread-list.tsx 拖动或 Alt+↑/↓ 改）：
//
//   公司组 — 按公司分组时公司组可拖成任意顺序；拖过一次以后各组按手排的顺序，新出现的公司（手排时还没有的）排最上面
//            （按排序方式排在它们自己之间）。
//   组里   — 按公司分组时对话只能在它自己那一组（某家公司，或「最近」）里拖着排先后（用户 10-17：不能跨公司）。按组记（键 =
//            公司编号，最近用 ""）：对话编号的先后 + 存下时这组最新的那个时间（at：按当前排序方式用的时间，取自服务端给的时间，
//            不用本机时钟——审查：本机时钟快了，存下后几分钟内新建的对话会被当成更旧）。显示时（sidebar-groups.ts 的
//            arrangeGroup）手排过的按手排的先后；不在手排列表里的，比 at 新的排最上面（新对话出现在顶上），不比它新的（「显示
//            更多」后才取到的）接在后面，各自按排序方式排。置顶、删除的对话从手排列表里去掉，公司不在侧栏了（删了、没对话了）
//            整组去掉（forgetInGroups / forgetGroups）。每组最多记 GROUP_LIMIT 条；拖到更后面时记到落点为止（落点不丢）。
//            刷新、换设备后服务端第一页把手排列表里的对话一并给（pearnly_core.assistant.sessions 的 _ordered），组顶照手排显示。
//   置顶   — 已置顶的对话可拖着排先后（三种分组方式都一样）；新置顶的排最上面（取消置顶就从记下的顺序里去掉，再置顶算新的）。
//            对话拖进「已置顶」= 置顶它，按落点排进这份顺序。取消置顶的对话回到它那一组的最上面（用户 10-17）：那一组手排过
//            就插到手排列表最前面、照常存与同步（同把它拖到组顶）；没手排过照排序方式（thread-list.tsx 的 liftUnpinned）。
//   恢复默认设置（「分组和排序」菜单）清掉这三份手排的顺序。在「分组和排序」里选排序方式（含点当前已选的那一项）只清组里的
//   （clearGroupOrders，回到按排序方式排）；手排过的公司组、置顶仍按手排的先后。
//
// 按人记住、跨设备同步（同「分组和排序」sidebar-view.ts 与右侧栏卡片 lib/company-panel.ts，见 lib/prefs-sync.ts）：存在服务端
// 当前用户的那份里，本机仍记一份（localStorage，键带用户）让打开时立刻生效、离线照样能用；存不了（无痕窗口）时本次照样生效。
// 各项后写的为准：公司组、置顶各是一项，组里的每一组各是一项（group:<公司编号>，最近 group:）——审查：各组合成一项时，一直开着、
// 没重取过的设备改一组就把整份交上去，冲掉别的设备在其他组排好的；离线改的联网后也整份盖回去。组里的各项以服务端为准：本机没改、
// 服务端没有的组（别处清掉了、服务端按上限截掉了）本机也去掉（prefs-sync 的 serverOwns）；去掉一组交 null，服务端删这一项。
// 侧栏各组的折叠（sidebar-fold.ts）仍按设备记。

import { create } from "zustand";
import { prefChanged, registerPref } from "@/lib/prefs-sync";

/** 一组里手排的先后：对话编号（keys），存下时这组最新的那个时间（at，毫秒；比它新的不在列表里的对话排最上面）。 */
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

/** 同步时组里的每一组各是一项：group:<公司编号>（最近 group:）。 */
export const GROUP_FIELD = "group:";
export const groupField = (group: string) => `${GROUP_FIELD}${group}`;

/** 同步来的各项里组里的那几项（group:<编号>）→ 各组。 */
function groupFieldsOf(saved: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(saved)
      .filter(([field]) => field.startsWith(GROUP_FIELD))
      .map(([field, value]) => [field.slice(GROUP_FIELD.length), value]),
  );
}

/** 记下的那份（JSON；本机缓存记 groups，同步来的是一组一项 group:<编号>）；读不出、形状不对的部分是空的（按排序方式）。 */
export function parseOrder(raw: string | null | undefined): SidebarOrder {
  try {
    const value: unknown = raw ? JSON.parse(raw) : null;
    if (!value || typeof value !== "object") return EMPTY;
    const saved = value as Record<string, unknown>;
    const groups = groupsOf({ ...(saved.groups && typeof saved.groups === "object" ? saved.groups : {}), ...groupFieldsOf(saved) });
    return { companies: ids(saved.companies), pinned: ids(saved.pinned), groups };
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

/** 改了几项：换成 next 并记进本机，交给同步（fields：改了的那几项，见 groupField；tidy：按数据收拾的，lib/prefs-sync.ts）。 */
function save(patch: Partial<SidebarOrder>, fields: readonly string[], tidy = false) {
  keep({ ...useSidebarOrder.getState(), ...patch });
  prefChanged("order", fields, tidy);
}

/** 这几组改了（同步时各是一项）。 */
const groupFields = (groups: Iterable<string>) => [...groups].map(groupField);

// 存到服务端的那份不超过服务端的上限（32 KB，pearnly_core.assistant.prefs）：
//   公司组、置顶 — 各最多 SYNC_LIMIT 条，且序列化后各不超过 LIST_BYTES（编号最长 128 字符时也放得下；手排时没显示的接在后面、
//                 一直留着，超出的最旧的先丢）
//   组里       — 每组最多 GROUP_LIMIT 条（拖到更后面时记到落点为止）；各组合起来的预算由服务端管（GROUPS_BYTES，最近存下的
//                 组先留，超出的整组去掉，本机随后取到时跟着去掉），这里不截：本机截了会把刚拖的那一组也截掉
export const SYNC_LIMIT = 300;
export const LIST_BYTES = 7 * 1024;
export const GROUP_LIMIT = 60;

const encoder = typeof TextEncoder === "undefined" ? null : new TextEncoder();
const bytesOf = (text: string) => (encoder ? encoder.encode(text).length : text.length * 3);

/** 一份编号列表按条数、字节数截（保留前面的）。 */
export function capList(list: readonly string[], limit = SYNC_LIMIT, budget = LIST_BYTES): string[] {
  const out: string[] = [];
  let size = 2; // []
  for (const id of list.slice(0, limit)) {
    size += bytesOf(JSON.stringify(id)) + (out.length ? 1 : 0);
    if (size > budget) break;
    out.push(id);
  }
  return out;
}

registerPref("order", {
  read: () => {
    const { companies, pinned, groups } = useSidebarOrder.getState();
    const each = Object.entries(groups).map(([group, order]) => [groupField(group), { keys: order.keys, at: order.at }]);
    return { companies: capList(companies), pinned: capList(pinned), ...Object.fromEntries(each) };
  },
  changed: () => hasManualOrder(useSidebarOrder.getState()),
  apply: (value, user) => {
    bindOrderUser(user);
    keep(parseOrder(JSON.stringify(value ?? null)));
  },
  // 组里的各项以服务端为准：本机没改、服务端没有的组去掉（别处清掉了、服务端按上限截掉了）
  serverOwns: (field) => field.startsWith(GROUP_FIELD),
});

/** 手排后的公司组顺序（shown：拖完眼前的先后；之前记下、这次没显示的接在后面，相对先后不变）。 */
export function setCompanyOrder(shown: readonly string[]) {
  save({ companies: mergeOrder(shown, useSidebarOrder.getState().companies) }, ["companies"]);
}

/** 手排后的置顶顺序（对话编号）。 */
export function setPinnedOrder(shown: readonly string[]) {
  save({ pinned: mergeOrder(shown, useSidebarOrder.getState().pinned) }, ["pinned"]);
}

/** 手排后一组里的先后（group：公司编号，最近 ""；shown：拖完眼前这组的先后，之前记下、这次没显示的接在后面）。
 *  at：此刻这组最新的那个时间（调用方按排序方式从这组的行里取，sidebar-groups.ts 的 groupStamp），之后才有动静（新建、新消息）
 *  的对话排在这组最上面。moved：这次挪动的（拖的那一条、多选的整块）：拖到 GROUP_LIMIT 以后时记到它们为止，落点不丢。
 *  tidy：按数据收拾的（别的设备上取消置顶，见 liftUnpinned），不是这台上人改的（lib/prefs-sync.ts）。 */
export function setGroupOrder(group: string, shown: readonly string[], at: number, moved: readonly string[] = [], tidy = false) {
  const { groups } = useSidebarOrder.getState();
  const merged = mergeOrder(shown, groups[group]?.keys ?? []);
  const upTo = Math.max(GROUP_LIMIT, ...moved.map((key) => merged.indexOf(key) + 1));
  save({ groups: { ...groups, [group]: { keys: merged.slice(0, upTo), at } } }, [groupField(group)], tidy);
}

/** 取消置顶的对话（key）放到它那一组手排列表的最前面（眼前的各行不知道时用：不在按公司分组、那一组没显示出来）。
 *  那一组没手排过不动（照排序方式排）；at 不变。tidy 同 setGroupOrder。 */
export function liftInGroup(group: string, key: string, tidy = false) {
  const { groups } = useSidebarOrder.getState();
  const saved = groups[group];
  if (!saved?.keys.length || saved.keys[0] === key) return;
  const keys = [key, ...saved.keys.filter((id) => id !== key)].slice(0, Math.max(GROUP_LIMIT, saved.keys.length));
  save({ groups: { ...groups, [group]: { keys, at: saved.at } } }, [groupField(group)], tidy);
}

// 别的设备上取消置顶、经侧栏同步并进本页的对话（list-sync.ts 的 restar 记下，侧栏把它挪到组顶时取走）：那一台已经存过一份
// 手排的先后，这里按数据收拾（tidy）；本页自己取消的（行菜单、P 键、长按菜单、多选、标题栏菜单）照常存、同步
const unpinnedElsewhere = new Set<string>();

export function noteUnpinnedElsewhere(key: string) {
  unpinnedElsewhere.add(key);
}

/** 这一条是别的设备上取消置顶的（取走记号）。 */
export function takeUnpinnedElsewhere(key: string): boolean {
  return unpinnedElsewhere.delete(key);
}

/** 在「分组和排序」里选了排序方式（含点当前已选的那一项）：各组里手排的先后都不要了，回到按排序方式排。 */
export function clearGroupOrders() {
  const { groups } = useSidebarOrder.getState();
  if (Object.keys(groups).length) save({ groups: {} }, groupFields(Object.keys(groups)));
}

/** 这些对话不在原来那一组里排了（置顶了、删了）：从各组手排的先后里去掉。tidy：按数据收拾的，不是人改的（lib/prefs-sync.ts）。 */
export function forgetInGroups(keys: readonly string[], tidy = false) {
  const { groups } = useSidebarOrder.getState();
  const changed: string[] = [];
  const next: Record<string, GroupOrder> = {};
  for (const [group, order] of Object.entries(groups)) {
    const kept = order.keys.filter((key) => !keys.includes(key));
    if (kept.length !== order.keys.length) changed.push(group);
    if (kept.length) next[group] = { keys: kept, at: order.at };
  }
  if (changed.length) save({ groups: next }, groupFields(changed), tidy);
}

/** 这几组没了（公司删了、侧栏里没有这家了）：整组去掉（按数据收拾的）。 */
export function forgetGroups(gone: readonly string[]) {
  const { groups } = useSidebarOrder.getState();
  const drop = gone.filter((group) => group in groups);
  if (!drop.length) return;
  save({ groups: Object.fromEntries(Object.entries(groups).filter(([group]) => !drop.includes(group))) }, groupFields(drop), true);
}

/** 这些对话不再置顶了：从手排的置顶顺序里去掉，以后再置顶时算新的、排最上面（第 5 轮审查：只增不减时再置顶会回到旧位置）。 */
export function forgetPinned(keys: readonly string[]) {
  const { pinned } = useSidebarOrder.getState();
  const next = pinned.filter((key) => !keys.includes(key));
  if (next.length !== pinned.length) save({ pinned: next }, ["pinned"], true); // 按数据收拾的，不是人改的（lib/prefs-sync.ts）
}

/** 「恢复默认设置」：手排的公司组、置顶与组里的顺序都不要了，回到按排序方式排。 */
export function clearSidebarOrder() {
  const now = useSidebarOrder.getState();
  if (hasManualOrder(now)) save(EMPTY, ["companies", "pinned", ...groupFields(Object.keys(now.groups))]);
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
