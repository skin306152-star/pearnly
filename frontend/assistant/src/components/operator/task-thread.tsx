// 实操任务里的对话（对话框甲，主会话 10-29 审定；11-04 右栏删了以后任务的一切都在这一条会话里）：输入框打的字交给 CC（同对话页
// 那一套：会话控制器收发、回放、实时跟；准备中说的也交，评估 Q6），回复与上传卡片按时间排在会话里（workspace.tsx）。
//   - 一个任务一段对话（站点 task_thread：第一次打字时建，不进对话侧栏），智能体用实操工具查进度、交代跳过 / 重做
//     （pearnly_core/express_operator/tools.py；跳过、重做由执行的那台电脑取走照桥上控制做，live-source 的 takeControls）。
//   - 录到中途停下等人选的单（任务里「未完成」、预检判成要选的）：选项卡片出在任务记录里，人点选项、点确定 → 记下并交回这个任务
//     接着录（task_answer），不调模型；其余单照录，这张排到最后（同开始前确认的选法：按类分组、下拉选）。
//   - 实操的状态只在这里说（用户 10-30：「对话框状态必须做到和对话那样」）：对话页同一条工作状态行（thread.tsx 的 RunLine），
//     在录 = 小点 +「计时 · 这张单」，排队 / 暂停后等着自己恢复 = 小点 +「计时 · 等电脑空闲」，停下等人 = 橙色小点 +
//     「需要您的输入」（等选项卡片时在卡片下面，同对话等「允许」），整个任务因必须人工的原因停了 = 错误框，完成 = 不显示；
//     对话这一轮在跑时让给对话自己的状态行（回复下面那一条），一次只有一行。

import { type FC, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { getSessionController, type ControllerSnapshot } from "@/lib/managed-agents/session-controller";
import { initialProjection, isBusy } from "@/lib/managed-agents/reducer";
import { RunLine, useClock } from "@/components/assistant-ui/thread";
import { Marquee } from "@/components/ui/marquee";
import { co } from "@/i18n";
import { shownError } from "@/lib/frappe";
import { useOperator } from "./controller";
import {
  answersFor,
  allAnswered,
  batchActivity,
  batchStatusText,
  docTitle,
  isFinal,
  kindTitle,
  optionLabel,
  questionGroups,
  questionsOf,
  type Question,
} from "./model";
import { upsertTask } from "./tasks";
import type { OperatorSource, PrecheckDoc, TaskRecord } from "./types";
import { reportCaught } from "@/lib/caught";

const EMPTY: ControllerSnapshot = { ...initialProjection(), isLoaded: true, isTailing: false, transportError: null, stalled: false, isSynced: true };
const subscribeNothing = () => () => {};
const getEmpty = () => EMPTY;

export type TaskThread = { sessionId: string; snapshot: ControllerSnapshot; running: boolean; send: (text: string) => Promise<void> };

/** 任务的对话：有编号就接上（看得见时回放 + 实时跟），没有就等第一次发话时建。 */
export function useTaskThread(source: OperatorSource, task: Pick<TaskRecord, "name" | "session_id"> | null): TaskThread {
  const [made, setMade] = useState<Record<string, string>>({});
  const sessionId = task ? task.session_id || made[task.name] || "" : "";
  const controller = sessionId ? getSessionController(sessionId) : null;
  const snapshot = useSyncExternalStore(controller ? controller.subscribe : subscribeNothing, controller ? controller.getSnapshot : getEmpty, getEmpty);
  useEffect(() => {
    if (!controller) return;
    controller.attach();
    return () => controller.detach();
  }, [controller]);
  const name = task?.name ?? "";
  const send = useCallback(
    async (text: string) => {
      if (!name || !text.trim()) return;
      let id = sessionId;
      if (!id) {
        id = await source.taskThread(name);
        setMade((m) => ({ ...m, [name]: id }));
      }
      await getSessionController(id).sendMessage(text, []);
    },
    [name, sessionId, source],
  );
  return { sessionId, snapshot, running: isBusy(snapshot), send };
}

/** 录到中途停下等人选的单：这几张在站点上判一次（同开始前确认），要选的按类出下拉，选齐点确定交回任务。 */
export const TaskChoices: FC<{ waiting: readonly string[] }> = ({ waiting }) => {
  const o = useOperator();
  const task = o.task;
  const [prechecks, setPrechecks] = useState<PrecheckDoc[] | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = waiting.join(",");
  useEffect(() => {
    if (!task || !key) return setPrechecks(null);
    let alive = true;
    void o.source.precheck(task.company, key.split(",")).then(
      (reply) => alive && setPrechecks(reply.documents),
      () => alive && setPrechecks([]),
    );
    return () => {
      alive = false;
    };
  }, [o.source, task?.company, key]); // eslint-disable-line react-hooks/exhaustive-deps
  const questions = useMemo(() => questionsOf(prechecks ?? []), [prechecks]);
  if (!task || !questions.length) return null;
  const docs = new Map((task.documents ?? []).map((d) => [d.doc_id, d]));
  const ready = allAnswered(questions, answers);
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const byDoc = answersFor(questions, answers);
      for (const [docId, list] of Object.entries(byDoc)) upsertTask(await o.source.answerTask(task.name, docId, list));
      setAnswers({});
      setPrechecks(null);
    } catch (e) { reportCaught(e, "operator-task-answer");
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="cc-op-choices" data-slot="cc-op-choices">
      {questionGroups(questions).map(([kind, list]) => (
        <div key={kind} className="cc-op-ask-group" data-kind={kind} data-slot="cc-op-ask-group">
          <div className="cc-op-ask-kind">{kindTitle(kind)}</div>
          {list.map((q: Question) => (
            <label key={q.key} className="cc-op-ask" data-slot="cc-op-ask">
              <Marquee className="min-w-0 flex-1 truncate">
                {[q.label, ...q.doc_ids.map((id) => docTitle(docs.get(id) ?? {}, ""))].filter(Boolean).join(" · ")}
              </Marquee>
              <select
                className="cc-input cc-op-ask-select"
                aria-label={`${kindTitle(kind)} · ${q.label}`}
                value={answers[q.key] ?? ""}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.key]: e.target.value }))}
              >
                <option value="" disabled>—</option>
                {q.options.map((option) => (
                  <option key={option.code} value={option.code}>
                    {optionLabel(kind, option)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      ))}
      {error && <div role="alert" className="cc-alert">{error}</div>}
      <div className="cc-card-actions">
        <button type="button" className="cc-card-btn" data-slot="cc-op-choices-ok" disabled={!ready || busy} onClick={() => void submit()}>
          {co.confirm}
        </button>
      </div>
      {/* 任务录完了只剩这张等选：卡片下面说「需要您的输入」（同对话等「允许」）；还在录别的单时状态行在最下面说在录哪张 */}
      {(!o.snapshot || isFinal(o.snapshot.status)) && <RunLine kind="input" className={RUN_GAP} />}
    </div>
  );
};

const RUN_GAP = "px-(--cc-space-8) pt-(--cc-run-gap)";

/** 任务的工作状态行（文件头）：chatBusy = 这段对话这一轮在跑（状态由回复下面那一条说）。 */
export const TaskRunIndicator: FC<{ chatBusy: boolean }> = ({ chatBusy }) => {
  const o = useOperator();
  const activity = batchActivity(o.draft ? null : o.snapshot);
  const working = !chatBusy && (activity.kind === "tool" || activity.kind === "queued");
  const now = useClock(working);
  if (chatBusy || activity.kind === "hidden") return null;
  if (activity.kind === "error") {
    return (
      <div role="alert" data-slot="cc-op-run-error" className="cc-alert">
        <p className="line-clamp-(--cc-alert-lines)">{activity.text}</p>
      </div>
    );
  }
  if (activity.kind === "input") return <RunLine kind="input" className={RUN_GAP} />;
  const since = activity.since ? activity.since * 1000 : now;
  const text = batchStatusText(activity, (now - since) / 1000);
  // 读屏只报在做什么，不每秒报一次计时
  return <RunLine kind={activity.kind} text={text} label={activity.step || text} className={RUN_GAP} />;
};
