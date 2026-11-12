// 本对话账套（A 线 11-12，服务端 pearnly_core.assistant.ledger）：对话的公司 + 月份 + 分店定出的写入目标，存在会话上，
// CC 的识别、推送、代录都只写它。右栏公司卡「ชุดบัญชี」一行显示它、点开改（components/session-panel.tsx 的 LedgerRow）。
// 页面不按日期现算（R2-5：右栏按日期现算的那本与服务端写入的不是同一本），当前值与可选项都读服务端。
//
//   读   GET ledgers(session_id) → {current, options}，按「公司|月份|分店」记；月份、分店改了（本页改的或推来的 move）重取
//        ——服务端按新月份保留选过的那本或重算
//   改   move(..., ledger)：先按本页显示，没收下就撤回
//   推   服务端设了账套（右栏、CC 工具 pearnly_conversation_ledger）推 pearnly.project move 带 ledger：认得的就地换，
//        认不出（新月份的可选项还没读）重取

import { useEffect } from "react";
import { create } from "zustand";
import { type Filing, getLedgers, type LedgerOption, type LedgerView, moveSession } from "./managed-agents/api";
import type { ProjectChange } from "./managed-agents/projects";
import { onProjectChange } from "./managed-agents/realtime";
import { withReport } from "./caught";

export type { LedgerOption, LedgerView } from "./managed-agents/api";

/** 一段对话读到的账套：key 是读的时候的「公司|月份|分店」；stale：推来的账套不在这份可选项里，要重取。 */
type Entry = LedgerView & { key: string; stale?: boolean };
type State = { views: Record<string, Entry> };

export const useLedgers = create<State>(() => ({ views: {} }));

const keyOf = (filing: Filing) => [filing.project, filing.period, filing.scope].join("|");
const inflight = new Map<string, Promise<void>>();
// 每段对话改过几次（本页选、推来的）：那之前发出的读，回来不盖掉新的
const turns = new Map<string, number>();
const turnOf = (sessionId: string) => turns.get(sessionId) ?? 0;
const bump = (sessionId: string) => turns.set(sessionId, turnOf(sessionId) + 1);

function put(sessionId: string, entry: Entry) {
  useLedgers.setState((s) => ({ views: { ...s.views, [sessionId]: entry } }));
}

/** 读一次这段对话的账套（同一段对话同一个「公司|月份|分店」正在读就等那一次）。 */
export function loadLedgers(sessionId: string, filing: Filing): Promise<void> {
  const key = keyOf(filing);
  const turn = turnOf(sessionId);
  const id = `${sessionId}|${key}|${turn}`;
  const running = inflight.get(id);
  if (running) return running;
  const run = getLedgers(sessionId)
    .then((out) => {
      if (turnOf(sessionId) !== turn) return;
      put(sessionId, { current: out?.current ?? "", options: out?.options ?? [], key });
    })
    .finally(() => inflight.delete(id));
  inflight.set(id, run);
  return run;
}

/** 这段对话的账套换成 ledger（推来的 move、move 的回包）：认得就地换，认不出标记重取。 */
export function noteLedger(sessionId: string, ledger: string) {
  const entry = useLedgers.getState().views[sessionId];
  if (!entry || entry.current === ledger) return;
  bump(sessionId);
  const known = !ledger || entry.options.some((item) => item.id === ledger);
  put(sessionId, known ? { ...entry, current: ledger } : { ...entry, stale: true });
}

/** 推来的公司变化：move 带 ledger 的并进来（服务端只在产品有本对话账套时带这个键）；这段对话的公司改了（改名、刷新账套，
 *  part 为空）可选的账套可能变了，标记重取。 */
export function applyLedgerChange(change: ProjectChange, sessionId = "", project = "") {
  if (change.op === "move" && change.session_id && typeof change.ledger === "string") noteLedger(change.session_id, change.ledger);
  else if (change.op === "upsert" && !change.part && sessionId && project && change.project?.id === project) {
    const entry = useLedgers.getState().views[sessionId];
    if (entry && !entry.stale) {
      bump(sessionId);
      put(sessionId, { ...entry, stale: true });
    }
  }
}

/** 右栏选了一本：先按本页显示，move 带 ledger；服务端没收下就撤回并抛出（调用方报错）。 */
export async function chooseLedger(sessionId: string, filing: Filing, ledger: string): Promise<void> {
  const before = useLedgers.getState().views[sessionId];
  if (!before || before.current === ledger) return;
  bump(sessionId);
  put(sessionId, { ...before, current: ledger });
  try {
    const out = await moveSession(sessionId, filing, ledger);
    if (typeof out?.ledger === "string") noteLedger(sessionId, out.ledger);
  } catch (error) {
    bump(sessionId);
    put(sessionId, before);
    throw error;
  }
}

/** 这段对话按这个「公司|月份|分店」读到的账套；还没读到、或读的是别的月份 / 分店时回 null（期间不显示旧的那本）。 */
export function ledgerOf(views: Readonly<Record<string, Entry>>, sessionId: string, filing: Filing): Entry | null {
  const entry = sessionId ? views[sessionId] : undefined;
  return entry && entry.key === keyOf(filing) ? entry : null;
}

/** 右栏要的：这段对话此刻的账套（:func:`ledgerOf`）；没读过、过期了就读。 */
export function useConversationLedger(sessionId: string, filing: Filing): Entry | null {
  const key = keyOf(filing);
  const entry = useLedgers((s) => ledgerOf(s.views, sessionId, filing));
  const fresh = !!entry && !entry.stale;
  useEffect(() => {
    if (!sessionId || !filing.project || fresh) return;
    void loadLedgers(sessionId, filing).catch(withReport(undefined, "ledger"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, key, fresh]);
  useEffect(
    () => (sessionId ? onProjectChange((change) => applyLedgerChange(change, sessionId, filing.project)) : undefined),
    [sessionId, filing.project],
  );
  return entry;
}

/** 当前账套的名称（未定、或不在可选项里时空）。 */
export function ledgerLabel(entry: Pick<LedgerView, "current" | "options"> | null): string {
  return entry?.options.find((item: LedgerOption) => item.id === entry.current)?.label ?? "";
}

/** 测试用：回到页面刚打开的状态。 */
export function resetLedgers() {
  inflight.clear();
  turns.clear();
  useLedgers.setState({ views: {} });
}
