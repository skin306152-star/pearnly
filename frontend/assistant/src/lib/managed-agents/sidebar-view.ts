// 侧栏的「分组和排序」（docs/work/2026-10-16-cc-shell-design.md 3.6、4.2；同 Claude Code 侧栏同名菜单）。只在有公司分组的产品
// （Cowork）出现，ERP / DMS 的侧栏不变：
//
//   分组依据 — 公司（默认）/ 处理月份 / 无
//   排序方式 — 上次更新（默认）/ 创建日期 / 名称，降序（默认）/ 升序
//   显示     — 元数据（行的第二行「9 月 · 分店 00012」，默认关：行高同 Code 的 32，设计文档 3.5）/ 空分组（没有对话的公司组，默认开）
//
// 按人记住、跨设备同步（lib/prefs-sync.ts：存在服务端当前用户的那份里）；本机仍记一份（localStorage，键带用户）让打开时立刻
// 生效、离线照样能用，存不了（无痕窗口）时本次照样生效。
// 按公司分组时列表用分组接口（grouped=1）；按月份、不分组时用普通的按上次更新分页的列表（服务端每行同样带公司、月份、分店），
// 换分组方式整表重拉（list-sync.ts）。「创建日期」只排已经加载的行：服务端分页仍按上次更新（列表行带 created_at），不加服务端排序。

import { create } from "zustand";
import { prefChanged, registerPref } from "@/lib/prefs-sync";
import { clearSidebarOrder } from "./sidebar-order";

export type GroupBy = "company" | "month" | "none";
export type SortBy = "updated" | "created" | "name";
export type SidebarView = { by: GroupBy; sort: SortBy; desc: boolean; meta: boolean; empty: boolean };

export const DEFAULT_VIEW: SidebarView = { by: "company", sort: "updated", desc: true, meta: false, empty: true };

const GROUPS: readonly GroupBy[] = ["company", "month", "none"];
const SORTS: readonly SortBy[] = ["updated", "created", "name"];

/** 本机记下的值（可能是旧版本写的、被改过的）：认不出的项回默认。 */
export function parseView(raw: unknown): SidebarView {
  const value = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const flag = (key: "desc" | "meta" | "empty") => (typeof value[key] === "boolean" ? (value[key] as boolean) : DEFAULT_VIEW[key]);
  return {
    by: GROUPS.includes(value.by as GroupBy) ? (value.by as GroupBy) : DEFAULT_VIEW.by,
    sort: SORTS.includes(value.sort as SortBy) ? (value.sort as SortBy) : DEFAULT_VIEW.sort,
    desc: flag("desc"),
    meta: flag("meta"),
    empty: flag("empty"),
  };
}

/** 跟默认一样（「恢复默认设置」不可点）。 */
export function isDefaultView(view: SidebarView): boolean {
  return (Object.keys(DEFAULT_VIEW) as Array<keyof SidebarView>).every((key) => view[key] === DEFAULT_VIEW[key]);
}

const USER_KEY = "cc.sidebar.user";
const viewKey = (user: string) => `cc.sidebar.view:${user}`;

function storage(): Storage | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function read(user: string): SidebarView {
  try {
    const raw = storage()?.getItem(viewKey(user));
    return raw ? parseView(JSON.parse(raw)) : DEFAULT_VIEW;
  } catch {
    return DEFAULT_VIEW;
  }
}

// 页面打开时还不知道是谁（资料要等 shell.describe）：先按这台设备上次登录的人读，第一页就按它取，不必取两遍
let owner = (() => {
  try {
    return storage()?.getItem(USER_KEY) ?? "";
  } catch {
    return "";
  }
})();

export const useSidebarView = create<SidebarView>(() => (owner ? read(owner) : DEFAULT_VIEW));

// 这台设备还没记过是谁、资料没到之前就改了「分组和排序」：先只在本页生效，知道是谁时按这次改的存下（不被读回的覆盖）
let unsaved = false;

/** 知道当前用户了：换成这个人记下的（与上次登录的是同一人就不变；之前还不知道是谁时改过的，留着并存给这个人）。 */
export function bindSidebarViewUser(user: string) {
  if (!user || user === owner) return;
  const keep = !owner && unsaved;
  owner = user;
  unsaved = false;
  try {
    storage()?.setItem(USER_KEY, user);
  } catch {
    // 存不了：本次照样按这个人的
  }
  if (keep) setSidebarView({});
  else useSidebarView.setState(read(user), true);
}

/** 换成 next 并记进本机（不同步）。 */
function keep(next: SidebarView) {
  useSidebarView.setState(next, true);
  if (!owner) {
    unsaved = true;
    return;
  }
  try {
    if (isDefaultView(next)) storage()?.removeItem(viewKey(owner));
    else storage()?.setItem(viewKey(owner), JSON.stringify(next));
  } catch {
    // 无痕窗口等存不了：本次照样生效
  }
}

export function setSidebarView(patch: Partial<SidebarView>) {
  keep({ ...useSidebarView.getState(), ...patch });
  prefChanged("view", Object.keys(patch));
}

registerPref("view", {
  read: () => ({ ...useSidebarView.getState() }),
  changed: () => !isDefaultView(useSidebarView.getState()),
  apply: (value, user) => {
    bindSidebarViewUser(user);
    keep(parseView(value));
  },
});

/** 「恢复默认设置」：分组和排序回默认，手排的公司组、置顶与组里的顺序也清掉（回到按排序方式排）。 */
export function resetSidebarView() {
  setSidebarView(DEFAULT_VIEW);
  clearSidebarOrder();
}

/** 列表按公司分组取（分组接口）；按月份、不分组时用普通列表。 */
export function byCompany(): boolean {
  return useSidebarView.getState().by === "company";
}

// ---------------------------------------------------------------- 创建时间（排序「创建日期」）
// 组件库的对话数据里没有创建时间：列表每一页带回的记在这里（不放进 custom：updateCustom 整份替换）
const created = new Map<string, number>();

export function noteCreated(id: string, at: string | null | undefined) {
  const time = at ? new Date(at).getTime() : Number.NaN;
  if (id && !Number.isNaN(time)) created.set(id, time);
}

export function createdOf(id: string | undefined): number | undefined {
  return id ? created.get(id) : undefined;
}
