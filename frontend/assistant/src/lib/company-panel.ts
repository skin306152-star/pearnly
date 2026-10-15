// 右侧会话栏（设计文档 3.13 / 4.4，只在 Cowork 挂了公司的对话里）的状态与数据：
//
//   卡片     默认顺序 公司 / 本月（第 1 期月结台账做好才出现）/ 待处理 / 知识库 / 单据文件夹 / 定时任务 / 记住的选择 /
//            申报草稿 / 输出文件；顺序、折叠、开关记在这台设备（键带用户，跨设备不同步）。拖动或 Alt+↑/↓ 调顺序时只在
//            看得见的卡片之间挪（空卡不显示，也不占位）。
//   开关     宽屏停靠时按记下的「开着」显示；窄屏浮在对话上、手机盖满屏时只在点了之后显示（overlay，换对话就收起），
//            不因为记着「开着」一打开对话就把对话盖住。
//   公司详情 按「公司|月份」缓存：先用缓存显示、后台再取；只在会话栏看得见时（组件挂着）跟着站点事件、回到前台重取
//            （lib/live.ts）；别处改了这家重取，删了清掉。
//   对话分组 公司页「对话」按进行中 / 等你处理 / 已完成：只用现有的真实信号（见 chatState）。
//
// 本文件不画界面；样式在 globals.css。

import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { type Detail, loadDetail, TASK_EVENTS } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { useLiveRefresh } from "@/lib/live";
import type { Activity } from "@/lib/managed-agents/activity";
import type { RunStatus } from "@/lib/managed-agents/events";
import { hintTitle } from "@/lib/managed-agents/projects";
import { onProjectChange } from "@/lib/managed-agents/realtime";

// ---------------------------------------------------------------- 卡片顺序
export const PANEL_CARDS = ["company", "month", "pending", "notes", "folders", "tasks", "choices", "drafts", "outputs"] as const;
export type PanelCard = (typeof PANEL_CARDS)[number];

const isCard = (id: unknown): id is PanelCard => typeof id === "string" && (PANEL_CARDS as readonly string[]).includes(id);

/** 记下的顺序：认不出的丢掉、重复的只留第一个；没记过的卡片（以后新加的）按默认顺序插在它前一张默认卡片后面。 */
export function orderCards(saved: readonly unknown[] | null | undefined): PanelCard[] {
  const out: PanelCard[] = [];
  for (const id of saved ?? []) if (isCard(id) && !out.includes(id)) out.push(id);
  PANEL_CARDS.forEach((id, i) => {
    if (out.includes(id)) return;
    const before = [...PANEL_CARDS.slice(0, i)].reverse().find((prev) => out.includes(prev));
    out.splice(before ? out.indexOf(before) + 1 : 0, 0, id);
  });
  return out;
}

/** 把一张卡片在看得见的卡片里挪 delta 格（Alt+↑ = -1、Alt+↓ = +1；拖动时 = 目标位置 - 原位置）；挪不动原样返回。
 *  shown：此刻看得见的卡片（空卡、本月卡不显示，不算位置）。 */
export function moveCard(order: readonly PanelCard[], id: PanelCard, delta: number, shown: readonly string[] = order): PanelCard[] {
  const visible = order.filter((card) => shown.includes(card));
  const from = visible.indexOf(id);
  const to = from + delta;
  if (from < 0 || !delta || to < 0 || to >= visible.length) return [...order];
  const target = visible[to]!;
  const rest = order.filter((card) => card !== id);
  rest.splice(rest.indexOf(target) + (delta > 0 ? 1 : 0), 0, id);
  return rest;
}

// ---------------------------------------------------------------- 本机记下的偏好（键带用户）
export type PanelPrefs = { open: boolean; order: PanelCard[]; collapsed: PanelCard[] };
export const DEFAULT_PREFS: PanelPrefs = { open: true, order: [...PANEL_CARDS], collapsed: [] };
export const panelKey = (user: string) => `cc.panel.${user || "-"}`;

/** 记下的那份（JSON）；读不出、形状不对的部分用默认值。 */
export function readPrefs(raw: string | null | undefined): PanelPrefs {
  let saved: Partial<Record<keyof PanelPrefs, unknown>> = {};
  try {
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === "object") saved = parsed as typeof saved;
  } catch {
    // 用默认值
  }
  return {
    open: typeof saved.open === "boolean" ? saved.open : DEFAULT_PREFS.open,
    order: orderCards(Array.isArray(saved.order) ? saved.order : null),
    collapsed: Array.isArray(saved.collapsed) ? [...new Set(saved.collapsed.filter(isCard))] : [],
  };
}

type PanelState = PanelPrefs & {
  user: string;
  /** 窄屏浮层、手机全屏：点了才显示，换对话收起（不记）。 */
  overlay: boolean;
  /** 要展开并滚到的那张卡（点了标题栏「公司名 ›」等）；at 让同一张卡再点一次也生效。 */
  focus: { card: PanelCard; at: number } | null;
};

export const usePanel = create<PanelState>(() => ({ ...DEFAULT_PREFS, user: "", overlay: false, focus: null }));

function save() {
  const { user, open, order, collapsed } = usePanel.getState();
  try {
    localStorage.setItem(panelKey(user), JSON.stringify({ open, order, collapsed }));
  } catch {
    // 无痕窗口、存储被禁：本次照样能用，只是不记
  }
}

/** 换了登录的人（或第一次拿到资料）：读这个人在这台设备上记下的。 */
export function loadPanelPrefs(user: string) {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(panelKey(user));
  } catch {
    // 用默认值
  }
  usePanel.setState({ user, ...readPrefs(raw) });
}

/** 宽屏停靠时的开关（记下）；同时收起浮层。 */
export function setPanelOpen(open: boolean) {
  usePanel.setState({ open, overlay: open });
  save();
}

/** 窄屏浮层、手机全屏的开关（不记）。 */
export function setPanelOverlay(overlay: boolean) {
  usePanel.setState({ overlay });
}

export function toggleCard(card: PanelCard) {
  const { collapsed } = usePanel.getState();
  usePanel.setState({ collapsed: collapsed.includes(card) ? collapsed.filter((c) => c !== card) : [...collapsed, card] });
  save();
}

export function setCardOrder(order: readonly PanelCard[]) {
  usePanel.setState({ order: orderCards(order) });
  save();
}

/** 打开会话栏并展开、滚到这张卡（标题栏「公司名 ›」、输入框下巴的公司胶囊、手机胶囊菜单里的公司名）。 */
export function showCard(card: PanelCard = "company") {
  const { collapsed } = usePanel.getState();
  usePanel.setState({ open: true, overlay: true, collapsed: collapsed.filter((c) => c !== card), focus: { card, at: Date.now() } });
  save();
}

export const openCompanyCard = () => showCard("company");

/** 停靠位放不放得下会话栏：对话区宽度够「对话最窄 + 会话栏」才并排，否则浮在对话上。 */
export function dockFits(width: number, chatMin: number, panelWidth: number): boolean {
  return width >= chatMin + panelWidth;
}

/** CSS 变量换成像素（px 或 rem；读不出回 0）。 */
export function cssPx(value: string, rootFont = 16): number {
  const text = value.trim();
  const number = Number.parseFloat(text);
  if (!Number.isFinite(number)) return 0;
  return text.endsWith("rem") ? number * rootFont : number;
}

// ---------------------------------------------------------------- 公司详情（按 公司|月份 缓存）
const details = new Map<string, Detail>();
export const detailKey = (company: string, period: string) => `${company}|${period}`;

/** 别处（公司页）取到的详情也放进来：会话栏打开同一家、同一个月时先显示它。 */
export function rememberDetail(company: string, period: string, detail: Detail) {
  details.set(detailKey(company, period), detail);
}

export function cachedPanelDetail(company: string, period: string): Detail | null {
  return details.get(detailKey(company, period)) ?? null;
}

export function forgetPanelDetail(company: string) {
  for (const key of [...details.keys()]) if (key.startsWith(`${company}|`)) details.delete(key);
}

/** 会话栏用的公司详情：先给缓存，后台再取；挂着期间跟着定时任务服务的站点事件、回到前台、重连重取（只在看得见时挂）。 */
export function usePanelDetail(company: string, period: string) {
  const key = detailKey(company, period);
  const [state, setState] = useState<{ key: string; detail: Detail | null; error: string }>(() => ({
    key,
    detail: details.get(key) ?? null,
    error: "",
  }));
  const asked = useRef(0);
  const reload = useCallback(async () => {
    if (!company) return;
    const mine = ++asked.current;
    try {
      const next = await loadDetail(company, period);
      if (mine !== asked.current) return;
      details.set(key, next);
      hintTitle(next.company.id, next.company.title);
      setState({ key, detail: next, error: "" });
    } catch (e) {
      if (mine === asked.current) setState((before) => ({ key, detail: before.key === key ? before.detail : (details.get(key) ?? null), error: shownError(e) }));
    }
  }, [company, period, key]);
  useEffect(() => {
    setState({ key, detail: details.get(key) ?? null, error: "" });
    void reload();
  }, [key, reload]);
  useLiveRefresh(reload, { events: TASK_EVENTS });
  useEffect(
    () =>
      onProjectChange((change) => {
        if (change.op === "upsert" && change.project.id === company) void reload();
        if (change.op === "delete" && change.id === company) {
          forgetPanelDetail(company);
          setState({ key, detail: null, error: "" });
        }
      }),
    [company, key, reload],
  );
  const current = state.key === key;
  return { detail: current ? state.detail : (details.get(key) ?? null), error: current ? state.error : "", reload };
}

// ---------------------------------------------------------------- 公司页「对话」分组
export type ChatState = "running" | "waiting" | "done";
export const CHAT_STATES: readonly ChatState[] = ["running", "waiting", "done"];

/** 一段对话在公司页「对话」里归哪一组，只用现有的真实信号：
 *  - 等你处理：服务端读回的运行状态里有等你点「允许」的调用（awaiting_approval，只有侧栏以为在跑的对话才会去读），
 *    或有你还没看的 CC 提醒（列表的 unread、实时推来的提醒圆点）；
 *  - 进行中：实时通道说它在跑（本页打开后看到的），或读回的运行状态是在跑 / 等处理；读回说已结束的不算在跑；
 *  - 其余：已完成。
 *  待处理单据按公司记、不挂在某段对话上，不参与这里的分组（公司页概览与会话栏「待处理」卡显示）。 */
export function chatState(
  activity: Activity | undefined,
  run?: Pick<RunStatus, "status" | "awaiting_approval"> | null,
  unread?: boolean,
): ChatState {
  if (run?.awaiting_approval?.length) return "waiting";
  const ended = run && (run.status === "idle" || run.status === "terminated");
  if (!ended && (activity === "running" || run?.status === "running" || run?.status === "requires_action")) return "running";
  if (activity === "notice" || unread) return "waiting";
  return "done";
}

/** 按 进行中 / 等你处理 / 已完成 分好，各组内保持原顺序（服务端按最后活动倒序）；空组不列。 */
export function groupChats<T extends { id: string }>(rows: readonly T[], stateOf: (row: T) => ChatState): Array<[ChatState, T[]]> {
  const groups = new Map<ChatState, T[]>(CHAT_STATES.map((state) => [state, []]));
  for (const row of rows) groups.get(stateOf(row))!.push(row);
  return CHAT_STATES.map((state) => [state, groups.get(state)!] as [ChatState, T[]]).filter(([, list]) => list.length > 0);
}
