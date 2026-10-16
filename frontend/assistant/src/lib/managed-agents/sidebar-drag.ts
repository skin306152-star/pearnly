// 侧栏拖动的规则（thread-list.tsx 用；拖动本身是共用拖动 components/ui/drag.tsx）：
//
//   对话行 — 按公司分组时拖到别的公司组（组头或这组的行）= 移到那家，拖到「最近」= 移出公司；原来那一组不是落点（拖回原组不动）
//   置顶   — 已置顶的对话在置顶那一列里调先后
//   公司组 — 在公司组那一列里调先后
//
// 移动同「移到 ▸」（月份、分店留空，不改置顶，fileSession）；多选里拖其中一条 = 整批，一条做完再下一条（不并发，同批量「移到…」），
// 做完清空多选；出错写在侧栏出错行（服务端原文）。

import type { DragData } from "@/components/ui/drag";
import { shownError } from "@/lib/frappe";
import { clearSelection, runInOrder } from "@/lib/sidebar-keys";
import { fileSession, setError } from "./projects";

export const pinDrag = (local: string) => `pin:${local}`;
export const rowDrag = (local: string) => `row:${local}`;
export const companyDrag = (id: string) => `company:${id}`;
export const RECENTS_TARGET = "group:recents";
/** 拖动编号去掉前缀（pin: / row: / company:）。 */
export const fromDrag = (id: string) => id.slice(id.indexOf(":") + 1);

/** 侧栏里拿着什么能放到哪：对话行 → 别的公司组 / 最近（不是原来那一组）；置顶、公司组 → 同一列里调序。 */
export function sidebarAccepts(active: DragData, target: DragData): boolean {
  if (active.kind === "row") return (target.kind === "company" || target.kind === "recents") && (target.group ?? "") !== (active.group ?? "");
  return !!active.list && active.list === target.list;
}

/** 拖到别的公司 / 最近：一条做完再下一条，某一条失败不挡后面的，第一个错误写在侧栏出错行；整批的做完清空多选。 */
export async function moveDragged(sessions: readonly string[], project: string, bulk: boolean): Promise<void> {
  await runInOrder(sessions, (id) => fileSession(id, { project, period: "", scope: "" }), (error) => setError(shownError(error)));
  if (bulk) clearSelection();
}
