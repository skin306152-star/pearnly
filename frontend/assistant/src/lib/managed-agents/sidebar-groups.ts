// 有公司分组时侧栏的各组（纯函数，thread-list.tsx 渲染），按「分组和排序」（sidebar-view.ts）分三种：
//
//   按公司（companySections，默认）
//     已置顶 — 置顶的对话只在这里（不论属于哪家公司），取消置顶回到所属公司；没有就不显示
//     各公司 — 我有对话的公司，默认按组内对话的最近活动排（提醒浮顶的也算）；有对话的前 5 家默认展开（空公司组不占位、默认收着）；每组先显示 5 条，
//              「显示更多」每次加 10 条；「显示 → 空分组」关掉时不显示没有对话的公司
//     最近   — 不属于任何公司的对话
//   按处理月份（monthSections）：已置顶 → 各月（新的在前，升序时旧的在前）→ 未指定月份
//   不分组（flatSections）：已置顶 → 其余全部（标题「最近」）
//
// 组内按排序方式排：上次更新 / 创建日期 / 名称，降序或升序；公司组按同一种方式排（名称 = 公司名）。
// 手排（用户 10-16，sidebar-order.ts）：公司组拖过以后按手排的顺序（新出现的公司排最上面）；置顶的对话拖过以后按手排的先后
// （新置顶的排最上面）。组里其余对话照排序方式排。
// 搜索同时匹配对话标题和公司名（公司名命中时这家的对话都算）；搜索时每组都显示全部命中的，没命中的组不显示。

import { FIRST_SHOWN, OPEN_FIRST, type ProjectRow, rowActivity } from "./projects";
import { arrange } from "./sidebar-order";
import type { SortBy } from "./sidebar-view";

/** 侧栏的一行对话：index 是组件库 threadIds 里的位置；created 创建时间（列表带回的，本页新建的没有）、period 处理月份；
 *  key 是记手排顺序用的对话编号（服务端编号，本页新建还没存的用本页编号）。 */
export type SidebarRow = {
  index: number;
  title: string;
  starred: boolean;
  time: number;
  group: string;
  created?: number;
  period?: string;
  key?: string;
};

export type SidebarSection =
  | { type: "pinned"; key: "pinned"; indices: number[] }
  | { type: "project"; key: string; project: ProjectRow; rank: number; indices: number[]; all: number[]; more: boolean }
  | { type: "month"; key: string; period: string; indices: number[] }
  | { type: "recents"; key: "recents"; indices: number[] };

export type SortOptions = { by: SortBy; desc: boolean };

export type SectionOptions = {
  query?: string;
  shown?: Readonly<Record<string, number>>;
  /** 服务端还有更多的公司（复合游标里的 groups）。 */
  serverMore?: Readonly<Record<string, string>>;
  sort?: SortOptions;
  /** 显示没有对话的公司组（默认显示）。 */
  empty?: boolean;
  /** 手排的公司组顺序（公司编号；空 = 按排序方式）。 */
  companyOrder?: readonly string[];
  /** 手排的置顶顺序（行的 key；空 = 按排序方式）。 */
  pinnedOrder?: readonly string[];
  /** 要留在眼前的行（key）：在公司组「显示更多」之外时接在已显示的最后（刚拖进这一组的对话，落在哪就留在哪）。 */
  keep?: readonly string[];
};

const UPDATED_DESC: SortOptions = { by: "updated", desc: true };

/** 选了文件还没发的空对话（用户 10-16：侧栏里「选了文件但还没发」会多出一条「新对话」）：输入框加附件要先建会话，会话一建就成了
 *  侧栏普通一行（标题空）。标题空 = 还没发过消息（首条消息一发就有临时标题，session-list-adapter.ts 的 generateTitle），这样的行
 *  不列；当前打开的那条（按本页编号，或与它同一个服务端编号的另一行）照常显示。只在侧栏拼行时滤，不动服务端列表与组件库的数据：
 *  侧栏多设备同步（list-sync.ts）会把本该在这一页却没有的当成在别处删了。返回要藏的本页编号。 */
export function unsentDrafts(
  items: readonly { id: string; remoteId?: string | undefined; title?: string | undefined }[],
  mainId: string | undefined,
): Set<string> {
  const openRemote = items.find((item) => item.id === mainId)?.remoteId;
  const hidden = new Set<string>();
  for (const item of items) {
    if (item.title?.trim() || item.id === mainId || (openRemote && item.remoteId === openRemote)) continue;
    hidden.add(item.id);
  }
  return hidden;
}

/** 已置顶的那一组：按排序方式，手排过的按手排的先后（新置顶的在最上面）。 */
function pinnedIndices(sorted: readonly SidebarRow[], keep: (row: SidebarRow) => boolean, order: readonly string[] | undefined): number[] {
  const rows = sorted.filter((row) => row.starred && keep(row));
  return arrange(rows, (row) => row.key ?? String(row.index), order).map((row) => row.index);
}

/** 行的先后：按排序方式，降序时反过来；一样时按原来的先后（组件库列表里的位置）。 */
export function compareRows(sort: SortOptions = UPDATED_DESC): (a: SidebarRow, b: SidebarRow) => number {
  const key = (row: SidebarRow) => (sort.by === "created" ? (row.created ?? row.time) : row.time);
  return (a, b) => {
    const order = sort.by === "name" ? a.title.localeCompare(b.title) : key(a) - key(b);
    return (sort.desc ? -order : order) || a.index - b.index;
  };
}

function searcher(projects: readonly ProjectRow[], query: string) {
  const q = query.trim().toLowerCase();
  const known = new Set(projects.map((p) => p.id));
  const groupOf = (row: SidebarRow) => (known.has(row.group) ? row.group : "");
  const named = new Map(projects.map((p) => [p.id, p.title.toLowerCase()]));
  const matches = (row: SidebarRow) => !q || row.title.toLowerCase().includes(q) || !!named.get(groupOf(row))?.includes(q);
  return { q, groupOf, matches };
}

/** 按公司分好的各组；搜索时一条都没命中回 null（侧栏显示「没有结果」）。 */
export function companySections(rows: readonly SidebarRow[], projects: readonly ProjectRow[], options: SectionOptions = {}): SidebarSection[] | null {
  const { q: query, groupOf, matches } = searcher(projects, options.query ?? "");
  const sort = options.sort ?? UPDATED_DESC;
  const sorted = [...rows].sort(compareRows(sort));
  const recentFirst = [...rows].sort(compareRows(UPDATED_DESC));
  const keys = new Map(rows.map((row) => [row.index, row.key ?? String(row.index)]));
  const keyOf = (index: number) => keys.get(index) ?? String(index);

  const sections: SidebarSection[] = [];
  const pinned = pinnedIndices(sorted, matches, options.pinnedOrder);
  if (pinned.length) sections.push({ type: "pinned", key: "pinned", indices: pinned });

  // 公司的先后：上次更新 = 服务端给的最近活动、本页这家对话的时间（含提醒浮顶）里最晚的一个；创建日期 = 这家最新创建的对话；
  // 名称 = 公司名。降序 / 升序同行
  const latest = new Map<string, number>();
  const newest = new Map<string, number>();
  for (const row of recentFirst) {
    const group = groupOf(row);
    if (!group) continue;
    if (!latest.has(group) && Number.isFinite(row.time) && row.time < Number.MAX_SAFE_INTEGER) latest.set(group, row.time);
    const made = row.created ?? row.time;
    if (made > (newest.get(group) ?? Number.NEGATIVE_INFINITY)) newest.set(group, made);
  }
  const activity = (p: ProjectRow) => Math.max(rowActivity(p), latest.get(p.id) ?? 0);
  const companyKey = (p: ProjectRow) => (sort.by === "created" ? (newest.get(p.id) ?? 0) : activity(p));
  const sortedProjects = [...projects].sort((a, b) => {
    const order = sort.by === "name" ? a.title.localeCompare(b.title) : companyKey(a) - companyKey(b);
    return (sort.desc ? -order : order) || a.title.localeCompare(b.title);
  });
  // 手排过：按手排的顺序，新出现的公司排最上面
  const ordered = arrange(sortedProjects, (p) => p.id, options.companyOrder);
  // 默认展开的「前 5 家」只数有对话的公司（测试窗口第 3 轮：手机上没有对话的空公司组排在前面，占掉了 5 个位置，有对话的
  // 各组一打开都收着）；空公司组排在它们之后，默认收着。对话列表还没到（一条对话都还没归到任何公司、服务端也没说哪家还有）时
  // 照公司顺序数前 5 家（复审第 3 轮：公司先到、对话后到的那段时间不能全收着）
  const anyChats = rows.some((row) => !!groupOf(row)) || Object.keys(options.serverMore ?? {}).length > 0;
  let rank = 0;
  let empties = 0;
  for (const project of ordered) {
    const all = sorted.filter((row) => !row.starred && groupOf(row) === project.id && matches(row)).map((row) => row.index);
    if (query && !all.length && !project.title.toLowerCase().includes(query)) continue;
    if (!query && !all.length && options.empty === false) continue;
    const first = query ? all : all.slice(0, options.shown?.[project.id] ?? FIRST_SHOWN);
    const kept = options.keep?.length ? all.slice(first.length).filter((index) => options.keep!.includes(keyOf(index))) : [];
    const indices = kept.length ? [...first, ...kept] : first;
    const serverHas = project.id in (options.serverMore ?? {});
    const more = !query && (all.length > indices.length || serverHas);
    const order = !anyChats || all.length || serverHas ? rank++ : OPEN_FIRST + empties++;
    sections.push({ type: "project", key: `project:${project.id}`, project, rank: order, indices, all, more });
  }

  const recents = sorted.filter((row) => !row.starred && !groupOf(row) && matches(row)).map((row) => row.index);
  if (recents.length) sections.push({ type: "recents", key: "recents", indices: recents });

  if (query && !sections.length) return null;
  return sections;
}

/** 拖对话时眼前没有「最近」组：补一个空的（好拖出公司；同 companySections 的排法，在最后）。 */
export function withRecents(sections: SidebarSection[]): SidebarSection[] {
  return sections.some((section) => section.type === "recents") ? sections : [...sections, { type: "recents", key: "recents", indices: [] }];
}

/** 拖动中对话的空档去了别的组（按公司分组，共用拖动的 onPlace）：把这一行从原来那组拿掉，按排序方式排进 to（公司编号，"" = 最近）
 *  那一组——就是它移过去以后的位置；那组「显示更多」之外的放在已显示的最后。各组的名次、展开、「显示更多」不变（只是摆法）；
 *  去最近而眼前没有最近组时补一个。找不到那一组（不在眼前）回原样。 */
export function liftRow(
  sections: SidebarSection[],
  rows: readonly SidebarRow[],
  index: number,
  to: string,
  sort: SortOptions = UPDATED_DESC,
): SidebarSection[] {
  const lifted = rows.find((row) => row.index === index);
  const base = to ? sections : withRecents(sections);
  const target = base.find((section) => (to ? section.type === "project" && section.project.id === to : section.type === "recents"));
  if (!lifted || !target) return sections;
  const byIndex = new Map(rows.map((row) => [row.index, row]));
  const compare = compareRows(sort);
  const order = (a: number, b: number) => {
    const x = byIndex.get(a);
    const y = byIndex.get(b);
    return x && y ? compare(x, y) : a - b;
  };
  const without = (list: readonly number[]) => list.filter((at) => at !== index);
  return base.map((section) => {
    if (section.type === "pinned" || section.type === "month") return section;
    const indices = without(section.indices);
    if (section !== target) {
      return section.type === "project" ? { ...section, indices, all: without(section.all) } : { ...section, indices };
    }
    const rest = without(section.type === "project" ? section.all : section.indices);
    const all = [...rest, index].sort(order);
    const at = all.indexOf(index);
    // 已显示的前面那一段里按位置插；之外的放进接在后面留着的那一段（companySections 的 keep），在那一段里同样按排序方式排
    // ——放下后 companySections 也这样排，落位后不再和留着的行换位
    let shown = 0;
    while (shown < indices.length && indices[shown] === rest[shown]) shown++;
    const tail = [...indices.slice(shown), index].sort(order);
    const placed = at < shown ? [...indices.slice(0, at), index, ...indices.slice(at)] : [...indices.slice(0, shown), ...tail];
    return section.type === "project" ? { ...section, indices: placed, all } : { ...section, indices: placed };
  });
}

/** 放下后要移进 section（已按 liftRow 摆好的那一组，拖的那一行 dragged 已排在里面）的各行（moving：拖的这一条和多选整批的其余
 *  各条）怎么留在眼前：那组眼前原有的行一条都不被挤进「显示更多」，移进来的也不落位后当场消失。排在眼前最后一条之前的每一条
 *  都让那组多显示一条（bump，projects.ts 的 showOneMore，同「显示更多」的计数）；排在「显示更多」之外的单独留着（keep，
 *  companySections 的 keep，接在已显示的最后、按排序方式排）。shown 是这组此刻显示几条（shownOf）。搜索时（全显示）、最近（不限
 *  条数）、去的组收着（拖的那一行没排进去）都不用。 */
export function keepInView(
  section: SidebarSection | undefined,
  dragged: number,
  moving: readonly number[],
  rows: readonly SidebarRow[],
  shown: number,
  searching: boolean,
  sort: SortOptions = UPDATED_DESC,
): { bump: number[]; keep: number[] } {
  const none = { bump: [], keep: [] };
  if (searching || section?.type !== "project" || !section.indices.includes(dragged)) return none;
  const byIndex = new Map(rows.map((row) => [row.index, row]));
  const compare = compareRows(sort);
  const order = (a: number, b: number) => {
    const x = byIndex.get(a);
    const y = byIndex.get(b);
    return x && y ? compare(x, y) : a - b;
  };
  const incoming = new Set(moving);
  const old = section.all.filter((index) => !incoming.has(index));
  const all = [...old, ...incoming].sort(order);
  // 眼前原有的最后一条前面每插进一条，它就往后挪一位；挪出已显示的那一段几位，就多显示几条（原来那组没满时先占空位）
  const seen = old.slice(0, shown);
  const last = seen.at(-1);
  const before = last === undefined ? [] : [...incoming].filter((index) => all.indexOf(index) < all.indexOf(last)).sort(order);
  const bump = before.slice(0, Math.max(0, seen.length + before.length - shown));
  const visible = shown + bump.length;
  return { bump, keep: [...incoming].filter((index) => all.indexOf(index) >= visible) };
}

/** 搜索时这一行移到 to（公司编号，"" = 最近）以后还在不在结果里（标题或那家公司名命中）；不搜索时都在。拖动时不在的不给它在
 *  那一组排空档（落位后会从结果里消失），按收着的组处理：组头高亮、落到组头并收起。 */
export function matchesAfterMove(row: SidebarRow, to: string, projects: readonly ProjectRow[], query: string): boolean {
  return searcher(projects, query).matches({ ...row, group: to });
}

/** 按处理月份：已置顶 → 各月（降序新的在前，升序旧的在前）→ 未指定月份（键 month:）。 */
export function monthSections(rows: readonly SidebarRow[], projects: readonly ProjectRow[], options: SectionOptions = {}): SidebarSection[] | null {
  const { q: query, matches } = searcher(projects, options.query ?? "");
  const sort = options.sort ?? UPDATED_DESC;
  const sorted = [...rows].sort(compareRows(sort)).filter(matches);
  const sections: SidebarSection[] = [];
  const pinned = pinnedIndices(sorted, () => true, options.pinnedOrder);
  if (pinned.length) sections.push({ type: "pinned", key: "pinned", indices: pinned });
  const months = new Map<string, number[]>();
  for (const row of sorted) {
    if (row.starred) continue;
    const period = row.period ?? "";
    months.set(period, [...(months.get(period) ?? []), row.index]);
  }
  const periods = [...months.keys()].filter(Boolean).sort();
  if (sort.desc) periods.reverse();
  if (months.has("")) periods.push("");
  for (const period of periods) sections.push({ type: "month", key: `month:${period}`, period, indices: months.get(period)! });
  if (query && !sections.length) return null;
  return sections;
}

/** 不分组：已置顶 → 其余全部（同一个「最近」组，可折叠）。 */
export function flatSections(rows: readonly SidebarRow[], projects: readonly ProjectRow[], options: SectionOptions = {}): SidebarSection[] | null {
  const { q: query, matches } = searcher(projects, options.query ?? "");
  const sorted = [...rows].sort(compareRows(options.sort ?? UPDATED_DESC)).filter(matches);
  const sections: SidebarSection[] = [];
  const pinned = pinnedIndices(sorted, () => true, options.pinnedOrder);
  if (pinned.length) sections.push({ type: "pinned", key: "pinned", indices: pinned });
  const rest = sorted.filter((row) => !row.starred).map((row) => row.index);
  if (rest.length) sections.push({ type: "recents", key: "recents", indices: rest });
  if (query && !sections.length) return null;
  return sections;
}

/** 组开着没有：搜索时都开；手动收起的（project:<编号>）收着；前 5 家默认开，其余要手动打开过（open:project:<编号>）。 */
export function groupOpen(section: { key: string; rank?: number }, collapsed: ReadonlySet<string>, searching: boolean): boolean {
  if (searching) return true;
  if (collapsed.has(section.key)) return false;
  if (section.rank === undefined || section.rank < OPEN_FIRST) return true;
  return collapsed.has(`open:${section.key}`);
}

/** 点组名：开着就收起（记 project:<编号>），收着就打开（前 5 家之外的记 open:project:<编号>）。 */
export function toggleGroup(section: { key: string; rank?: number }, collapsed: ReadonlySet<string>): Set<string> {
  const next = new Set(collapsed);
  const open = groupOpen(section, collapsed, false);
  next.delete(section.key);
  next.delete(`open:${section.key}`);
  if (open) next.add(section.key);
  else if (section.rank !== undefined && section.rank >= OPEN_FIRST) next.add(`open:${section.key}`);
  return next;
}
