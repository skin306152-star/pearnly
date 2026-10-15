// 右侧会话栏（设计文档 3.13 / 4.4，只在 Cowork 挂了公司的对话与公司页里）的状态与数据：
//
//   卡片     默认顺序 公司 / 本月（第 1 期月结台账）/ 待处理 / 知识库 / 单据文件夹 / 定时任务 / 记住的选择 / 申报草稿 / 输出文件；
//            顺序、折叠、开关记在这台设备（键带用户，跨设备不同步），对话与公司页共用同一份。拖动（dnd-kit）或 Alt+↑/↓ 调顺序时
//            只在看得见的卡片之间挪（空卡不显示，也不占位）。
//   开关     宽屏停靠时按记下的「开着」显示（公司页同样，默认开着）；窄屏浮在页面上、手机盖满屏时只在点了之后显示
//            （overlay，换对话、换页就收起），不因为记着「开着」一打开就把页面盖住。
//   公司页   自己的月份（usePagePeriod）：「在这家新建对话」输入框的月份胶囊与会话栏「本月」卡的月份胶囊是同一个。
//   公司详情 按「公司|月份」缓存：先用缓存显示、后台再取；只在会话栏看得见时（组件挂着）跟着站点事件、回到前台重取
//            （lib/live.ts）；别处改了这家重取，删了清掉。
//   对话行   公司页的对话列表：行尾装饰只用现有的真实信号（见 chatState），时间写相对时间（relativeWhen）。
//
// 本文件不画界面；样式在 globals.css。

import { useCallback, useEffect, useRef, useState } from "react";
import { create } from "zustand";
import { lang } from "@/i18n";
import { type Detail, lastMonth, loadDetail, TASK_EVENTS } from "@/lib/companies";
import { shownError } from "@/lib/frappe";
import { useLiveRefresh } from "@/lib/live";
import type { Activity } from "@/lib/managed-agents/activity";
import type { RunStatus } from "@/lib/managed-agents/events";
import { hintTitle, useProjects } from "@/lib/managed-agents/projects";
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
 *  shown：此刻看得见的卡片（空卡不显示，不算位置）。 */
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
  /** 此刻会话栏是不是宽屏并排停靠（assistant.tsx 按停靠位宽度设；不记）：只有停靠时点公司名才改记下的「开着」。 */
  docked: boolean;
  /** 要展开并滚到的那张卡（点了标题栏「公司名 ›」等）；at 让同一张卡再点一次也生效。 */
  focus: { card: PanelCard; at: number } | null;
};

export const usePanel = create<PanelState>(() => ({ ...DEFAULT_PREFS, user: "", overlay: false, docked: true, focus: null }));

function save() {
  const { user, open, order, collapsed } = usePanel.getState();
  if (!user) return; // 还没读到登录资料：本次照样能用，不写进不带人的公共键
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
    if (user) raw = localStorage.getItem(panelKey(user));
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

/** 打开会话栏并展开、滚到这张卡（标题栏「公司名 ›」、输入框下巴的公司胶囊、手机胶囊菜单里的公司名）。
 *  宽屏停靠时记下「开着」；浮层、手机全屏只开这一次，不改宽屏记下的开关。 */
export function showCard(card: PanelCard = "company") {
  const { collapsed, docked } = usePanel.getState();
  usePanel.setState({
    ...(docked ? { open: true } : {}),
    overlay: true,
    collapsed: collapsed.filter((c) => c !== card),
    focus: { card, at: Date.now() },
  });
  save();
}

/** 停靠位此刻是不是并排（assistant.tsx 每次停靠方式变了就报）。 */
export function setPanelDocked(docked: boolean) {
  if (usePanel.getState().docked !== docked) usePanel.setState({ docked });
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

// ---------------------------------------------------------------- 公司页自己的月份
/** 公司页的月份：null = 还没选过（按设置取上个月，产品不按月份时为空）；换公司不重置（同原来页面里的月份）。 */
export const usePagePeriod = create<{ period: string | null }>(() => ({ period: null }));

export function setPagePeriod(period: string) {
  usePagePeriod.setState({ period });
}

/** 公司页此刻的月份（输入框与会话栏「本月」卡共用）。 */
export function pagePeriod(saved: string | null, withPeriod: boolean, fallback = lastMonth()): string {
  return saved ?? (withPeriod ? fallback : "");
}

export function usePageMonth(): string {
  const saved = usePagePeriod((s) => s.period);
  const withPeriod = useProjects((s) => s.period);
  return pagePeriod(saved, withPeriod);
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

// ---------------------------------------------------------------- 公司页的对话行
export type ChatState = "running" | "waiting" | "done";

/** 一段对话的状态，只用现有的真实信号：
 *  - 进行中：只看实时状态（activity，lib/managed-agents/activity.ts）。取列表时服务端读回的运行状态先交给 noteRun 校正它，
 *    之后跟着推送走；读回的那一刻的快照不再单独算数（页面开着时跑完 / 又开始跑，快照会过期）；
 *  - 等你处理：实时状态在跑、且读回的运行状态里有等你点「允许」的调用（awaiting_approval，只有侧栏以为在跑的对话才会去读），
 *    或有你还没看的 CC 提醒（列表的 unread、实时推来的提醒圆点）；
 *  - 其余：已完成。
 *  待处理单据按公司记、不挂在某段对话上，不算在这里（会话栏「待处理」卡显示）。 */
export function chatState(
  activity: Activity | undefined,
  run?: Pick<RunStatus, "awaiting_approval"> | null,
  unread?: boolean,
): ChatState {
  if (activity === "running") return run?.awaiting_approval?.length ? "waiting" : "running";
  if (activity === "notice" || unread) return "waiting";
  return "done";
}

/** 对话行行尾的装饰（同侧栏行）：在跑 = 脉冲点；等你处理 = 提醒圆点；跑完没看 = 圆点；其余不标。 */
export function chatMark(state: ChatState, activity: Activity | undefined): "running" | "notice" | "unread" | null {
  if (state === "running") return "running";
  if (state === "waiting") return "notice";
  return activity === "unread" ? "unread" : null;
}

/** 在跑的对话里，取列表那一刻还没读过运行状态的（之后才开始跑的）：要向服务端补读的。 */
export function unreadRuns(running: readonly string[], runs: Readonly<Record<string, unknown>>, checked: ReadonlySet<string>): string[] {
  return running.filter((id) => !(id in runs) && !checked.has(id));
}

/** 按最后活动倒序（最新的在前；没有时间的放最后，其余保持原顺序）。 */
export function newestFirst<T extends { updated_at?: string | null }>(rows: readonly T[]): T[] {
  const time = (row: T) => {
    const at = row.updated_at ? new Date(row.updated_at).getTime() : Number.NaN;
    return Number.isNaN(at) ? -Infinity : at;
  };
  return [...rows].sort((a, b) => time(b) - time(a));
}

const RELATIVE_LOCALE: Record<string, string> = { th: "th-TH", zh: "zh-CN", en: "en-US" };
const RELATIVE_STEPS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["second", 60], ["minute", 60], ["hour", 24], ["day", 7], ["week", 4.35], ["month", 12], ["year", Infinity],
];

/** 相对时间（「5 分钟前」「昨天」「3 周前」，同 Claude 项目页的对话列表）；一分钟内写「现在」，读不出的时间回空。 */
export function relativeWhen(value: string | null | undefined, now = Date.now(), locale = RELATIVE_LOCALE[lang] ?? "en-US"): string {
  if (!value) return "";
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return "";
  const format = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  let amount = (at - now) / 1000;
  if (Math.abs(amount) < 60) return format.format(0, "second");
  for (const [unit, size] of RELATIVE_STEPS) {
    if (Math.abs(amount) < size) return format.format(Math.round(amount), unit);
    amount /= size;
  }
  return "";
}
