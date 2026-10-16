// 侧栏按公司分组（ADR-020，同 Claude Code 按项目分组；CC 按公司主档）。公司主档全所共用（Cowork companies 域），
// 侧栏只列「我有对话的公司」（共用层 projects()）；全所公司在公司页（components/companies.tsx）里找。
//
//   projects()   → kind 为空 = 这个产品没有公司分组（ERP、DMS），侧栏与今天一样；period / scope：服务端收不收月份、分店
//   filings      → 对话编号 → {公司, 处理月份, 分店}（公司 "" = 最近）。不放进组件库的 custom：updateCustom 会整份替换
//                  custom，组件库遇到已认得的编号也不更新（重拉列表的那一页另记，见 session-list-adapter 的 absorb）
//   draft        → 待建的新对话挂在哪（组头 ＋、主档页输入框、输入框上方的公司条设好；顶部「新建」、Ctrl+N、托盘、快捷提问清掉）
//   shown / requestedGroup → 每组显示几条；「显示更多」要翻哪一组（复合游标，session-list-adapter 的 list）
//
// 实时：服务端经侧栏同步同一通道推 pearnly.project（realtime.ts）——move（对话换组、换月份）、upsert（公司改名等）、
// delete（删公司）——这里并入；页面打开、重连、回到前台重取一次 projects()（list-sync.ts 的 watchList）。

import { create } from "zustand";
import { get } from "@/lib/frappe";
import { type Filing, moveSession } from "./api";

export type { Filing } from "./api";

/** 侧栏里的一家公司（共用层 projects()：provider 给的名称与副标题，加上我在这家的对话数与最近活动）。 */
export type ProjectRow = { id: string; title: string; subtitle?: string; last_active?: string | null; count?: number };
export type ProjectKind = "company" | null;

/** 服务端推来的公司变化（同侧栏同步的信封，type = pearnly.project）。 */
export type ProjectChange =
  | { type: "pearnly.project"; op: "upsert"; project: { id: string; title: string; subtitle?: string }; part?: string }
  | { type: "pearnly.project"; op: "delete"; id: string }
  | { type: "pearnly.project"; op: "move"; session_id: string; project: string; period?: string; scope?: string };

/** 复合游标：最近一组 + 还有更多的各家公司（"" = 这家从头翻）。 */
export type GroupCursor = { recent: string | null; groups: Record<string, string> };

export const FIRST_SHOWN = 5; // 每组先显示几条
export const SHOW_MORE_STEP = 10; // 「显示更多」每次加几条
export const OPEN_FIRST = 5; // 前几家公司默认展开（其余收起，点开再看）
export const NO_FILING: Filing = { project: "", period: "", scope: "" };

type State = {
  loaded: boolean;
  kind: ProjectKind;
  /** 服务端收月份（provider PERIOD）、收分店（provider SCOPE）。 */
  period: boolean;
  scope: boolean;
  projects: ProjectRow[];
  filings: Record<string, Filing>;
  draft: Filing;
  shown: Record<string, number>;
  requestedGroup: string | null;
  more: GroupCursor;
  /** 侧栏公司下的一行出错提示（服务端给的原文）。 */
  error: string;
};

const EMPTY_CURSOR: GroupCursor = { recent: null, groups: {} };

export const useProjects = create<State>(() => ({
  loaded: false,
  kind: null,
  period: false,
  scope: false,
  projects: [],
  filings: {},
  draft: NO_FILING,
  shown: {},
  requestedGroup: null,
  more: EMPTY_CURSOR,
  error: "",
}));

const store = useProjects;

// ---------------------------------------------------------------- 读
let loading: Promise<ProjectKind> | null = null;

type ProjectsReply = { kind?: string | null; period?: boolean; scope?: boolean; projects?: ProjectRow[] };

/** 重取我有对话的公司（页面打开、重连、回到前台、对话移进还不在侧栏的公司）。读不到（旧服务端没有这个接口、断网）时
 *  保留已有的，第一次就按没有公司分组。 */
export function refreshProjects(): Promise<ProjectKind> {
  const asked = Date.now();
  loading = get<ProjectsReply>("projects")
    .then((out) => {
      const kind: ProjectKind = out?.kind === "company" ? "company" : null;
      const rows = kind ? (out?.projects ?? []).filter((row) => row && typeof row.id === "string") : [];
      // 页面先搭出来的组：服务端那一刻还不可能有的（对话还没收下）留着，其余照这次回复
      const listed = new Set(rows.map((row) => row.id));
      const kept = kind ? store.getState().projects.filter((row) => provisional.has(row.id) && !listed.has(row.id) && heldFor(row.id, asked)) : [];
      for (const id of [...provisional]) {
        if (kept.some((row) => row.id === id)) continue;
        provisional.delete(id);
        holds.delete(id);
      }
      store.setState({
        loaded: true,
        kind,
        period: !!kind && !!out?.period,
        scope: !!kind && !!out?.scope,
        projects: sortRows([...kept, ...rows]),
      });
      return kind;
    })
    .catch(() => {
      if (!store.getState().loaded) store.setState({ loaded: true, kind: null });
      return store.getState().kind;
    });
  return loading;
}

/** 侧栏第一页之前：公司读好了没有（没读过就读一次）。 */
export function projectsReady(): Promise<ProjectKind> {
  if (store.getState().loaded) return Promise.resolve(store.getState().kind);
  return loading ?? refreshProjects();
}

const time = (value: string | undefined | null) => {
  const t = value ? new Date(value).getTime() : Number.NaN;
  return Number.isNaN(t) ? 0 : t;
};

/** 公司的最近活动（我在这家的对话里最晚的一次）。 */
export function rowActivity(row: ProjectRow): number {
  return time(row.last_active);
}

function sortRows(rows: ProjectRow[]): ProjectRow[] {
  return [...rows].sort((a, b) => rowActivity(b) - rowActivity(a) || a.title.localeCompare(b.title));
}

export function projectById(id: string | undefined | null): ProjectRow | undefined {
  return id ? store.getState().projects.find((row) => row.id === id) : undefined;
}

/** 这段对话挂在哪（对话编号或本页新建时的本地编号；侧栏没有这家公司的回到最近，月份分店照记）。 */
export function filingOf(...ids: Array<string | undefined>): Filing {
  const { filings } = store.getState();
  for (const id of ids) {
    if (!id) continue;
    const filing = filings[id];
    if (filing) return filing;
  }
  return NO_FILING;
}

/** 这段对话在侧栏哪一组（侧栏里没有这家公司 = 最近）。 */
export function groupOf(...ids: Array<string | undefined>): string {
  const project = filingOf(...ids).project;
  return project && store.getState().projects.some((row) => row.id === project) ? project : "";
}

// ---------------------------------------------------------------- 列表带回的归属
const moved = new Map<string, number>();

/** 本页在 asked 之后自己移动过（或正在移动）这段对话：那之前读的一页不拿来改它。 */
export function movedSince(sessionId: string, asked: number): boolean {
  return (moved.get(sessionId) ?? -1) >= asked;
}

const same = (a: Filing | undefined, b: Filing) => !!a && a.project === b.project && a.period === b.period && a.scope === b.scope;

/** 一行摘要里的归属（旧服务端没有这几项回 null，不改已记的）。 */
export function filingFrom(row: { project?: unknown; period?: unknown; scope?: unknown }): Filing | null {
  if (typeof row.project !== "string") return null;
  const project = row.project;
  return {
    project,
    period: project && typeof row.period === "string" ? row.period : "",
    scope: project && typeof row.scope === "string" ? row.scope : "",
  };
}

// 页面已知的公司名称（主档页、公司页、选公司的菜单读到的）：在这家开第一段对话时，侧栏还没有这一组，先按这个名称显示，
// 不让新对话先闪到「最近」再跳进公司；projects() 回来后换成服务端那一行。
const titleHints = new Map<string, string>();

export function hintTitle(id: string, title: string) {
  if (id && title) titleHints.set(id, title);
}

// 页面先搭出来、服务端还没给过的公司组（provisional），以及哪些组在等服务端收下本页的对话（holds）：建会话、移动对话的
// 请求还没回时，服务端的 projects() 不可能有这家——那期间问的回复不拿来拆这一组（2026-10-15 生产：建会话前就重取，回复把
// 先搭的组盖掉，新对话在「最近」待了约 2 秒）。open：还没回的请求数；until：最后一个回来的时刻，在那之前问的回复也不作数。
const provisional = new Set<string>();
const holds = new Map<string, { open: number; until: number }>();

function heldFor(id: string, asked: number): boolean {
  const hold = holds.get(id);
  return !!hold && (hold.open > 0 || asked < hold.until);
}

const projectsOf = (entries: Record<string, Filing>) => [...new Set(Object.values(entries).map((f) => f.project).filter(Boolean))];

/** 记下归属；挂进侧栏还没有的公司时按页面已知的名称先搭出这一组。回：还不在侧栏里（连先搭的也没有）的公司。 */
function record(entries: Record<string, Filing>): string[] {
  const { filings, projects, kind } = store.getState();
  if (Object.entries(entries).every(([id, filing]) => same(filings[id], filing))) return [];
  const missing = kind ? projectsOf(entries).filter((id) => !projects.some((row) => row.id === id)) : [];
  const made: ProjectRow[] = missing
    .filter((id) => titleHints.has(id))
    .map((id) => ({ id, title: titleHints.get(id) as string, count: 1, last_active: new Date().toISOString() }));
  for (const row of made) provisional.add(row.id);
  store.setState(made.length ? { filings: { ...filings, ...entries }, projects: [...made, ...projects] }
    : { filings: { ...filings, ...entries } });
  return missing;
}

/** 记下几段对话挂在哪（列表的每一页、推来的事件都经这里；本页新建、移动走 fileLocally）。挂进侧栏还没有的公司时重取一次 projects()。 */
export function noteFilings(entries: Record<string, Filing>) {
  if (record(entries).length) void refreshProjects();
}

/** 本页把对话挂进某家公司、服务端还没收下（新建会话、移动对话）：先按本页记下（侧栏还没有这家就先搭出这一组），收下之前
 *  不重取（读也读不到），别处触发的重取回来也不拆这一组。done：收下了，照服务端回的再记一次，这家是先搭的（或还不在侧栏）
 *  就重取一次换成服务端那一行；undo：没收下，按给的撤回，先搭的组没有别的对话就去掉。 */
export function fileLocally(entries: Record<string, Filing>): {
  done: (entries: Record<string, Filing>) => void;
  undo: (entries: Record<string, Filing>) => void;
} {
  record(entries);
  const held = projectsOf(entries).filter((id) => provisional.has(id));
  for (const id of held) {
    const hold = holds.get(id) ?? { open: 0, until: 0 };
    hold.open++;
    holds.set(id, hold);
  }
  let settled = false;
  const release = () => {
    if (settled) return false;
    settled = true;
    for (const id of held) {
      const hold = holds.get(id);
      if (!hold) continue;
      hold.open = Math.max(0, hold.open - 1);
      hold.until = Date.now();
    }
    return true;
  };
  return {
    done(final) {
      if (!release()) return;
      record(final);
      const { projects } = store.getState();
      if (projectsOf(final).some((id) => provisional.has(id) || !projects.some((row) => row.id === id))) void refreshProjects();
    },
    undo(previous) {
      if (!release()) return;
      record(previous);
      const { filings } = store.getState();
      const empty = held.filter(
        (id) => provisional.has(id) && !holds.get(id)?.open && !Object.values(filings).some((f) => f.project === id),
      );
      if (!empty.length) return;
      for (const id of empty) {
        provisional.delete(id);
        holds.delete(id);
      }
      store.setState((s) => ({ projects: s.projects.filter((row) => !empty.includes(row.id)) }));
    },
  };
}

// ---------------------------------------------------------------- 复合游标
export function encodeCursor(cursor: GroupCursor): string | undefined {
  if (!cursor.recent && !Object.keys(cursor.groups).length) return undefined;
  return JSON.stringify(cursor);
}

export function decodeCursor(after: string | undefined): GroupCursor {
  if (!after) return EMPTY_CURSOR;
  try {
    const value = JSON.parse(after) as Partial<GroupCursor>;
    if (value && typeof value === "object") {
      return { recent: typeof value.recent === "string" && value.recent ? value.recent : null, groups: { ...(value.groups ?? {}) } };
    }
  } catch {
    // 换成公司分组前给的旧游标：就是最近这一组的
  }
  return { recent: after, groups: {} };
}

/** 这一次翻哪一组：「显示更多」要的那组 → 最近 → 活动最近、还有更多的那家公司；都翻完回 undefined。 */
export function pickGroup(cursor: GroupCursor, requested: string | null, order: readonly string[]): string | undefined {
  if (requested !== null && requested !== "" && requested in cursor.groups) return requested;
  if (cursor.recent) return "";
  const pending = Object.keys(cursor.groups);
  if (!pending.length) return undefined;
  return order.find((id) => pending.includes(id)) ?? pending[0];
}

/** 侧栏这一刻的翻页状态（list 每次给组件库新游标时一并记下，界面据此决定「显示更多」和滚到底加载）。 */
export function noteCursor(cursor: GroupCursor) {
  store.setState({ more: cursor });
}

/** 活动最近的公司在前。 */
export function projectOrder(): string[] {
  return sortRows(store.getState().projects).map((row) => row.id);
}

/** 取走「显示更多」要翻的那一组（只用一次）。 */
export function takeRequestedGroup(): string | null {
  const requested = store.getState().requestedGroup;
  if (requested !== null) store.setState({ requestedGroup: null });
  return requested;
}

export function shownOf(id: string): number {
  return store.getState().shown[id] ?? FIRST_SHOWN;
}

/** 「显示更多」：这一组多显示 10 条；已加载的不够、服务端还有时只翻这一组（loadMore：组件库的加载下一页）。 */
export async function showMore(id: string, loaded: number, loadMore: () => Promise<void>) {
  const limit = shownOf(id) + SHOW_MORE_STEP;
  store.setState((s) => ({ shown: { ...s.shown, [id]: limit } }));
  if (loaded >= limit || !(id in store.getState().more.groups)) return;
  store.setState({ requestedGroup: id });
  await loadMore();
  // 正在翻别的组时组件库把这次并进了那一页：那一页翻完还没轮到这组，再要一次
  if (store.getState().requestedGroup === id) await loadMore();
  if (store.getState().requestedGroup === id) store.setState({ requestedGroup: null });
}

// ---------------------------------------------------------------- 写
function upsert(row: { id: string; title: string; subtitle?: string }) {
  // 推来的改名只改侧栏里已有的公司（不在侧栏 = 我在这家没有对话，忽略）
  store.setState((s) =>
    s.projects.some((p) => p.id === row.id)
      ? { projects: s.projects.map((p) => (p.id === row.id ? { ...p, title: row.title || p.title, subtitle: row.subtitle ?? p.subtitle } : p)) }
      : {},
  );
}

/** 公司删了（或本页删的）：这一组的对话并入最近、待建的新对话不再挂它。 */
export function forgetProject(id: string) {
  provisional.delete(id);
  holds.delete(id);
  store.setState((s) => ({
    projects: s.projects.filter((p) => p.id !== id),
    filings: Object.fromEntries(Object.entries(s.filings).map(([sid, f]) => [sid, f.project === id ? NO_FILING : f])),
    draft: s.draft.project === id ? NO_FILING : s.draft,
  }));
}

/** 本页改了公司名：侧栏里有就跟着改。 */
export function renameProjectLocally(id: string, title: string) {
  upsert({ id, title });
}

export function setError(text: string) {
  store.setState({ error: text });
}

/** 整批移到某家公司（project "" = 最近）时真要动的：已经在这家的不动（不发请求、月份分店不清空；同「拖回原组不动」），
 *  拖动整批与行菜单「将 N 项移到…」共用。 */
export function notIn(sessions: readonly string[], project: string): string[] {
  return sessions.filter((id) => filingOf(id).project !== project);
}

/** 把对话移到某家公司（project "" = 最近），或改它的处理月份、分店。不改置顶。 */
export async function fileSession(sessionId: string, target: Filing): Promise<void> {
  const before = filingOf(sessionId);
  const next = target.project ? target : NO_FILING;
  if (same(before, next)) return;
  // 移动请求还没回：这期间读的列表（服务端可能还没改）不拿来改它；回来后，那之前问的也不算
  moved.set(sessionId, Number.POSITIVE_INFINITY);
  const local = fileLocally({ [sessionId]: next });
  try {
    const out = await moveSession(sessionId, next);
    moved.set(sessionId, Date.now());
    local.done({ [sessionId]: filingFrom(out ?? {}) ?? next });
  } catch (error) {
    moved.set(sessionId, Date.now());
    local.undo({ [sessionId]: before });
    throw error;
  }
}

/** 新对话开在哪（NO_FILING = 最近）：调用方随后切到新对话。 */
export function setDraft(filing: Partial<Filing>) {
  const next = filing.project ? { ...NO_FILING, ...filing } : NO_FILING;
  if (!same(store.getState().draft, next)) store.setState({ draft: next });
}

// ---------------------------------------------------------------- 实时
export function isProjectChange(event: unknown): event is ProjectChange {
  const change = event as ProjectChange;
  return change?.type === "pearnly.project" && ["upsert", "delete", "move"].includes(change.op);
}

/** 一条推来的公司变化并进本页；move 回对话编号（侧栏没有这一行时由调用方重拉列表）。 */
export function applyProjectChange(change: ProjectChange): string | undefined {
  if (change.op === "upsert" && change.project?.id) upsert(change.project);
  else if (change.op === "delete" && change.id) forgetProject(change.id);
  else if (change.op === "move" && change.session_id) {
    noteFilings({ [change.session_id]: filingFrom(change) ?? NO_FILING });
    return change.session_id;
  }
  return undefined;
}

/** 测试用：回到页面刚打开的状态。 */
export function resetProjects() {
  loading = null;
  moved.clear();
  provisional.clear();
  holds.clear();
  store.setState({
    loaded: false, kind: null, period: false, scope: false, projects: [], filings: {}, draft: NO_FILING, shown: {},
    requestedGroup: null, more: EMPTY_CURSOR, error: "",
  });
}

