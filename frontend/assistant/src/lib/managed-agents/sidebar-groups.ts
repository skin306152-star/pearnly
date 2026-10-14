// 按公司分组时侧栏的各组（纯函数，thread-list.tsx 渲染）：
//
//   已置顶   — 置顶的对话只在这里（不论属于哪家公司），取消置顶回到所属公司；没有就不显示
//   各公司   — 我有对话的公司，按组内对话的最近活动排（提醒浮顶的也算）；前 5 家默认展开；每组先显示 5 条，
//              「显示更多」每次加 10 条
//   最近     — 不属于任何公司的对话（不再按日子分组）
//
// 搜索同时匹配对话标题和公司名（公司名命中时这家的对话都算）；搜索时每组都显示全部命中的，没命中的组不显示。

import { FIRST_SHOWN, OPEN_FIRST, type ProjectRow, rowActivity } from "./projects";

/** 侧栏的一行对话：index 是组件库 threadIds 里的位置。 */
export type SidebarRow = { index: number; title: string; starred: boolean; time: number; group: string };

export type SidebarSection =
  | { type: "pinned"; key: "pinned"; indices: number[] }
  | { type: "project"; key: string; project: ProjectRow; rank: number; indices: number[]; all: number[]; more: boolean }
  | { type: "recents"; key: "recents"; indices: number[] };

export type SectionOptions = {
  query?: string;
  shown?: Readonly<Record<string, number>>;
  /** 服务端还有更多的公司（复合游标里的 groups）。 */
  serverMore?: Readonly<Record<string, string>>;
};

/** 分好的各组；搜索时一条都没命中回 null（侧栏显示「没有结果」）。 */
export function companySections(rows: readonly SidebarRow[], projects: readonly ProjectRow[], options: SectionOptions = {}): SidebarSection[] | null {
  const query = (options.query ?? "").trim().toLowerCase();
  const known = new Set(projects.map((p) => p.id));
  const groupOf = (row: SidebarRow) => (known.has(row.group) ? row.group : "");
  const named = new Map(projects.map((p) => [p.id, p.title.toLowerCase()]));
  const matches = (row: SidebarRow) =>
    !query || row.title.toLowerCase().includes(query) || !!named.get(groupOf(row))?.includes(query);
  const sorted = [...rows].sort((a, b) => b.time - a.time);

  const sections: SidebarSection[] = [];
  const pinned = sorted.filter((row) => row.starred && matches(row)).map((row) => row.index);
  if (pinned.length) sections.push({ type: "pinned", key: "pinned", indices: pinned });

  // 公司按组内最近活动排：服务端给的最近活动、本页这家对话的时间（含提醒浮顶）里最晚的一个
  const latest = new Map<string, number>();
  for (const row of sorted) {
    const group = groupOf(row);
    if (group && !latest.has(group) && Number.isFinite(row.time) && row.time < Number.MAX_SAFE_INTEGER) latest.set(group, row.time);
  }
  const activity = (p: ProjectRow) => Math.max(rowActivity(p), latest.get(p.id) ?? 0);
  const ordered = [...projects].sort((a, b) => activity(b) - activity(a) || a.title.localeCompare(b.title));
  let rank = 0;
  for (const project of ordered) {
    const all = sorted.filter((row) => !row.starred && groupOf(row) === project.id && matches(row)).map((row) => row.index);
    if (query && !all.length && !project.title.toLowerCase().includes(query)) continue;
    const indices = query ? all : all.slice(0, options.shown?.[project.id] ?? FIRST_SHOWN);
    const more = !query && (all.length > indices.length || project.id in (options.serverMore ?? {}));
    sections.push({ type: "project", key: `project:${project.id}`, project, rank: rank++, indices, all, more });
  }

  const recents = sorted.filter((row) => !row.starred && !groupOf(row) && matches(row)).map((row) => row.index);
  if (recents.length) sections.push({ type: "recents", key: "recents", indices: recents });

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
