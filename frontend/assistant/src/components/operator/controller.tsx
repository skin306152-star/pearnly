// 「实操」工作区的状态与动作（侧栏、顶栏、主栏、右侧栏共用一份；外壳 assistant.tsx 在实操视图里把它包在侧栏与主区外面）。
//
//   看的是哪个任务：useModePlace.batch（空 = 新建任务，其余是站点的任务号）。任务本身是站点的任务记录（tasks.ts，跨设备）。
//   新建：选了公司（月份默认上个月）就建一个准备中的任务（task_create），会话里随即出资料步骤行与「开始前」框（conversation.tsx）；
//         在输入框里说的话交给这个任务的对话（task-thread.tsx），放的资料一份一张上传卡片（uploads.ts）。
//   准备中：资料（站点分页取）、开始前确认（预检，要人定的一次问完；上次推送结果不明的单标已入账 / 没有入账；预检回包带这次写入的
//         账套）、电脑；公司、月份还能改。「开始」一直能点：还不能开始时点了在它上面说还差什么（startBlocker）。
//   已开始：进度（这台电脑上在跑的来自桥，别处的按站点任务记录里的交单结论；先取一次，之后跟着订阅）；停止 / 开始、跳过 / 重试、
//         整批停下后继续、删除都交给数据源，完了按数据源推来的进度刷新。登录类停下的先授权，授权好了接着继续。
// 出错显示在会话里（同对话的出错框 .cc-alert；shownError：本站拒收照原文，别的只说通用的一句）。

import { type FC, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { op } from "@/i18n";
import { shownError } from "@/lib/frappe";
import { useModePlace } from "@/lib/nav";
import { newTaskIn, OperatorContext, useOperatorOrNull } from "./context";
import { allAnswered, answersFor, batchNeedsHuman, docsToStart, groupOf, questionsOf, unknownDocs, type Question } from "./model";
import { type Draft, EMPTY_PREP, forgetPrep, setPrep, useOperatorStore } from "./store";
import { dropTask, loadTasks, upsertTask, useTasks } from "./tasks";
import type { BatchSnapshot, CreateBatchInput, DocGroup, DocumentOption, OperatorComputer, OperatorSource, PrecheckDoc, TaskRecord, WriteTarget } from "./types";
import { sendUploads } from "./uploads";
import { siteSeconds } from "./live-source";
import { reportCaught, withReport } from "@/lib/caught";

export const PAGE = 50;
/** 整批停在 Express 登录上的原因：先在 CC 授权（「设置」），授权好了接着继续。 */
export const LOGIN_REASONS: ReadonlySet<string> = new Set(["login_failed", "login_missing"]);

/** 开始的条件：有要交的单、确认都答了（结果不明的单都标过了）、电脑在线、Express 登录不是「未设置」（这一页不知道的不拦）。 */
export function canStart(input: { docs: number; questions: readonly Question[]; answers: Record<string, string>; unknown?: number; computer?: OperatorComputer }) {
  return !startBlocker(input);
}

/** 还不能开始时差的那一项（给人看的一句，写在「开始」上面）；能开始回 null。 */
export function startBlocker(input: {
  docs: number;
  questions: readonly Question[];
  answers: Record<string, string>;
  unknown?: number;
  computer?: OperatorComputer;
}): string | null {
  if (input.docs <= 0) return op.noDocs;
  const left = input.questions.filter((q) => !input.answers[q.key]).length + (input.unknown ?? 0);
  if (left > 0) return op.confirmLeft(left);
  const pc = input.computer;
  if (!pc || !pc.online) return [pc?.name || op.computer, op.offline].join(" · ");
  if (pc.login === false) return op.reason.login_missing!;
  return null;
}

/** 默认的执行电脑：本机（在线时），否则第一台在线的。 */
export function initialComputer(computers: readonly OperatorComputer[]): string {
  return (computers.find((pc) => pc.local && pc.online) ?? computers.find((pc) => pc.online))?.id ?? "";
}

/** 「开始」交给数据源的：不入账与跳过的单不交，回答按单据拆开（answersFor）；task = 任务号（批次号就用它），groups 各张的分组。 */
export function draftStartInput(draft: Draft, docs: readonly DocumentOption[], questions: readonly Question[], computer: string): CreateBatchInput {
  const docIds = docsToStart(docs, draft.skipped);
  return {
    company: draft.company,
    company_title: draft.company_title,
    period: draft.period,
    doc_ids: docIds,
    computer,
    start_at: 0,
    answers: answersFor(questions, draft.answers),
    task: draft.id,
    groups: Object.fromEntries(docs.filter((doc) => docIds.includes(doc.id)).map((doc) => [doc.id, groupOf(doc)])),
  };
}

/** 准备中的任务：站点的任务记录 + 这台设备上的准备。 */
export function draftOf(task: TaskRecord, prep = EMPTY_PREP): Draft {
  return { ...prep, id: task.name, company: task.company, company_title: task.company_title, period: task.period, created: siteSeconds(task.creation) };
}

export type DraftData = {
  docs: DocumentOption[] | null;
  next: number | null;
  prechecks: PrecheckDoc[] | null;
  computers: OperatorComputer[] | null;
  /** 这次写进哪份账套（站点预检回包；不知道为 null）。 */
  target: WriteTarget | null;
};

const EMPTY: DraftData = { docs: null, next: null, prechecks: null, computers: null, target: null };

export type Operator = {
  source: OperatorSource;
  id: string;
  /** 站点的任务记录（还没取到为 null）。 */
  task: TaskRecord | null;
  draft: Draft | null;
  data: DraftData;
  questions: Question[];
  ready: boolean;
  snapshot: BatchSnapshot | null;
  busy: boolean;
  error: string;
  /** 点了「开始」还不能开始：差的那一项（能开始了就空）。 */
  notice: string;
  computer: OperatorComputer | undefined;
  /** 这次写进哪份账套（准备中按预检；开始以后看 snapshot.account_set）。 */
  target: WriteTarget | null;
  select(id: string): void;
  /** 新建一个准备中的任务（选了公司就建）；prompt：输入框里说的那句话（可空）。 */
  create(input: { company: string; company_title: string; period: string; prompt?: string; computer?: string }): void;
  /** 准备中改公司、月份。 */
  retarget(patch: { company?: string; company_title?: string; period?: string }): void;
  answer(key: string, value: string): void;
  chooseComputer(id: string): void;
  regroup(docId: string, group: DocGroup): void;
  /** 放资料进来：一份一张上传卡片（uploads.ts），准备中、开始以后都进这家这个月。 */
  intake(files: readonly File[]): void;
  /** 有一份刚识别完：准备中重取资料与预检。 */
  refresh(): void;
  loadMore(): void;
  /** 结果不明的单：标已入账 / 没有入账，再预检一次。 */
  resolve(docId: string, entered: boolean): void;
  start(): void;
  stop(): void;
  skip(docId: string): void;
  retry(docId: string): void;
  /** 整批停下以后继续（因整批停没录成的单交回去重录）。 */
  resume(): void;
  authorize(): void;
  remove(id: string): Promise<void>;
};

/** 直接给一份状态（预览与测试用）。 */
export const OperatorValue = OperatorContext.Provider;
export { newTaskIn, useOperatorOrNull };

export function useOperator(): Operator {
  const found = useContext(OperatorContext);
  if (!found) throw new Error("useOperator outside OperatorProvider");
  return found;
}

/** 一个任务的进度：先取一次，之后跟着订阅（只收这一个）；回到前台再取一次（别处在跑的按站点的交单结论，没有推送）。 */
export function useBatch(source: OperatorSource, batchId: string) {
  const [snapshot, setSnapshot] = useState<BatchSnapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setSnapshot(null);
    setError("");
    if (!batchId) return;
    let alive = true;
    const read = () =>
      source.snapshot(batchId).then(
        (s) => alive && setSnapshot(s),
        (e) => alive && setError(shownError(e)),
      );
    void read();
    const off = source.subscribe((s) => {
      if (alive && s.id === batchId) setSnapshot(s);
    });
    const again = () => {
      if (document.visibilityState !== "hidden") void read();
    };
    document.addEventListener("visibilitychange", again);
    return () => {
      alive = false;
      off();
      document.removeEventListener("visibilitychange", again);
    };
  }, [source, batchId]);
  return { snapshot, error };
}

/** active：外壳在实操视图里（不在时不取、不订阅，切回对话不在后台读桥）。 */
export const OperatorProvider: FC<{ source: OperatorSource; active?: boolean; children: ReactNode }> = ({
  source,
  active = true,
  children,
}) => {
  const place = useModePlace((s) => s.batch);
  const id = active ? place : "";
  const listed = useTasks((s) => (id ? (s.rows.find((row) => row.name === id) ?? null) : null));
  const [fetched, setFetched] = useState<TaskRecord | null>(null);
  const task = fetched && fetched.name === id && (!listed || fetched.modified >= listed.modified) ? fetched : listed;
  const prep = useOperatorStore((s) => (id ? s.preps[id] : undefined));
  const preparing = task?.status === "preparing";
  const draft = useMemo(() => (task && preparing ? draftOf(task, prep) : null), [task, preparing, prep]);
  const { snapshot, error: loadError } = useBatch(source, task && !preparing ? id : "");
  const [data, setData] = useState<DraftData>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [asked, setAsked] = useState(false);
  const company = draft?.company ?? "";
  const period = draft?.period ?? "";
  const skipped = draft?.skipped;

  // 任务列表：实操开着时取第一页；站点推来的记录（进行中、收尾）就地换上
  useEffect(() => {
    if (!active || useTasks.getState().loaded) return;
    void loadTasks(source).catch(withReport(undefined, "operator-tasks"));
  }, [source, active]);
  useEffect(() => (active ? source.subscribeTasks(upsertTask) : undefined), [source, active]);
  // 打开的任务：取一次带单据的记录（列表里没有的——别的设备新建的、深链接——也取得到）
  useEffect(() => {
    setFetched(null);
    if (!id) return;
    let alive = true;
    void source.getTask(id).then(
      (found) => {
        if (!alive) return;
        setFetched(found);
        upsertTask(found);
      },
      (e) => alive && setError(shownError(e)),
    );
    return () => {
      alive = false;
    };
  }, [source, id]);

  const act = useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) { reportCaught(e, "operator-action");
      setError(shownError(e));
    } finally {
      setBusy(false);
    }
  }, []);

  // 准备中的任务：资料、开始前确认、电脑（换了任务、公司或月份重取）
  const loading = useRef(0);
  const precheck = useCallback(
    async (docs: readonly DocumentOption[], merge = false) => {
      const ids = docs.filter((doc) => groupOf(doc) !== "skip").map((doc) => doc.id);
      const reply = ids.length ? await source.precheck(company, ids) : { documents: [], target: null };
      const found = reply.documents;
      setData((before) => {
        const target = reply.target ?? before.target;
        if (!merge) return { ...before, prechecks: found, target };
        const fresh = new Set(found.map((row) => row.doc_id));
        return { ...before, prechecks: [...(before.prechecks ?? []).filter((row) => !fresh.has(row.doc_id)), ...found], target };
      });
    },
    [source, company],
  );
  // clear：换了任务、公司或月份时先清空；有一份刚识别完时就地换上（框不闪）
  const reload = useCallback(async (clear = true) => {
    const ticket = ++loading.current;
    if (clear) setData(EMPTY);
    const [page, computers] = await Promise.all([source.documents(company, period, 0, PAGE), source.computers(company)]);
    if (ticket !== loading.current) return;
    setData((before) => ({ docs: page.documents, next: page.next, prechecks: clear ? null : before.prechecks, computers, target: clear ? null : before.target }));
    await precheck(page.documents);
  }, [source, company, period, precheck]);
  useEffect(() => {
    if (!company) {
      loading.current++;
      setData(EMPTY);
      return;
    }
    void act(() => reload());
  }, [company, reload, act]);
  useEffect(() => {
    setError("");
    setAsked(false);
  }, [id]);

  const questions = useMemo(() => questionsOf(data.prechecks ?? [], skipped), [data.prechecks, skipped]);
  const unknown = unknownDocs(data.prechecks ?? [], skipped).length;
  const computer = data.computers?.find((pc) => pc.id === (draft?.computer || initialComputer(data.computers ?? [])));
  const toStart = docsToStart(data.docs ?? [], skipped);
  const loaded = !!draft && !!data.docs && !!data.prechecks;
  const blocker = loaded ? startBlocker({ docs: toStart.length, questions, answers: draft!.answers, unknown, computer }) : null;
  const ready = loaded && !blocker;

  const select = useCallback((next: string) => useModePlace.setState({ batch: next }), []);

  const value: Operator = {
    source,
    id,
    task,
    draft,
    data,
    questions,
    ready,
    snapshot,
    busy,
    error: error || loadError,
    notice: asked && blocker ? blocker : "",
    computer,
    target: data.target,
    select,
    create(input) {
      void act(async () => {
        const made = await source.createTask({ company: input.company, company_title: input.company_title, period: input.period });
        upsertTask(made);
        setPrep(made.name, { prompt: input.prompt ?? "", computer: input.computer ?? "" });
        select(made.name);
      });
    },
    retarget(patch) {
      if (!draft) return;
      void act(async () => upsertTask(await source.updateTask(draft.id, patch)));
    },
    answer(key, choice) {
      if (draft) setPrep(draft.id, { answers: { ...draft.answers, [key]: choice } });
    },
    chooseComputer(pc) {
      if (draft) setPrep(draft.id, { computer: pc });
    },
    regroup(docId, group) {
      if (!draft) return;
      const before = data.docs?.find((doc) => doc.id === docId);
      if (!before || before.group === group) return;
      void act(async () => {
        await source.regroup(company, docId, group);
        const docs = (data.docs ?? []).map((doc) => (doc.id === docId ? { ...doc, group } : doc));
        setData((d) => ({ ...d, docs }));
        // 换了组，方向与入账类型跟着变，要确认的项也会变：重新预检
        await precheck(docs);
      });
    },
    intake(files) {
      const at = draft ?? task;
      if (!at || !files.length) return;
      void sendUploads(source, { task: id, company: at.company, period: at.period }, files);
    },
    refresh() {
      if (draft && company) void act(() => reload(false));
    },
    loadMore() {
      if (!draft || data.next === null) return;
      const at = data.next;
      void act(async () => {
        const page = await source.documents(company, period, at, PAGE);
        setData((d) => ({ ...d, docs: [...(d.docs ?? []), ...page.documents], next: page.next }));
        await precheck(page.documents, true);
      });
    },
    resolve(docId, entered) {
      if (busy) return;
      if (draft) {
        void act(async () => {
          await source.resolve(company, docId, entered);
          await precheck(data.docs ?? []);
        });
        return;
      }
      if (snapshot?.site) void act(() => source.resolve(snapshot.site!, docId, entered, snapshot.id));
    },
    start() {
      if (busy) return;
      if (draft) {
        if (!loaded) return;
        if (blocker) {
          // 一直能点：还差什么就在「开始」上面说
          setAsked(true);
          return;
        }
        void act(async () => {
          await source.createBatch(draftStartInput(draft, data.docs ?? [], questions, computer?.id ?? ""));
          const started = await source.getTask(draft.id);
          setFetched(started);
          upsertTask(started);
          setPrep(draft.id, { answers: {}, skipped: [] });
        });
        return;
      }
      if (snapshot) void act(() => source.start(snapshot.id));
    },
    stop() {
      if (snapshot) void act(() => source.stop(snapshot.id));
    },
    skip(docId) {
      if (draft) return setPrep(draft.id, { skipped: [...draft.skipped, docId] });
      if (snapshot) void act(() => source.skip(snapshot.id, docId));
    },
    retry(docId) {
      if (draft) {
        const doc = data.docs?.find((row) => row.id === docId);
        if (doc) void act(() => precheck([doc], true));
        return;
      }
      if (snapshot) void act(() => source.retry(snapshot.id, docId));
    },
    resume() {
      if (snapshot) void act(() => source.resume(snapshot.id));
    },
    authorize() {
      void act(async () => {
        const target = snapshot ? { batch: snapshot.id } : { computer: computer?.id ?? "" };
        const { ok } = await source.authorizeLogin(target);
        if (ok && !snapshot) setData((d) => ({ ...d, computers: (d.computers ?? []).map((pc) => (pc.id === computer?.id ? { ...pc, login: true } : pc)) }));
        // 整批因登录停下：授权好了接着继续
        if (ok && snapshot && batchNeedsHuman(snapshot) && LOGIN_REASONS.has(snapshot.reason)) await source.resume(snapshot.id);
      });
    },
    async remove(taskId) {
      await source.remove(taskId);
      dropTask(taskId);
      forgetPrep(taskId);
      if (taskId === id) select("");
    },
  };
  return <OperatorContext.Provider value={value}>{children}</OperatorContext.Provider>;
};
