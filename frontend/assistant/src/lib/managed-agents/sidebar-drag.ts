// 侧栏拖动的规则（thread-list.tsx 用；拖动本身是共用拖动 components/ui/drag.tsx）：
//
//   对话行 — 按公司分组时只在它自己那一组（某家公司，或「最近」）里拖着排先后，或拖进「已置顶」= 置顶它、按落点排进置顶的顺序
//            （用户 10-17：不能跨公司，「移到」已删）。不能落到别的公司组、别的组头；已置顶的行只在置顶里排先后。键盘拖动、
//            Alt+↑/↓ 只在本组里挪（置顶用菜单 / P 键）。多选里拖其中一条：拖进已置顶 = 整批置顶（按侧栏里的先后排进落点）；落在本组
//            = 选中的全都在这一组时整批作为一块排到落点，否则本组不是落点（只能置顶）
//   置顶   — 已置顶的对话在置顶那一列里调先后
//   公司组 — 在公司组那一列里调先后
//
// 每组的行是一列（rowList），已置顶、各组整块另是一个落点（zoneDrag：指针进了已置顶那一块才算拖进置顶，见共用拖动的 useDropZone）。

import type { DragData } from "@/components/ui/drag";
import { moveTo } from "./sidebar-order";

export const pinDrag = (local: string) => `pin:${local}`;
export const rowDrag = (local: string) => `row:${local}`;
export const companyDrag = (id: string) => `company:${id}`;
/** 已置顶那一列；组里的行那一列（group：公司编号，最近 ""）。 */
export const PINS = "pins";
export const rowList = (group: string) => `rows:${group}`;
/** 一列整块的落点（已置顶、各组）。 */
export const zoneDrag = (list: string) => `zone:${list}`;
/** 拖动编号去掉前缀（pin: / row: / company:）。 */
export const fromDrag = (id: string) => id.slice(id.indexOf(":") + 1);

/** 侧栏里拿着什么能放到哪：对话行 → 已置顶（置顶的行或整块）、自己那一组（这组的行；整块只是回到这一组，放下不动）——多选而
 *  选中的不全在这一组时（wholeBatchHere 为 false）这组的行不是落点，只能置顶；置顶、公司组 → 同一列里调序。 */
export function sidebarAccepts(active: DragData, target: DragData, wholeBatchHere = true): boolean {
  if (active.kind === "row") {
    if (target.zone) return target.list === PINS || target.list === active.list;
    if (target.kind === "pin") return target.list === PINS;
    return wholeBatchHere && target.kind === "row" && target.list === active.list;
  }
  return !!active.list && active.list === target.list && !target.zone;
}

/** 一块（多选整批，按侧栏里的先后）排到 anchor（拖的那一条）放下的位置：list 是放下后的先后（anchor 已在落点），block 里的其余
 *  各条从原处拿掉、紧挨着排进来。anchor 不在 list 里原样返回。 */
export function placeBlock(list: readonly string[], anchor: string, block: readonly string[]): string[] {
  if (!list.includes(anchor)) return [...list];
  const others = block.filter((key) => key !== anchor);
  const rest = list.filter((key) => !others.includes(key));
  const at = rest.indexOf(anchor);
  const team = block.includes(anchor) ? block : [anchor, ...others];
  return [...rest.slice(0, at), ...team, ...rest.slice(at + 1)];
}

/** 对话拖进已置顶、还没放下时排在第几位（onOver）：指在置顶的某一行上 = 那一行的位置（在它下半截 = 它后面）；指在已置顶整块上
 *  （组头、空的已置顶）= 上半截最前、下半截最后。pins 是此刻已置顶的各行（不含拖的这一条）。 */
export function pinSpot(pins: readonly string[], over: DragData, below: boolean): number {
  if (over.kind === "pin") {
    const at = pins.indexOf(over.id);
    return at < 0 ? 0 : at + (below ? 1 : 0);
  }
  return below ? pins.length : 0;
}

/** 放下时已置顶的新先后（拖动编号）：pins 是此刻已置顶这一列（拖进来的这一条已排在里面，见 pinGuest；没排进来——已置顶收着——
 *  不在里面），target 是落点。落在置顶的另一行上 = 挪到那一行的位置；落在自己（已排好的位置）、已置顶整块上 = 留在排好的位置，
 *  没排进来的放最上面（新置顶的排最上面）。 */
export function pinDrop(pins: readonly string[], active: string, target: DragData): string[] {
  if (!pins.includes(active)) return [active, ...pins];
  return (target.kind === "pin" && moveTo(pins, active, target.id)) || [...pins];
}
