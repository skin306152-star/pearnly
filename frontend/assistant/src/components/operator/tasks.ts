// 「实操」的任务列表（站点的任务记录，跨设备；侧栏、顶栏、主栏共用一份）：
//   - 列表按页取（task_list，改动时间新的在前），滚到底再取下一页；新建、改名、置顶、收尾回来的记录就地换上；
//   - 这台电脑上在跑的任务另有桥上的实时进度（live：任务号 → 进度状态），只用来在侧栏行尾标「在跑」；
//   - 置顶 / 取消置顶请求还没回时按点的样子摆（stars），回来换成站点的；
//   - 看过没有（seen，本机按人记）：任务结束（已完成、需要你处理、已停止）以后这台设备还没打开看过，行尾标未读蓝点（同对话）；
//     第一次在这台设备上用时之前结束的不算（base）。
//   - 别的设备上的变化：站点事件 pearnly_operator_task（共用层 express_operator/tasks.py 提交后推给本人）就地换上或拿掉（watchTasks）。
// 行尾只有对话行那两种标记（在跑脉冲点、未读蓝点；有资料在传也算在跑）；进度与要你处理的都在任务会话里。

import { create } from "zustand";
import { shownError } from "@/lib/frappe";
import type { Activity } from "@/lib/managed-agents/activity";
import { onSiteEvent } from "@/lib/managed-agents/realtime";
import { clock } from "@/lib/prefs-sync";
import { siteSeconds, taskOf } from "./live-source";
import type { BatchSnapshot, BatchStatus, OperatorSource, TaskRecord, TaskStatus } from "./types";
import { reportCaught } from "@/lib/caught";

export const TASK_PAGE = 50;

type TasksState = {
  rows: TaskRecord[];
  next: number | null;
  loaded: boolean;
  loading: boolean;
  error: string;
  /** 最近读完的一次列表的请求发出的时刻（prefs-sync 的 clock；侧栏按「没有这家公司了」收拾手排顺序时看它新不新）。 */
  seenAt: number;
  stars: Record<string, boolean>;
  live: Record<string, BatchStatus>;
};

export const useTasks = create<TasksState>(() => ({
  rows: [],
  next: null,
  loaded: false,
  loading: false,
  error: "",
  seenAt: -Infinity,
  stars: {},
  live: {},
}));

/** 取一页（start 0 = 从头重取，换掉整份）。出错写进 error（侧栏出错行），已有的留着。 */
export async function loadTasks(source: OperatorSource, start = 0): Promise<void> {
  const at = clock();
  useTasks.setState({ loading: true });
  try {
    const page = await source.listTasks(start, TASK_PAGE);
    useTasks.setState((s) => {
      const rows = start ? [...s.rows, ...page.tasks.filter((row) => !s.rows.some((had) => had.name === row.name))] : page.tasks;
      return { rows, next: page.next, loaded: true, loading: false, error: "", seenAt: start ? s.seenAt : at };
    });
  } catch (error) {
    useTasks.setState({ loaded: true, loading: false, error: shownError(error) });
    throw error;
  }
}

/** 一个任务的记录换上（新的放最前面；收尾、改名、置顶回来的就地换）。 */
export function upsertTask(task: TaskRecord) {
  if (!task.name) return;
  useTasks.setState((s) =>
    s.rows.some((row) => row.name === task.name)
      ? { rows: s.rows.map((row) => (row.name === task.name ? { ...task, documents: task.documents ?? row.documents } : row)) }
      : { rows: [task, ...s.rows] },
  );
}

export function dropTask(name: string) {
  useTasks.setState((s) => ({ rows: s.rows.filter((row) => row.name !== name) }));
}

export const setTasksError = (error: string) => useTasks.setState({ error });

/** 置顶 / 取消置顶请求还没回：先按点的样子摆；undefined = 回来了，按站点的。 */
export function overrideStar(name: string, starred: boolean | undefined) {
  useTasks.setState((s) => {
    const stars = { ...s.stars };
    if (starred === undefined) delete stars[name];
    else stars[name] = starred;
    return { stars };
  });
}

/** 桥上的进度（这台电脑上的；别处的 remote 不算）。 */
export function noteLive(snapshot: Pick<BatchSnapshot, "id" | "status" | "remote">) {
  if (snapshot.remote) return;
  useTasks.setState((s) => (s.live[snapshot.id] === snapshot.status ? s : { live: { ...s.live, [snapshot.id]: snapshot.status } }));
}

/** 任务此刻的状态：这台电脑上在跑的按桥上的进度（在跑、暂停算进行中；跑完了按站点收尾前先算已完成 / 已停止），其余按站点。 */
export function effectiveStatus(task: Pick<TaskRecord, "status">, live?: BatchStatus): TaskStatus {
  if (live === "running" || live === "paused") return "running";
  if ((live === "finished" || live === "stopped") && (task.status === "queued" || task.status === "running")) return live;
  return task.status;
}

const ENDED: ReadonlySet<TaskStatus> = new Set<TaskStatus>(["finished", "attention", "stopped"]);

// ---------------------------------------------------------------- 看过没有（本机按人记）
const SEEN_KEY = (user: string) => `cc.operator.seen:${user}`;
/** 最多记这么多个任务（旧的先丢）。 */
export const SEEN_MAX = 500;
type Seen = { base: number; seen: Record<string, string> };

function readSeen(user: string): Seen | null {
  try {
    const raw = user ? localStorage.getItem(SEEN_KEY(user)) : null;
    const value = raw ? (JSON.parse(raw) as Partial<Seen>) : null;
    if (!value || typeof value.base !== "number") return null;
    const seen = value.seen && typeof value.seen === "object" ? value.seen : {};
    return { base: value.base, seen: Object.fromEntries(Object.entries(seen).filter(([, v]) => typeof v === "string")) };
  } catch (caughtError) { reportCaught(caughtError, "operator-tasks-seen");
    return null;
  }
}

export const useSeen = create<Seen & { user: string }>(() => ({ base: Math.floor(Date.now() / 1000), seen: {}, user: "" }));

function keepSeen() {
  const { user, base, seen } = useSeen.getState();
  if (!user) return;
  try {
    localStorage.setItem(SEEN_KEY(user), JSON.stringify({ base, seen }));
  } catch (caughtError) { reportCaught(caughtError, "operator-tasks-seen");
    // 存不了：本次照样标
  }
}

/** 知道是谁了：换成这个人记下的；这台设备上第一次用，从现在算起（之前结束的不标未读）。 */
export function bindSeenUser(user: string) {
  if (!user || useSeen.getState().user === user) return;
  const saved = readSeen(user);
  useSeen.setState({ user, ...(saved ?? { base: Math.floor(Date.now() / 1000), seen: {} }) });
  if (!saved) keepSeen();
}

/** 正开着这个任务：记下看过时它的状态（状态再变——比如又跑完一次——才又算没看过）。 */
export function markSeen(name: string, status: TaskStatus) {
  const { seen } = useSeen.getState();
  if (!name || seen[name] === status) return;
  const next = { ...seen };
  delete next[name];
  next[name] = status;
  useSeen.setState({ seen: Object.fromEntries(Object.entries(next).slice(-SEEN_MAX)) });
  keepSeen();
}

/** 行尾标记（同对话行）：在跑 = 脉冲点；结束了、这台设备还没看过 = 未读蓝点；正开着的不标。 */
export function taskActivity(
  task: Pick<TaskRecord, "name" | "status" | "finished_at" | "modified">,
  live: BatchStatus | undefined,
  seen: Pick<Seen, "base" | "seen">,
  open: boolean,
): Activity | undefined {
  const status = effectiveStatus(task, live);
  if (open) return undefined;
  if (status === "running") return "running";
  if (!ENDED.has(status) || seen.seen[task.name] === status) return undefined;
  const ended = siteSeconds(task.finished_at) || siteSeconds(task.modified);
  return seen.seen[task.name] !== undefined || ended > seen.base ? "unread" : undefined;
}

// ---------------------------------------------------------------- 别的设备上的变化
/** 站点事件（共用层 express_operator/tasks.py 的 EVENT）。 */
export const TASK_EVENT = "pearnly_operator_task";

/** 有代录的产品挂一次（assistant.tsx）：任务记录变了就地换上、删了拿掉（列表还没取过时不管）；返回取消订阅。 */
export function watchTasks(): () => void {
  return onSiteEvent(TASK_EVENT, (data) => {
    const change = (data && typeof data === "object" ? data : {}) as { op?: string; task?: unknown; name?: string };
    if (!useTasks.getState().loaded) return;
    if (change.op === "delete" && change.name) dropTask(String(change.name));
    else if (change.op === "upsert" && change.task) upsertTask(taskOf(change.task));
  });
}
