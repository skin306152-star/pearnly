// 「实操」的真实数据源：Cowork 账号、ERP 账号登录的对话页都用它。
//
//   站点（同源会话，pearnly_core.express_operator.api，两个产品都有）
//     documents(company, period, offset, limit) — 这家这个月的资料（服务端分页）；每张带 group（没有时按 kind 归采购 / 销售）
//     precheck(company, doc_ids) — 开始前确认：每张 ready / choose（needed 一次列全，带可选值，"__new__" = 新建）/ waiting / blocked
//     start(company, doc_ids, computer, start_at, answers) — 建一个任务：任务号由站点发，回每张的交单结果
//     regroup(company, doc_id, group) — 资料拖到别的组（站点按公司记住）
//     intake(company, request_key, file) — 放资料进来（同对话页附件上传那条路：multipart 交给本站）
//     task_list / task_get / task_create / task_update / task_delete — 任务的服务端记录（只给本人看、改；列表、改名、置顶跨设备）
//     task_running(name) — 桥上看到这台电脑开始录了，转「进行中」（每个任务一次）
//     task_settle(name) — 以站点为准收尾：这台电脑上没在跑、还没收尾的任务（换设备看、CC 重启后），以及这台电脑上跑完的
//     resolve(company, doc_id, entered, docnum) — 开始前确认里「结果不明」的单标已入账 / 没有入账（同产品复核页）
//   公司：Cowork 用公司主档的 listCompanies；ERP 用 Frappe 原生 search_link（ERP Company，按页取：多取到 start + limit 再切）
//   桥（CC 桌面版窗口，state().features 含 operator 时）
//     operator_state — 本机最近的任务（CC 重启后也在，带 created）与 Express 登录设置了没有
//     operator_control — stop / resume / skip / retry / delete；operator_authorize — 原生小窗设置 Express 登录
//
// 建任务后先按 start 的回包拼一份「准备中」的进度（没收的单停在需要处理、原因按回包）；桥上有了这个任务就换成桥上的进度，没收的单
// 照旧并在后面。网页、手机（没有桥）、别的电脑上的任务按站点任务记录里的交单结论拼（remote），进度在执行的电脑上看。
// 没有推送信号：只在有人订阅（实操工作区开着）、页面看得见、桥支持实操时每 POLL_MS 读一次桥；页面切到后台就停，回到前台先读一次。

import { op } from "@/i18n";
import { listCompanies } from "@/lib/companies";
import { type Desktop, desktop, OPERATOR } from "@/lib/desktop";
import { getMethod, postMethod, uploadMethod } from "@/lib/frappe";
import { docTitle, groupOf, isFinal } from "./model";
import { TASK_STATES } from "./types";
import type {
  BatchListItem,
  BatchSnapshot,
  CompanyOption,
  CreateBatchInput,
  DocGroup,
  DocSnapshot,
  DocumentOption,
  OperatorComputer,
  OperatorSource,
  PrecheckDoc,
  TaskDocument,
  TaskRecord,
  TaskStatus,
} from "./types";
import { reportCaught, withReport } from "@/lib/caught";

export const API = "pearnly_core.express_operator.api.";
/** 桥上进度多久读一次（毫秒）：只在实操工作区开着、页面看得见时。 */
export const POLL_MS = 3000;
/** 在跑的批次多久取一次对话里交代的跳过 / 重做（秒）。 */
export const CONTROLS_S = 6;
/** 交单结果里算交进去了的状态（其余是没收，停在需要处理）。 */
/** 本机没在跑的任务隔多久再向站点核一次（秒）。 */
export const SETTLE_S = 60;
export const SENT_STATES: ReadonlySet<string> = new Set(["sent", "queued"]);
/** ERP 公司（search_link 的 doctype）。 */
export const ERP_COMPANY = "ERP Company";

type Get = <T>(method: string, args?: Record<string, string | number | undefined>) => Promise<T>;
type Post = <T>(method: string, args?: Record<string, unknown>) => Promise<T>;
type Upload = <T>(method: string, args: Record<string, string>, file: File) => Promise<T>;

export type LiveDeps = {
  /** 当前产品（useProfile().product）：erp 走 search_link，其余走公司主档。 */
  product: string;
  bridge?: () => Promise<Desktop | null>;
  get?: Get;
  post?: Post;
  upload?: Upload;
  now?: () => number;
};

export type StartResult = { doc_id: string; status: string; reason: string };
type DocumentsReply = {
  documents?: Array<{ doc_id: string; kind?: string; group?: string; date?: string; party?: string; ref?: string; total?: string; stopped?: string }>;
  next_offset?: number | null;
  total?: number;
};
type LinkRow = { value: string; label?: string; description?: string };

/** 这一页建的任务：建的时刻、公司显示名、月份与 start 的回包。 */
type Started = {
  id: string;
  company: string;
  company_title: string;
  period: string;
  created: number;
  start_at: number;
  results: StartResult[];
  skipped: Set<string>;
};

/** 站点回的任务（字段缺的补空、状态认不出按准备中）。 */
export function taskOf(raw: unknown): TaskRecord {
  const row = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const text = (key: string) => (row[key] === null || row[key] === undefined ? "" : String(row[key]));
  const status = (TASK_STATES as readonly string[]).includes(text("status")) ? (text("status") as TaskStatus) : "preparing";
  const docs = Array.isArray(row.documents)
    ? (row.documents as Record<string, unknown>[]).map((d): TaskDocument => ({
        doc_id: String(d.doc_id ?? ""),
        doc_group: String(d.doc_group ?? ""),
        state: String(d.state ?? ""),
        docnum: String(d.docnum ?? ""),
        voided_docnum: String(d.voided_docnum ?? ""),
        reason: String(d.reason ?? ""),
        ref: String(d.ref ?? ""),
        party: String(d.party ?? ""),
        blocked_by: String(d.blocked_by ?? ""),
      }))
    : undefined;
  return {
    name: text("name"),
    company: text("company"),
    company_title: text("company_title"),
    period: text("period"),
    title: text("title"),
    starred: row.starred === true || row.starred === 1 || row.starred === "1",
    status,
    computer: text("computer"),
    start_at: text("start_at"),
    started_at: text("started_at"),
    finished_at: text("finished_at"),
    session_id: text("session_id"),
    creation: text("creation"),
    modified: text("modified"),
    ...(docs ? { documents: docs } : {}),
  };
}

/** 站点的日期时间（"2026-10-27 19:35:44.123"）→ Unix 秒；空、认不出为 0。 */
export function siteSeconds(value: string): number {
  if (!value) return 0;
  const ms = Date.parse(value.trim().replace(" ", "T"));
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

/** 交出去还没收尾的任务（站点上等电脑空闲、进行中）。 */
export const OPEN_TASK: ReadonlySet<string> = new Set<TaskStatus>(["queued", "running"]);

const DOC_STATE: Record<string, DocSnapshot["status"]> = { saved: "saved", not_done: "waiting", cancelled: "skipped", skipped: "skipped" };
const BATCH_STATE: Record<TaskStatus, BatchSnapshot["status"]> = {
  preparing: "ready",
  queued: "ready",
  running: "running",
  attention: "finished",
  finished: "finished",
  stopped: "stopped",
};

/** 站点任务记录里的进度（这台电脑上没在跑的：网页、手机、别的电脑）：每张单按交单结论；进度在执行的电脑上（remote）。 */
export function taskSnapshot(task: TaskRecord, listed: ReadonlyMap<string, DocumentOption> = new Map()): BatchSnapshot {
  const docs = (task.documents ?? []).map((d): DocSnapshot => {
    const doc = listed.get(d.doc_id);
    const status = DOC_STATE[d.state] ?? "pending";
    return {
      ...emptyDoc(d.doc_id, doc),
      status,
      docnum: d.docnum,
      voided: d.voided_docnum ? [d.voided_docnum] : [],
      reason: status === "waiting" || status === "skipped" ? d.reason || (d.state === "cancelled" ? "voided" : "") : "",
      ...(status === "pending" && d.blocked_by ? { blocked: d.blocked_by } : {}),
      summary: {
        ...(doc ? { date: doc.date, total: doc.total } : {}),
        ref: d.ref || doc?.ref,
        party: d.party || doc?.party,
        group: groupOf({ group: d.doc_group || doc?.group }),
      },
    };
  });
  const started = siteSeconds(task.started_at);
  const finished = siteSeconds(task.finished_at);
  return {
    id: task.name,
    status: BATCH_STATE[task.status],
    reason: "",
    company: task.company,
    account_set: "",
    total: docs.length,
    counts: countsOf(docs),
    current: "",
    index: 0,
    events: [],
    started,
    finished,
    elapsed: started && finished ? Math.max(0, finished - started) : 0,
    remaining_estimate: 0,
    docs,
    company_title: task.company_title || task.company,
    created: siteSeconds(task.creation),
    start_at: siteSeconds(task.start_at),
    computer: task.computer,
    period: task.period,
    remote: true,
  };
}

export type LiveOperatorSource = OperatorSource & {
  /** 读一次桥、把变了的进度交给订阅者（测试与控制指令之后用；定时读也走这里）。 */
  refresh(): Promise<void>;
  /** 正在定时读桥（测试核对用）。 */
  readonly polling: boolean;
};

const refusedResult = (r: Pick<StartResult, "status">) => !SENT_STATES.has(r.status);
const codeOf = (r: StartResult) => r.reason || r.status || "";

function emptyDoc(docId: string, doc: DocumentOption | undefined): DocSnapshot {
  return {
    doc_id: docId,
    doc_type: "",
    status: "pending",
    docnum: "",
    voided: [],
    placeholders: [],
    reason: "",
    reports: [],
    seconds: 0,
    summary: doc ? { date: doc.date, party: doc.party, ref: doc.ref, total: doc.total, group: doc.group } : undefined,
  };
}

/** 没收的单：停在需要处理，原因按回包（交单状态或产品给的原因）；点了跳过的记成跳过。 */
function refusedDocs(started: Started, listed: ReadonlyMap<string, DocumentOption>): DocSnapshot[] {
  return started.results.filter(refusedResult).map((r) => ({
    ...emptyDoc(r.doc_id, listed.get(r.doc_id)),
    status: started.skipped.has(r.doc_id) ? "skipped" : "waiting",
    reason: codeOf(r),
    refused: true,
  }));
}

function countsOf(docs: readonly DocSnapshot[]): BatchSnapshot["counts"] {
  const counts: BatchSnapshot["counts"] = {};
  for (const d of docs) counts[d.status] = (counts[d.status] ?? 0) + 1;
  return counts;
}

/** 桥上还没有这个任务时的进度（网页、手机一直是这样）：交进去的等待，没收的停在需要处理。remote：这一页看不到执行进度。 */
export function startedSnapshot(started: Started, listed: ReadonlyMap<string, DocumentOption>): BatchSnapshot {
  const sent = started.results.filter((r) => !refusedResult(r)).map((r) => emptyDoc(r.doc_id, listed.get(r.doc_id)));
  const docs = [...sent, ...refusedDocs(started, listed)];
  const open = docs.some((d) => d.status === "pending");
  return {
    id: started.id,
    status: open ? "ready" : "finished",
    reason: open && started.start_at ? "waiting_start" : "",
    company: started.company,
    account_set: "",
    total: docs.length,
    counts: countsOf(docs),
    current: "",
    index: 0,
    events: [],
    started: 0,
    finished: 0,
    elapsed: 0,
    remaining_estimate: 0,
    docs,
    company_title: started.company_title,
    created: started.created,
    start_at: started.start_at,
    computer: "",
    period: started.period,
    remote: true,
  };
}

/** 桥上的进度并上这一页知道的：公司显示名、月份、建的时刻、没收的单（桥上没有）；要点按这一页的资料补。 */
/** 站点回的一张单（task_settle 的任务单据、status 的单据）；``blocked_by``：任务记录里是「单号 · 对方」，status 里是 {ref, party}。 */
type SiteDoc = {
  doc_id: string; state: string; docnum?: string; reason?: string; ref?: string; party?: string;
  blocked_by?: string | { ref?: string; party?: string };
};

/** 排着的单挂上「被哪张结果不明的单挡着」（站点给的，见 createLiveSource 的 blocked）。 */
export function withBlocked(s: BatchSnapshot, blocked: ReadonlyMap<string, string>): BatchSnapshot {
  if (!blocked.size || !s.docs.some((d) => d.status === "pending" && blocked.has(d.doc_id))) return s;
  return { ...s, docs: s.docs.map((d) => (d.status === "pending" && blocked.has(d.doc_id) ? { ...d, blocked: blocked.get(d.doc_id) } : d)) };
}

export function mergedSnapshot(bridge: BatchSnapshot, started: Started | undefined, listed: ReadonlyMap<string, DocumentOption>): BatchSnapshot {
  // 这台电脑没在跑、还没收尾的（CC 重启前留下的）：不显示进行中，按排队显示；站点核过后桥上收尾
  if (bridge.live === false && !isFinal(bridge.status)) bridge = { ...bridge, status: "ready", current: "" };
  const merged = mergedRunning(bridge, started, listed);
  return bridge.live === false && !isFinal(bridge.status) ? { ...merged, remote: true } : merged;
}

function mergedRunning(bridge: BatchSnapshot, started: Started | undefined, listed: ReadonlyMap<string, DocumentOption>): BatchSnapshot {
  const created = started?.created ?? (bridge.created || bridge.started || 0);
  const withSummary = bridge.docs.map((d) => {
    const doc = listed.get(d.doc_id);
    return d.summary || !doc ? d : { ...d, summary: { date: doc.date, party: doc.party, ref: doc.ref, total: doc.total, group: doc.group } };
  });
  if (!started) return { ...bridge, docs: withSummary, created, company_title: bridge.company_title || bridge.company, remote: false };
  const docs = [...withSummary, ...refusedDocs(started, listed)];
  return {
    ...bridge,
    // 侧栏、标题用这一页的公司编号与名称（桥上是 Express 里的公司名）
    company: started.company,
    docs,
    counts: countsOf(docs),
    total: Math.max(docs.length, started.results.length),
    company_title: started.company_title || bridge.company,
    period: started.period || bridge.period,
    created,
    start_at: started.start_at,
    remote: false,
  };
}

function listItem(s: BatchSnapshot): BatchListItem {
  return {
    id: s.id,
    status: s.status,
    reason: s.reason,
    company: s.company,
    total: s.total,
    counts: s.counts,
    docs: s.docs,
    company_title: s.company_title,
    created: s.created ?? 0,
    period: s.period,
    remote: s.remote,
  };
}

export function createLiveSource(deps: LiveDeps): LiveOperatorSource {
  const bridge = deps.bridge ?? desktop;
  const get: Get = deps.get ?? getMethod;
  const post: Post = deps.post ?? postMethod;
  const upload: Upload = deps.upload ?? uploadMethod;
  const now = deps.now ?? (() => Math.floor(Date.now() / 1000));
  const listed = new Map<string, DocumentOption>();
  // 排着的单被同一份账套里哪张结果不明的单挡着（doc_id → 「单号 · 对方」，站点给的，ADR-021 11-02 修订）
  const blocked = new Map<string, string>();
  const started = new Map<string, Started>();
  const listeners = new Set<(snapshot: BatchSnapshot) => void>();
  const last = new Map<string, string>();
  let operatorApi: Promise<Desktop | null> | null = null;
  let login: boolean | null = null;
  let bridged: BatchSnapshot[] = [];
  let timer: ReturnType<typeof setInterval> | null = null;
  let watching = false;

  /** 支持实操的桥（读一次 features 记住）；网页、手机、旧版桌面版回 null。 */
  const api = () =>
    (operatorApi ??= bridge()
      .then(async (found) => (found && (await found.state()).features?.includes(OPERATOR) ? found : null))
      .catch(withReport(null, "operator-bridge")));

  async function readBridge(): Promise<BatchSnapshot[]> {
    const found = await api();
    if (!found) return (bridged = []);
    const reply = (await found.call("operator_state")) as unknown as { ok?: boolean; batches?: BatchSnapshot[]; login?: boolean };
    if (!reply?.ok) return bridged;
    login = !!reply.login;
    bridged = Array.isArray(reply.batches) ? reply.batches : [];
    if (await settle(found, bridged)) return readBridge();
    report(bridged);
    return bridged;
  }

  // 任务的服务端记录变了（task_running、task_settle 回来的）：交给订阅者（侧栏、主栏换上）
  const taskListeners = new Set<(task: TaskRecord) => void>();
  const noteTask = (task: TaskRecord) => {
    for (const listener of taskListeners) listener(task);
    return task;
  };
  /** 以站点为准收尾一次（task_settle）：每个任务 SETTLE_S 内只问一次；回 null = 这次不问（刚问过）。 */
  const asked = new Map<string, number>();
  async function settleTask(name: string): Promise<TaskRecord | null> {
    if (now() - (asked.get(name) ?? -Infinity) < SETTLE_S) return null;
    asked.set(name, now());
    return noteTask(taskOf(await post(API + "task_settle", { name })));
  }

  // 桥上看到这台电脑开始录了：站点上转「进行中」（每个任务一次）；这台电脑上跑完了：以站点为准收尾一次。不是站点任务的旧批次
  // （任务记录出来前建的）站点不认，照旧只看桥
  const marked = new Set<string>();
  const closed = new Set<string>();
  function report(rows: readonly BatchSnapshot[]) {
    for (const row of rows) {
      if (row.live === false) continue;
      if ((row.status === "running" || row.status === "paused") && !marked.has(row.id)) {
        marked.add(row.id);
        void post(API + "task_running", { name: row.id }).then((reply) => noteTask(taskOf(reply)), () => undefined);
      }
      if (isFinal(row.status) && !closed.has(row.id)) {
        closed.add(row.id);
        asked.delete(row.id);
        void settleTask(row.id).catch(withReport(undefined, "operator-settle"));
      }
    }
  }

  // CC 重启后留下、这台电脑没在跑的任务：以站点为准收尾（task_settle：站点问产品这几张单现在怎样、写回任务），交回桥上收尾，本机
  // 的缓存跟着结束；没收尾的每 SETTLE_S 再问一次（刚重启时单子常还在队列里，之后站点才定成已入账 / 要人处理）。任务记录出来前建的
  // 旧批次站点不认，照旧按单问（status）；都问不到（断网、站点不认）就照旧按排队显示
  function noteBlocked(docs: readonly SiteDoc[]) {
    for (const d of docs) {
      const by = d.blocked_by;
      const text = typeof by === "string" ? by : by ? docTitle(by) : "";
      if (d.state === "queued" && text) blocked.set(String(d.doc_id), text);
      else blocked.delete(String(d.doc_id));
    }
  }

  // 这台电脑上开着、还没开始录（排队）的任务：每 SETTLE_S 问一次站点排着的单有没有被结果不明的单挡着，只记这一项、不收尾
  const askedBlocked = new Map<string, number>();
  async function askBlocked(row: BatchSnapshot) {
    const open = row.docs.filter((d) => d.status === "pending").map((d) => d.doc_id);
    if (!open.length || now() - (askedBlocked.get(row.id) ?? -Infinity) < SETTLE_S) return;
    askedBlocked.set(row.id, now());
    try {
      const reply = await get<{ documents?: SiteDoc[] }>(API + "status", { company: row.site!, doc_ids: JSON.stringify(open) });
      noteBlocked(reply?.documents ?? []);
    } catch (caughtError) { reportCaught(caughtError, "operator-settle");
      // 断网或站点不认：照旧按排队显示
    }
  }

  async function settle(found: Desktop, rows: readonly BatchSnapshot[]): Promise<boolean> {
    let changed = false;
    for (const row of rows) {
      if (row.live !== false && !isFinal(row.status) && row.status !== "running" && row.site) await askBlocked(row);
      if (row.live !== false || isFinal(row.status) || !row.site) continue;
      if (now() - (asked.get(row.id) ?? -Infinity) < SETTLE_S) continue;
      // 录到一半被 CC 重启打断、收拾时作废了的（cc_restarted）也算没定：站点会把它定成结果不明，要按站点的结论收尾（10-29 测试机）
      const open = row.docs.filter((d) => d.status === "pending" || d.status === "entering"
        || (d.status === "waiting" && d.reason === "cc_restarted")).map((d) => d.doc_id);
      if (!open.length) continue;
      try {
        let docs: SiteDoc[];
        try {
          docs = (await settleTask(row.id))?.documents ?? [];
        } catch (caughtError) { reportCaught(caughtError, "operator-settle");
          const reply = await get<{ documents?: typeof docs }>(API + "status", { company: row.site, doc_ids: JSON.stringify(open) });
          docs = reply?.documents ?? [];
        }
        noteBlocked(docs);
        for (const d of docs) {
          // 这一页没有资料清单（CC 重启后）：按站点回的单号、对方把单据写成「单号 · 对方」
          if (!listed.has(String(d.doc_id)) && (d.ref || d.party)) listed.set(String(d.doc_id), { id: String(d.doc_id), group: "purchase", ref: d.ref || undefined, party: d.party || undefined });
        }
        const results = Object.fromEntries(docs.map((d) => [String(d.doc_id), d]));
        const done = await found.call("operator_control", { batch: row.id, action: "settle", results });
        changed = changed || !!done?.ok;
      } catch (caughtError) { reportCaught(caughtError, "operator-settle");
        // 断网或站点不认：留着按排队显示
      }
    }
    return changed;
  }

  async function all(fresh: boolean): Promise<BatchSnapshot[]> {
    const rows = fresh ? await readBridge().catch(withReport(() => bridged, "operator-bridge")) : bridged;
    const byId = new Map<string, BatchSnapshot>();
    for (const row of rows) byId.set(row.id, withBlocked(mergedSnapshot(row, started.get(row.id), listed), blocked));
    for (const s of started.values()) if (!byId.has(s.id)) byId.set(s.id, startedSnapshot(s, listed));
    return [...byId.values()];
  }

  function emit(snapshots: readonly BatchSnapshot[]) {
    for (const s of snapshots) {
      const key = JSON.stringify(s);
      if (last.get(s.id) === key) continue;
      last.set(s.id, key);
      for (const listener of listeners) listener(s);
    }
  }

  const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";

  // 定时读桥：有订阅者、页面看得见、桥支持实操时才跑；页面切到后台停，回到前台先读一次再接着
  function stopPolling() {
    if (timer !== null) clearInterval(timer);
    timer = null;
  }
  async function startPolling() {
    if (timer !== null || !listeners.size || !visible() || !(await api())) return;
    // 等桥的那一下里可能已经退订、切到后台或另一处已经开了
    if (timer !== null || !listeners.size || !visible()) return;
    timer = setInterval(() => void source.refresh(), POLL_MS);
  }
  const onVisibility = () => {
    if (!visible()) return stopPolling();
    void source.refresh();
    void startPolling();
  };
  function watch(on: boolean) {
    if (on === watching || typeof document === "undefined") return;
    watching = on;
    if (on) document.addEventListener("visibilitychange", onVisibility);
    else document.removeEventListener("visibilitychange", onVisibility);
  }

  // 对话里交代的跳过 / 重做（对话框甲）：记在站点的任务上，这台电脑上在跑的批次每 CONTROLS_S 秒取一次、照桥上控制做（取走即清空）
  const controlsAsked = new Map<string, number>();
  async function takeControls(rows: readonly BatchSnapshot[]) {
    for (const s of rows) {
      if (s.remote || isFinal(s.status) || now() - (controlsAsked.get(s.id) ?? 0) < CONTROLS_S) continue;
      controlsAsked.set(s.id, now());
      try {
        const reply = await post<{ controls?: Array<{ action?: string; doc_id?: string }> }>(API + "task_controls", { name: s.id });
        for (const c of reply?.controls ?? []) {
          if ((c.action === "skip" || c.action === "retry") && c.doc_id) await control(s.id, c.action, String(c.doc_id)).catch(withReport(undefined, "operator-controls"));
        }
      } catch (caughtError) { reportCaught(caughtError, "operator-controls");
        // 断网、不是站点上的任务：下一次再取
      }
    }
  }

  async function control(batch: string, action: string, doc = "") {
    const found = await api();
    const reply = found ? await found.call("operator_control", { batch, action, doc }) : null;
    if (!reply?.ok) throw new Error(`operator_control ${action} refused`);
    await source.refresh();
  }

  const source: LiveOperatorSource = {
    get polling() {
      return timer !== null;
    },

    async refresh() {
      const rows = await all(true);
      emit(rows);
      void takeControls(rows);
    },

    async listTasks(start, limit) {
      const reply = await get<{ tasks?: unknown[]; next?: number | null }>(API + "task_list", { start, limit });
      return { tasks: (reply?.tasks ?? []).map(taskOf), next: typeof reply?.next === "number" ? reply.next : null };
    },
    async getTask(name) {
      const task = taskOf(await get(API + "task_get", { name }));
      // 交出去还没收尾、这台电脑上没在跑的（网页、手机、别的电脑、CC 重启后）：以站点为准收尾一次
      const here = bridged.some((b) => b.id === name && b.live !== false && !isFinal(b.status));
      if (OPEN_TASK.has(task.status) && !here) return (await settleTask(name).catch(withReport(null, "operator-settle"))) ?? task;
      return task;
    },
    async createTask(input) {
      return taskOf(await post(API + "task_create", { company: input.company, period: input.period, company_title: input.company_title }));
    },
    async updateTask(name, patch) {
      const args: Record<string, unknown> = { name, ...patch };
      if (patch.starred !== undefined) args.starred = patch.starred ? 1 : 0;
      return noteTask(taskOf(await post(API + "task_update", args)));
    },
    async taskThread(name) {
      const reply = await post<{ session_id?: string }>(API + "task_thread", { name });
      if (!reply?.session_id) throw new Error("task thread not created");
      return String(reply.session_id);
    },
    async answerTask(name, docId, answers) {
      return noteTask(taskOf(await post(API + "task_answer", { name, doc_id: docId, answers: JSON.stringify(answers) })));
    },
    subscribeTasks(onChange) {
      taskListeners.add(onChange);
      return () => void taskListeners.delete(onChange);
    },
    async resolve(company, docId, entered, batchId) {
      const reply = await post<{ ok?: boolean }>(API + "resolve", { company, doc_id: docId, entered: entered ? 1 : 0, docnum: "" });
      if (reply && reply.ok === false) throw new Error("resolve refused");
      if (!batchId) return;
      // 交出去之后标的（CC 重启打断的那张）：以站点为准把这个任务再收尾一次，单子转已入账 / 没有入账
      const found = await api();
      const task = noteTask(taskOf(await post(API + "task_settle", { name: batchId })));
      asked.set(batchId, now());
      for (const d of task.documents ?? []) {
        if (!listed.has(String(d.doc_id)) && (d.ref || d.party)) listed.set(String(d.doc_id), { id: String(d.doc_id), group: "purchase", ref: d.ref || undefined, party: d.party || undefined });
      }
      if (found) await found.call("operator_control", { batch: batchId, action: "settle",
        results: Object.fromEntries((task.documents ?? []).map((d) => [String(d.doc_id), d])) });
      emit(await all(true));
    },

    async listBatches(query = {}) {
      const rows = (await all(true)).map(listItem).sort((a, b) => b.created - a.created);
      const start = query.start ?? 0;
      return rows.slice(start, start + (query.limit ?? 50));
    },
    async snapshot(batchId) {
      const found = (await all(true)).find((s) => s.id === batchId);
      if (found) return found;
      // 这台电脑上没有：按站点任务记录里的交单结论
      return taskSnapshot(await source.getTask(batchId), listed);
    },
    subscribe(onChange) {
      listeners.add(onChange);
      watch(true);
      void startPolling();
      return () => {
        listeners.delete(onChange);
        if (listeners.size) return;
        stopPolling();
        watch(false);
      };
    },

    async companies(search, start, limit) {
      if (deps.product === "erp") {
        const rows = await get<LinkRow[]>("frappe.desk.search.search_link", {
          doctype: ERP_COMPANY,
          txt: search,
          page_length: start + limit,
        });
        return (rows ?? []).slice(start, start + limit).map((row) => ({ id: row.value, title: row.label || row.value }));
      }
      const rows = await listCompanies(search, start, limit);
      return rows.map((row): CompanyOption => ({ id: row.id, title: row.title, tax_id: row.tax_id }));
    },
    async documents(company, period, start, limit) {
      const reply = await get<DocumentsReply>(API + "documents", { company, period, offset: start, limit });
      const documents = (reply?.documents ?? []).map((row) => {
        const doc: DocumentOption = {
          id: String(row.doc_id),
          kind: row.kind ?? "",
          group: groupOf(row),
          date: row.date || undefined,
          party: row.party || undefined,
          ref: row.ref || undefined,
          total: row.total || undefined,
          stopped: row.stopped || undefined,
        };
        listed.set(doc.id, doc);
        return doc;
      });
      const next = typeof reply?.next_offset === "number" ? reply.next_offset : null;
      return { documents, next, total: reply?.total };
    },
    async precheck(company, docIds) {
      const reply = await post<{ documents?: PrecheckDoc[] }>(API + "precheck", { company, doc_ids: JSON.stringify(docIds) });
      return (reply?.documents ?? []).map((row) => ({ ...row, doc_id: String(row.doc_id) }));
    },
    async regroup(company, docId, group: DocGroup) {
      const reply = await post<{ ok?: boolean }>(API + "regroup", { company, doc_id: docId, group });
      if (!reply?.ok) throw new Error("regroup refused");
      const doc = listed.get(docId);
      if (doc) listed.set(docId, { ...doc, group });
    },
    async intake(company, _period, files) {
      // 每次上传一个新请求号：同一份文件再放一次照常存、照常识别（重复上传也照常扣费）
      for (const file of files) await upload(API + "intake", { company, request_key: crypto.randomUUID() }, file);
    },
    async computers() {
      // 交给「本人自己的电脑」（computer 空串）；Express 登录设置了没有只在桥上知道
      const found = await api();
      if (found) await readBridge().catch(withReport(() => bridged, "operator-bridge"));
      const pc: OperatorComputer = { id: "", name: op.thisComputer, online: true, local: !!found, login: found ? login : null };
      return [pc];
    },
    async authorizeLogin() {
      const found = await api();
      if (!found) return { ok: false };
      const reply = await found.call("operator_authorize");
      if (reply?.ok) login = true;
      return { ok: !!reply?.ok };
    },
    async createBatch(input: CreateBatchInput) {
      const reply = await post<{ batch: string; results?: StartResult[] }>(API + "start", {
        company: input.company,
        doc_ids: JSON.stringify(input.doc_ids),
        computer: input.computer,
        start_at: input.start_at,
        answers: JSON.stringify(input.answers),
        task: input.task ?? "",
        groups: JSON.stringify(input.groups ?? {}),
      });
      const id = String(reply.batch);
      started.set(id, {
        id,
        company: input.company,
        company_title: input.company_title ?? "",
        period: input.period,
        created: now(),
        start_at: input.start_at,
        results: (reply.results ?? []).map((r) => ({ doc_id: String(r.doc_id), status: String(r.status ?? ""), reason: String(r.reason ?? "") })),
        skipped: new Set(),
      });
      emit(await all(false));
      return { id };
    },

    start: (batchId) => control(batchId, "resume"),
    stop: (batchId) => control(batchId, "stop"),
    async skip(batchId, docId) {
      // 交单时没收的单只在这一页：标成跳过；其余交给执行的电脑
      const mine = started.get(batchId);
      if (mine?.results.some((r) => r.doc_id === docId && refusedResult(r))) {
        mine.skipped.add(docId);
        emit(await all(false));
        return;
      }
      await control(batchId, "skip", docId);
    },
    retry: (batchId, docId) => control(batchId, "retry", docId),
    async remove(batchId) {
      // 先删站点的任务记录（交出去还没收尾的站点不让删）；桥上有的交给桥删本机的进度（录单中的桥回 ok:false：站点已删时本机的
      // 进度留到 CC 收尾）。任务记录出来前建的旧批次站点不认，桥删得掉就算删了
      const refused = await post(API + "task_delete", { name: batchId }).then(() => null, (error: unknown) => error ?? new Error("task_delete"));
      const local = bridged.some((b) => b.id === batchId) ? await control(batchId, "delete").then(() => true, () => false) : false;
      if (refused && !local) throw refused;
      started.delete(batchId);
      last.delete(batchId);
    },
  };
  return source;
}
