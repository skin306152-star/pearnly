// 侧栏的键盘与多选（docs/work/2026-10-16-cc-shell-design.md 3.7、3.8，同 Claude Code 侧栏）：
//
//   roving 焦点 — 导航行、组标题开关、对话行是一组（标 data-roving），整组只有一个 tabIndex=0：Tab 进出侧栏只停一次，
//                 ↑ ↓ 移动、Home / End 到头尾；组标题开关上 ← 收起、→ 展开，对话行上 ← 回到所在组的标题；Enter 是按钮本身
//   多选       — Shift 点一段（可跨组，按眼前的先后）、⌘ / Ctrl 点单条切换；Shift + ↑ ↓ 扩大；Esc 清空。不出批量栏：
//                行「···」菜单在选中两条以上时换成批量项（thread-list.tsx），用现有接口逐条顺序执行（runInOrder，不并发）
//   跳转       — 只在 CC 桌面版窗口（<html data-cc-variant="desktop">，浏览器里 ⌘ / Ctrl 加数字是切标签页）：
//                ⌘ / Ctrl + 1–9 打开眼前第 n 条对话；按住修饰键约 300ms，前 9 行行尾的装饰换成数字（data-jump，globals.css）
//
// 先后、可见行都按页面上实际排出来的（DOM 顺序）：折叠的组、搜索筛掉的不在里面。触屏没有这些（多选、键盘只在电脑上）。

import { useCallback, useRef } from "react";
import { create } from "zustand";
import { isDragEscape, isDragging, OPEN_LAYERS } from "@/lib/layers";

// ---------------------------------------------------------------- 多选
export type Selection = { ids: string[]; anchor: string | null };

/** 选中的对话（组件库的本页编号，同一段对话两行数据时按行算）。 */
export const useSidebarSelection = create<Selection>(() => ({ ids: [], anchor: null }));

/** 从 anchor 到 target（含两头）的一段，按 order 的先后；anchor 不在里面时只选 target。 */
export function rangeBetween(order: readonly string[], anchor: string | null, target: string): string[] {
  const end = order.indexOf(target);
  if (end < 0) return [];
  const start = anchor === null ? -1 : order.indexOf(anchor);
  if (start < 0) return [target];
  return order.slice(Math.min(start, end), Math.max(start, end) + 1);
}

/** ⌘ / Ctrl 点：在就去掉、不在就加上（保持原来的先后，新的放最后）。 */
export function toggleIn(ids: readonly string[], id: string): string[] {
  return ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id];
}

/** Shift 点 / Shift + ↑ ↓：从起点选到 target。current：正开着的那一行（还没有起点时从它算起，同 Claude Code）。 */
export function selectRange(order: readonly string[], target: string, current: string | null = null) {
  const { anchor, ids } = useSidebarSelection.getState();
  // 还没有起点：正开着的那一行（不在眼前就是点的这一行）算起点
  const from = anchor ?? (ids.length ? ids[0]! : current && order.includes(current) ? current : null);
  useSidebarSelection.setState({ ids: rangeBetween(order, from, target), anchor: from ?? target });
}

export function toggleSelected(id: string) {
  const { ids } = useSidebarSelection.getState();
  useSidebarSelection.setState({ ids: toggleIn(ids, id), anchor: id });
}

/** 选中的里只留眼前还排着的行（rendered：页面上的对话行，按 rowOrder）：收起分组、搜索筛掉、换分组方式、别处删掉之后，
 *  看不见的不再算在批量操作里。起点不在了一并清掉。没有变化回 null。 */
export function keepRendered(selection: Selection, rendered: readonly string[]): Selection | null {
  const ids = selection.ids.filter((id) => rendered.includes(id));
  const anchor = selection.anchor !== null && rendered.includes(selection.anchor) ? selection.anchor : null;
  if (ids.length === selection.ids.length && anchor === selection.anchor) return null;
  return { ids, anchor: ids.length ? anchor : null };
}

export function clearSelection() {
  const { ids, anchor } = useSidebarSelection.getState();
  if (ids.length || anchor) useSidebarSelection.setState({ ids: [], anchor: null });
}

/** 批量操作：一条做完再做下一条（F7 并发写出现过数据库死锁）；某一条失败不挡后面的，第一个错误交给 onError。 */
export async function runInOrder<T>(items: readonly T[], run: (item: T) => unknown, onError?: (error: unknown) => void): Promise<number> {
  let done = 0;
  let failed = false;
  for (const item of items) {
    try {
      await run(item);
      done++;
    } catch (error) {
      if (!failed) onError?.(error);
      failed = true;
    }
  }
  return done;
}

// ---------------------------------------------------------------- 菜单里的单字母
type Letter = "KeyP" | "KeyR" | "KeyD";
type LetterEvent = { code: string; target: EventTarget | null; currentTarget: EventTarget | null; metaKey: boolean; ctrlKey: boolean; altKey: boolean; preventDefault(): void };

/** 这个键是不是按在这一层菜单本身：子菜单（宽屏旁边展开的 SubContent、窄屏原菜单里换上的那一页）里的键沿组件树冒上来时
 *  不算——组件库在子菜单里按字母跳转不拦冒泡，否则在子菜单里按 D 想跳到某一项会删掉对话（原来行菜单里的「移到 ▸」，10-17 已删）。 */
export function ownMenuKey(target: EventTarget | null, menu: EventTarget | null): boolean {
  const el = target as Element | null;
  if (!el || typeof el.closest !== "function") return false;
  return el.closest('[role="menu"]') === menu && !el.closest('[data-slot="cc-menu-subpage"]');
}

/** 行菜单开着时的单字母快捷键（按物理键认，泰文等键盘下也一样）：只认按在这一层菜单本身的（ownMenuKey）。 */
export function menuLetterKeys(actions: Partial<Record<Letter, () => void>>, close: () => void) {
  return (event: LetterEvent) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const run = actions[event.code as Letter];
    if (!run || !ownMenuKey(event.target, event.currentTarget)) return;
    event.preventDefault();
    close();
    run();
  };
}

// ---------------------------------------------------------------- 方向键
/** ↑ ↓ Home End 之后落到第几个（到头不绕回）；不是这几个键回 null。 */
export function nextIndex(key: string, index: number, count: number): number | null {
  if (!count) return null;
  if (key === "ArrowDown") return Math.min(index + 1, count - 1);
  if (key === "ArrowUp") return Math.max(index - 1, 0);
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

/** ⌘ / Ctrl + 数字的那个数字（1–9，按物理键认：泰文等键盘下 key 不是数字）；不是回 0。 */
export function jumpDigit(code: string): number {
  const match = /^(?:Digit|Numpad)([1-9])$/.exec(code);
  return match ? Number(match[1]) : 0;
}

const ROW = '[data-slot="aui_thread-list-item"]';
const ROW_TRIGGER = '[data-slot="aui_thread-list-item-trigger"]';
const HINT_DELAY_MS = 300;

/** 眼前的对话行（本页编号），按页面上的先后。 */
export function rowOrder(from: Element | null): string[] {
  const root = from?.closest('[data-slot="aui_thread-list-root"]') ?? from;
  if (!root) return [];
  return [...root.querySelectorAll<HTMLElement>(`${ROW}[data-row-id]`)].map((row) => row.dataset.rowId!).filter(Boolean);
}

/** 眼前正开着的那一行（本页编号）；没有回 null。 */
export function currentRow(from: Element | null): string | null {
  const root = from?.closest('[data-slot="aui_thread-list-root"]') ?? from;
  return root?.querySelector<HTMLElement>(`${ROW}[aria-current="true"][data-row-id]`)?.dataset.rowId ?? null;
}

const isDesktopWindow = () => document.documentElement.dataset.ccVariant === "desktop";

/** 挂在侧栏内容上（返回的回调当 ref）：roving 焦点、方向键、组标题右侧操作区、Esc 清空多选、桌面窗口的 ⌘ / Ctrl + 1–9 与数字提示。
 *  companies：有公司分组的产品（Cowork）。多选（Shift + ↑ ↓）与 ⌘ / Ctrl + 1–9 只在这时有；roving 焦点与方向键是外壳统一的，
 *  ERP / DMS 的侧栏也有（日期分组开关同样进 roving，行尾「···」用菜单键或 Shift+F10 打开）。 */
export function useSidebarKeys(companies: boolean): (node: HTMLElement | null) => void {
  const detach = useRef<(() => void) | null>(null);
  const enabled = useRef(companies);
  enabled.current = companies;
  return useCallback((node: HTMLElement | null) => {
    detach.current?.();
    detach.current = node ? attach(node, () => enabled.current) : null;
  }, []);
}

const GROUP_TOGGLE = "[data-roving][aria-expanded]";
const GROUP_ACTION = ".cc-group-actions > :is(button, [role='button'])";

/** 组标题右侧操作区里眼前的按钮（待处理数、···、分组和排序、新建；悬停才出的没出来时不算）。 */
function groupActions(toggle: Element | null): HTMLElement[] {
  const row = toggle?.closest(".cc-group-row");
  if (!row) return [];
  return [...row.querySelectorAll<HTMLElement>(GROUP_ACTION)].filter((el) => el.getClientRects().length > 0 && !el.hasAttribute("disabled"));
}

function attach(root: HTMLElement, companies: () => boolean): () => void {
  let current: HTMLElement | null = null;
  const items = () =>
    [...root.querySelectorAll<HTMLElement>("[data-roving]")].filter((el) => !el.closest("[hidden]") && el.getClientRects().length > 0);
  // 只有一个能 Tab 到：上次停的那个；它不在了就落到当前页（选中的导航行 / 正开着的对话），再不然第一个
  const sync = () => {
    const list = items();
    if (!current || !list.includes(current)) {
      current =
        list.find((el) => el.matches(ROW_TRIGGER) && el.closest(`${ROW}[aria-current="true"]`)) ??
        list.find((el) => el.matches("[data-active]")) ??
        list[0] ??
        null;
    }
    for (const el of list) {
      const index = el === current ? 0 : -1;
      if (el.tabIndex !== index) el.tabIndex = index;
    }
  };
  sync();
  // 侧栏有增删：合到下一帧做一次（对话多时不在每次变动都排版）——重排 roving、选中的里去掉已不在眼前的行
  let frame = 0;
  const onMutation = () => {
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      sync();
      const selection = useSidebarSelection.getState();
      if (!selection.ids.length && selection.anchor === null) return;
      const kept = keepRendered(selection, rowOrder(root));
      if (kept) useSidebarSelection.setState(kept);
    });
  };
  const observer = new MutationObserver(onMutation);
  observer.observe(root, { childList: true, subtree: true });

  const onFocusIn = (event: FocusEvent) => {
    const item = (event.target as HTMLElement).closest<HTMLElement>("[data-roving]");
    if (item && root.contains(item) && item !== current) {
      current = item;
      sync();
    }
  };

  const focus = (el: HTMLElement | undefined) => {
    if (!el) return;
    current = el;
    sync();
    el.focus();
    el.scrollIntoView({ block: "nearest" });
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const target = event.target as HTMLElement;
    // 键盘拖动中（空格拿起了对话、组头）：方向键归拖动，不移焦点
    if (event.altKey || event.metaKey || event.ctrlKey || isDragging()) return;
    if (target.matches(GROUP_ACTION)) return onActionKey(event, target);
    if (!target.matches("[data-roving]")) return;
    const list = items();
    const index = list.indexOf(target);
    if (index < 0) return;
    const expanded = target.getAttribute("aria-expanded");
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      if (expanded !== null) {
        // 组标题开关：← 收起、→ 展开；已展开时 → 进右侧操作区（分组和排序、···、新建、待处理数，键盘只能从这里到）
        if ((event.key === "ArrowLeft") === (expanded === "true")) target.click();
        else if (event.key === "ArrowRight") groupActions(target)[0]?.focus();
      } else if (event.key === "ArrowLeft" && target.matches(ROW_TRIGGER)) {
        // 对话行：← 回到所在组的标题
        focus(list.slice(0, index).reverse().find((el) => el.hasAttribute("aria-expanded")));
      } else return;
      event.preventDefault();
      return;
    }
    const next = nextIndex(event.key, index, list.length);
    if (next === null) return;
    event.preventDefault();
    const to = list[next]!;
    // Shift + ↑ ↓：从起点选到这一行（只在对话行之间）
    if (event.shiftKey && companies() && to.matches(ROW_TRIGGER) && target.matches(ROW_TRIGGER)) {
      const id = (el: HTMLElement) => el.closest<HTMLElement>(ROW)?.dataset.rowId;
      const from = id(target);
      const toId = id(to);
      if (from && toId) {
        if (!useSidebarSelection.getState().anchor) useSidebarSelection.setState({ anchor: from, ids: [from] });
        selectRange(rowOrder(root), toId);
      }
    }
    focus(to);
  };

  // 组标题右侧操作区：← → 在操作之间走（最左再 ← 回到组标题），↑ ↓ 回到 roving 那一组（从组标题算上一个 / 下一个）
  const onActionKey = (event: KeyboardEvent, target: HTMLElement) => {
    const toggle = target.closest(".cc-group-row")?.querySelector<HTMLElement>(GROUP_TOGGLE);
    if (!toggle || !root.contains(toggle)) return;
    const actions = groupActions(toggle);
    const index = actions.indexOf(target);
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const next = index + (event.key === "ArrowRight" ? 1 : -1);
      if (next < 0) focus(toggle);
      else actions[next]?.focus();
      return;
    }
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const list = items();
    const at = list.indexOf(toggle);
    focus(list[nextIndex(event.key, at, list.length) ?? at]);
  };

  // Esc：清空多选（菜单、弹窗开着时 Esc 只关它们；拖动中——鼠标或键盘拖，会话栏卡片或侧栏——Esc 只取消拖动，取消拖动的那一下
  // Esc 本身也不算，lib/layers.ts 的 isDragEscape）；弹窗、菜单开着时 ⌘ / Ctrl + 数字也不切换后面的对话。
  // 一次只关一层：这个 Esc 已经被上面一层用掉（关了浮着的会话栏、菜单，取消了改名、编辑、拖动——它们都标记已处理）就不清多选
  const onWindowKey = (event: KeyboardEvent) => {
    const layered = () => !!document.querySelector(OPEN_LAYERS);
    if (event.key === "Escape") {
      if (useSidebarSelection.getState().ids.length && !event.isComposing && !event.defaultPrevented && !layered() && !isDragEscape(event)) {
        clearSelection();
        event.preventDefault();
      }
      return;
    }
    if (!isDesktopWindow() || !companies()) return;
    if (event.key === "Meta" || event.key === "Control") {
      if (!event.repeat) startHints();
      return;
    }
    stopHints();
    const digit = jumpDigit(event.code);
    if (!digit || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey || layered()) return;
    const trigger = [...root.querySelectorAll<HTMLElement>(ROW_TRIGGER)].filter((el) => el.getClientRects().length > 0)[digit - 1];
    if (!trigger) return;
    event.preventDefault();
    trigger.click();
  };

  // 按住 ⌘ / Ctrl 约 300ms：前 9 行行尾换成数字
  let timer: number | null = null;
  const startHints = () => {
    stopHints();
    timer = window.setTimeout(() => {
      timer = null;
      const rows = [...root.querySelectorAll<HTMLElement>(ROW)].filter((el) => el.getClientRects().length > 0).slice(0, 9);
      rows.forEach((row, i) => row.querySelector<HTMLElement>(".cc-row-end")?.setAttribute("data-jump", String(i + 1)));
      root.setAttribute("data-jump-hints", "");
    }, HINT_DELAY_MS);
  };
  const stopHints = () => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    if (!root.hasAttribute("data-jump-hints")) return;
    root.removeAttribute("data-jump-hints");
    for (const el of root.querySelectorAll("[data-jump]")) el.removeAttribute("data-jump");
  };
  const onKeyUp = (event: KeyboardEvent) => {
    if (event.key === "Meta" || event.key === "Control") stopHints();
  };

  root.addEventListener("focusin", onFocusIn);
  root.addEventListener("keydown", onKeyDown);
  window.addEventListener("keydown", onWindowKey);
  window.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", stopHints);
  return () => {
    observer.disconnect();
    if (frame) window.cancelAnimationFrame(frame);
    stopHints();
    root.removeEventListener("focusin", onFocusIn);
    root.removeEventListener("keydown", onKeyDown);
    window.removeEventListener("keydown", onWindowKey);
    window.removeEventListener("keyup", onKeyUp);
    window.removeEventListener("blur", stopHints);
  };
}
