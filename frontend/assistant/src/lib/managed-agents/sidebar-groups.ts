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
// 搜索同时匹配对话标题和公司名（公司名命中时这家的对话都算）；搜索时每组都显示全部命中的，没命中的组不显示。

import { FIRST_SHOWN, OPEN_FIRST, type ProjectRow, rowActivity } from "./projects";
import type { SortBy } from "./sidebar-view";

/** 侧栏的一行对话：index 是组件库 threadIds 里的位置；created 创建时间（列表带回的，本页新建的没有）、period 处理月份。 */
export type SidebarRow = { index: number; title: string; starred: boolean; time: number; group: string; created?: number; period?: string };

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
};

const UPDATED_DESC: SortOptions = { by: "updated", desc: true };

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

  const sections: SidebarSection[] = [];
  const pinned = sorted.filter((row) => row.starred && matches(row)).map((row) => row.index);
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
  const ordered = [...projects].sort((a, b) => {
    const order = sort.by === "name" ? a.title.localeCompare(b.title) : companyKey(a) - companyKey(b);
    return (sort.desc ? -order : order) || a.title.localeCompare(b.title);
  });
  // 默认展开的「前 5 家」只数有对话的公司（测试窗口第 3 轮：手机上没有对话的空公司组排在前面，占掉了 5 个位置，有对话的
  // 各组一打开都收着）；空公司组排在它们之后，默认收着
  let rank = 0;
  let empties = 0;
  for (const project of ordered) {
    const all = sorted.filter((row) => !row.starred && groupOf(row) === project.id && matches(row)).map((row) => row.index);
    if (query && !all.length && !project.title.toLowerCase().includes(query)) continue;
    if (!query && !all.length && options.empty === false) continue;
    const indices = query ? all : all.slice(0, options.shown?.[project.id] ?? FIRST_SHOWN);
    const serverHas = project.id in (options.serverMore ?? {});
    const more = !query && (all.length > indices.length || serverHas);
    const order = all.length || serverHas ? rank++ : OPEN_FIRST + empties++;
    sections.push({ type: "project", key: `project:${project.id}`, project, rank: order, indices, all, more });
  }

  const recents = sorted.filter((row) => !row.starred && !groupOf(row) && matches(row)).map((row) => row.index);
  if (recents.length) sections.push({ type: "recents", key: "recents", indices: recents });

  if (query && !sections.length) return null;
  return sections;
}

/** 按处理月份：已置顶 → 各月（降序新的在前，升序旧的在前）→ 未指定月份（键 month:）。 */
export function monthSections(rows: readonly SidebarRow[], projects: readonly ProjectRow[], options: SectionOptions = {}): SidebarSection[] | null {
  const { q: query, matches } = searcher(projects, options.query ?? "");
  const sort = options.sort ?? UPDATED_DESC;
  const sorted = [...rows].sort(compareRows(sort)).filter(matches);
  const sections: SidebarSection[] = [];
  const pinned = sorted.filter((row) => row.starred).map((row) => row.index);
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
  const pinned = sorted.filter((row) => row.starred).map((row) => row.index);
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
