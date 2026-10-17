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
//            就插到手排列表最前面，记成组顶的一段（top：这几条排在存下以后才有动静的对话之上，取消置顶这一刻以后才有动静的
//            对话照常排在它们上面），照常存、同步，别的行不写进手排；没手排过照排序方式（noteUnpinned / applyLifts）。
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

/** 一组里手排的先后：对话编号（keys），存下时这组最新的那个时间（at，毫秒；比它新的不在列表里的对话排最上面）。
 *  top：取消置顶回到组顶的那一段（keys 的前 n 条，at 是放上去那一刻这组最新的时间）：排在比 at 新、不比 top.at 新的对话之上，
 *  比 top.at 新的照常排最上面（sidebar-groups.ts 的 arrangeGroup）；之后在这一组拖过就不再有（拖完眼前的先后整份记下）。 */
export type GroupOrder = { keys: string[]; at: number; top?: { n: number; at: number } };
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
    const top = (saved.top && typeof saved.top === "object" ? saved.top : {}) as Record<string, unknown>;
    const n = typeof top.n === "number" && Number.isInteger(top.n) ? Math.min(top.n, keys.length) : 0;
    const lift = n > 0 && typeof top.at === "number" && Number.isFinite(top.at) ? { top: { n, at: top.at } } : {};
    if (keys.length) out[group] = { keys, at, ...lift };
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
    const each = Object.entries(groups).map(([group, order]) => [groupField(group), { keys: order.keys, at: order.at, ...(order.top ? { top: order.top } : {}) }]);
    return { companies: capList(companies), pinned: capList(pinned), ...Object.fromEntries(each) };
  },
  changed: () => hasManualOrder(useSidebarOrder.getState()),
  apply: (value, user) => {
    bindOrderUser(user);
    keep(parseOrder(JSON.stringify(value ?? null)));
    confirmLifts();
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
 *  这次记下的对话里还等着回组顶的（别的设备上取消置顶、等服务端那份确认的）不再挪：人排好的为准。 */
export function setGroupOrder(group: string, shown: readonly string[], at: number, moved: readonly string[] = []) {
  const { groups } = useSidebarOrder.getState();
  const merged = mergeOrder(shown, groups[group]?.keys ?? []);
  const upTo = Math.max(GROUP_LIMIT, ...moved.map((key) => merged.indexOf(key) + 1));
  for (const key of merged) lifts.delete(key);
  save({ groups: { ...groups, [group]: { keys: merged.slice(0, upTo), at } } }, [groupField(group)]);
}

// ---------------------------------------------------------------- 取消置顶回组顶
// 取消置顶的对话回到它那一组的最上面（用户 10-17）。由取消置顶这件事本身记下（noteUnpinned），侧栏（thread-list.tsx）眼前有
// 这一行时照数据挪（applyLifts：它在哪一组、这组此刻最新的时间由侧栏给），不靠「上一次渲染还是置顶、这次不是」去猜（审查：
// 置顶失败、组件库撤回乐观更新也是由置顶变成没置顶，会被当成取消置顶留下手排记录）：
//   本机   — 行菜单、P 键、手机长按菜单、标题栏菜单、多选「取消置顶 N」（unpinChat）：请求发出就先挪（只改本机，不晚一拍），
//            服务端确认后才存、同步；失败（组件库撤回置顶）撤回这次挪动。
//   别处   — 别的设备上取消、经侧栏同步推来的（list-sync.ts 的 restar）：按数据收拾（tidy，那一台存过）。服务端那份换进来时还没有
//            它（那一台还没存上）就再挪一次；服务端那份里那一组已经有它了才算收拾完（confirmLifts）。
//   多选   — 一起取消的按侧栏里原来的先后回到组顶（逐条做，后一条排在同批已经回来的那几条后面）。
// 记号有时效（LIFT_TTL）：这一行一直没出现在侧栏（别的产品、归档的、没加载的）也不会一直留着。
export const LIFT_TTL = 30_000;
type Lift = {
  mine: boolean;
  /** 服务端已确认（本机的）。 */
  ok: boolean;
  until: number;
  /** 同批排在它前面的（回组顶后照旧在它前面）。 */
  after: readonly string[];
  /** 已经挪了、还等服务端确认（本机的）：那一组挪前 / 挪后的两份，失败时比对后撤回。 */
  moved?: { group: string; before: GroupOrder; after: GroupOrder };
};
const lifts = new Map<string, Lift>();
const clock = () => (typeof performance !== "undefined" ? performance.now() : Date.now());

/** 等着回组顶的有变化（侧栏据此再挪一遍）。 */
export const useLifts = create<{ tick: number }>(() => ({ tick: 0 }));
const poke = () => useLifts.setState((state) => ({ tick: state.tick + 1 }));

/** 记下这一条取消置顶了；mine：本机人点的（服务端确认前只改本机），否则是别的设备上的。after：同批排在它前面的。 */
export function noteUnpinned(key: string, mine: boolean, after: readonly string[] = []) {
  lifts.set(key, { mine, ok: !mine, until: clock() + LIFT_TTL, after });
  poke();
}

/** 本机这一条取消置顶的请求有结果了：成功就把挪好的存上、同步（侧栏还没挪的，挪的时候直接存）；失败撤回这次挪动。 */
export function unpinSettled(key: string, ok: boolean) {
  const lift = lifts.get(key);
  if (!lift?.mine) return;
  const moved = lift.moved;
  const groups = useSidebarOrder.getState().groups;
  const intact = !!moved && groups[moved.group] === moved.after;
  if (!ok) {
    lifts.delete(key);
    if (intact) keep({ ...useSidebarOrder.getState(), groups: { ...groups, [moved!.group]: moved!.before } });
    return;
  }
  if (intact) {
    lifts.delete(key);
    prefChanged("order", [groupField(moved!.group)]);
    return;
  }
  // 挪过以后那一组又被换过（服务端那份换进来了）或者还没挪：侧栏照新的那份再挪、直接存
  lift.ok = true;
  lift.moved = undefined;
  poke();
}

/** 本机取消置顶一条（各入口同一道）：先记下回组顶，发请求（组件库乐观地先显示成没置顶），按结果存或撤回。after：多选时同批排在
 *  它前面的。置顶不经这里（照旧直接 updateCustom）。服务端确认了才从手排的置顶顺序里去掉（forgetPinned）。 */
export async function unpinChat(key: string, run: () => Promise<void>, after: readonly string[] = []): Promise<void> {
  noteUnpinned(key, true, after);
  try {
    await run();
  } catch (error) {
    unpinSettled(key, false);
    throw error;
  }
  forgetPinned([key]);
  unpinSettled(key, true);
}

// 本机发出、服务端还没回的置顶 / 取消置顶（对话编号）。组件库乐观地先显示成新的样子，失败再撤回：这段时间侧栏不按眼前的样子收拾
// 手排的顺序（置顶的从组里去掉、取消置顶的从置顶顺序里去掉），确认了才收拾。测试窗口第 10 轮 B 段：手机断网取消置顶第 2 位的
// 对话，乐观更新那一下就把它从置顶顺序里去掉并同步出去，失败撤回后它成了「新置顶的」排到第 1 位，联网后服务端那份换进来才回到
// 第 2 位；同一时刻别的设备排好的置顶顺序也会被这次收拾盖掉。
const starring = new Set<string>();

/** 这一条的置顶 / 取消置顶请求还没回（侧栏收拾手排顺序时跳过它）。 */
export const starPending = (key: string) => starring.has(key);

/** 组件库的一段对话（置顶用到的那几样）。updateCustom 运行时回 Promise（组件库的乐观更新，失败时撤回并抛出），类型上写的是 void。 */
export type StarItem = {
  getState(): { readonly id: string; readonly remoteId?: string | undefined; readonly custom?: Record<string, unknown> | undefined };
  updateCustom(custom: Record<string, unknown>): unknown;
};

/** 置顶 / 取消置顶一段对话（行菜单、P 键、手机长按菜单、标题栏菜单、多选同一道）。展开写法：组件库整份替换 custom，以后加的键
 *  不能被冲掉。取消置顶经 unpinChat 回到它那一组的最上面；after 同 unpinChat。请求没回之前侧栏不收拾它的手排顺序（starPending），
 *  失败时组件库撤回、它原样留在原位；出错由调用方写进侧栏出错行（projects.ts 的 setError，通用句）。 */
export async function starChat(item: StarItem, starred: boolean, after: readonly string[] = []): Promise<void> {
  const { id, remoteId, custom } = item.getState();
  const key = remoteId ?? id;
  // 明知断网：不先乐观地挪走再撤回（那一下会闪一帧），当场按请求失败处理（调用方写出错行）
  if (typeof navigator !== "undefined" && navigator.onLine === false) throw new TypeError("offline");
  const run = async () => void (await item.updateCustom({ ...custom, starred }));
  starring.add(key);
  try {
    if (!starred) return await unpinChat(key, run, after);
    await run();
    forgetInGroups([key], true); // 置顶了：从组里手排的先后里去掉（服务端确认后）
  } finally {
    starring.delete(key);
  }
}

/** 这一条此刻在侧栏里的样子（applyLifts 问侧栏）：group 它那一组，stamp 这组此刻最新的那个时间（groupStamp，按排序方式）；
 *  "starred" 仍是置顶；null 眼前没有这一行（没加载、不在侧栏）。 */
export type LiftSpot = { group: string; stamp: number } | "starred" | null;

/** 把等着回组顶的挪上去（侧栏每次行、手排顺序或记号变了时调）。那一组没手排过的不动（照排序方式）。 */
export function applyLifts(spotOf: (key: string) => LiftSpot) {
  const now = clock();
  for (const [key, lift] of [...lifts]) {
    if (lift.until < now) {
      lifts.delete(key);
      continue;
    }
    const spot = spotOf(key);
    if (!spot) continue;
    if (spot === "starred") {
      // 还没换成没置顶（本机刚点、组件库还没改；推来的还没并进去）：等着。本机挪过又变回置顶（请求失败、组件库撤回乐观更新，
      // 或又置顶了）：撤回这次挪动
      if (lift.moved) unpinSettled(key, false);
      continue;
    }
    if (lift.moved) continue; // 等服务端确认
    const groups = useSidebarOrder.getState().groups;
    const saved = groups[spot.group];
    // 别的设备上的：那一组已经有它（那一台存上了，或这里已经挪过）就不再挪，等服务端那份确认
    if (!saved?.keys.length || (!lift.mine && saved.keys.includes(key))) {
      if (lift.mine || !saved?.keys.length) lifts.delete(key);
      continue;
    }
    const next = liftedOrder(saved, key, spot.stamp, lift.after);
    if (sameOrder(next, saved)) {
      lifts.delete(key);
      continue;
    }
    keep({ ...useSidebarOrder.getState(), groups: { ...groups, [spot.group]: next } });
    if (!lift.mine) prefChanged("order", [groupField(spot.group)], true);
    else if (lift.ok) {
      lifts.delete(key);
      prefChanged("order", [groupField(spot.group)]);
    } else lift.moved = { group: spot.group, before: saved, after: next };
  }
}

/** 服务端那份换进来了：别的设备上取消置顶的，那一组里已经有它（那一台存上了）就收拾完了。 */
function confirmLifts() {
  const groups = Object.values(useSidebarOrder.getState().groups);
  for (const [key, lift] of [...lifts]) if (!lift.mine && groups.some((order) => order.keys.includes(key))) lifts.delete(key);
}

const sameOrder = (a: GroupOrder, b: GroupOrder) =>
  a.at === b.at && a.top?.n === b.top?.n && a.top?.at === b.top?.at && a.keys.length === b.keys.length && a.keys.every((key, i) => key === b.keys[i]);

/** key 回到这一组的组顶：放在手排列表最前面（after 里的——同批先回来的——排在它前面），组顶那一段（top）多它一条；top.at 取此刻
 *  这组最新的时间（stamp；不早于存下时的 at），之后才有动静的对话照常排在它上面。at 不变，别的行不写进手排。条数不超过原来的
 *  （至少 GROUP_LIMIT）。 */
export function liftedOrder(saved: GroupOrder, key: string, stamp: number, after: readonly string[] = []): GroupOrder {
  const index = saved.keys.indexOf(key);
  const had = saved.top?.n ?? 0;
  const rest = saved.keys.filter((id) => id !== key);
  let lead = 0;
  while (lead < rest.length && after.includes(rest[lead]!)) lead++;
  const keys = [...rest.slice(0, lead), key, ...rest.slice(lead)].slice(0, Math.max(GROUP_LIMIT, saved.keys.length));
  const n = Math.min(Math.max(had - (index >= 0 && index < had ? 1 : 0), lead) + 1, keys.length);
  return { keys, at: saved.at, top: { n, at: Math.max(stamp, saved.at, saved.top?.at ?? 0) } };
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
    if (kept.length === order.keys.length) {
      next[group] = order;
      continue;
    }
    changed.push(group);
    // 组顶那一段（top）里去掉了几条就少几条，一条不剩就不再有
    const n = order.top ? order.top.n - order.keys.slice(0, order.top.n).filter((key) => keys.includes(key)).length : 0;
    if (kept.length) next[group] = { keys: kept, at: order.at, ...(n > 0 ? { top: { n, at: order.top!.at } } : {}) };
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
