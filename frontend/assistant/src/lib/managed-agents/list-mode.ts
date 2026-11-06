// 侧栏列的是哪种会话（方案 cc-unified-plan 14.8，审定 U-Q5 甲：同一个列表、同一个接口，按模式筛）：直写模式只列直写会话，代录模式只列代录
// 会话（服务端 sessions.listing 的 mode：chat = 没挂代录任务、operator = 挂了）。新建时也带它（sessions.create 的 mode=operator 在同一事务里
// 建代录任务）。模式本身由外壳的左上角切换定（lib/nav.ts 的 useModePlace 跟着改这里）；这里单放，免得接口层反过来引外壳。
// 每段代录会话挂的任务号（listing / get / create 每行的 task，方案 14.10）也记在这里：输入框放的资料交给这个任务（intake），不进对话附件。
// 每段会话是哪种（同一行的 mode）也记下：切模式时正在看的对话不属于新模式就离开它（lib/nav.ts 的 leaveForeignThread，用户 11-06）。

import { create } from "zustand";

export type ListMode = "chat" | "operator";

export const useListMode = create<{ mode: ListMode; tasks: Readonly<Record<string, string>>; modes: Readonly<Record<string, ListMode>> }>(
  () => ({ mode: "chat", tasks: {}, modes: {} }),
);

/** 此刻侧栏按哪种模式取。 */
export const listMode = (): ListMode => useListMode.getState().mode;

/** 记下几行会话是哪种、挂的代录任务（没挂的不记任务）。 */
export function noteSessionTasks(rows: ReadonlyArray<{ id: string; mode?: string; task?: string }>) {
  const now = useListMode.getState();
  const fresh = rows.filter((row) => row.mode === "operator" && row.task && now.tasks[row.id] !== row.task);
  const kinds = rows.filter((row) => (row.mode === "chat" || row.mode === "operator") && now.modes[row.id] !== row.mode);
  if (!fresh.length && !kinds.length) return;
  useListMode.setState((s) => ({
    tasks: fresh.length ? { ...s.tasks, ...Object.fromEntries(fresh.map((row) => [row.id, row.task!])) } : s.tasks,
    modes: kinds.length ? { ...s.modes, ...Object.fromEntries(kinds.map((row) => [row.id, row.mode as ListMode])) } : s.modes,
  }));
}

/** 这段会话是哪种（还没从服务端见过的回 ""）。 */
export const sessionMode = (sessionId: string): ListMode | "" => useListMode.getState().modes[sessionId] ?? "";

/** 这段会话挂的代录任务号（直写会话、还不知道的回 ""）。 */
export const useSessionTask = (sessionId: string | null | undefined): string =>
  useListMode((s) => (sessionId ? (s.tasks[sessionId] ?? "") : ""));
