// 只给本地预览与测试用的假数据源（不是真实来源，不进产品入口）：内存里三家公司、几张单、两台电脑、两个已有任务（任务记录同站点的
// task_*：准备中的只有记录，开始后批次号 = 任务号）。C-003 有一张上次推送结果不明的单（开始前确认里标已入账 / 没有入账）。
// 不开计时器：进度由调用方 step() 推一步（测试里直接调），推一步 = 正在做的那张出结果、下一张开始。
// 编号以 "-X" 结尾的那张做的时候被 Express 拦（库存不够），用来看「需要处理」。

import type {
  AuthorizeTarget,
  BatchListItem,
  BatchSnapshot,
  BatchStatus,
  CompanyOption,
  CreateBatchInput,
  DocGroup,
  DocSnapshot,
  DocumentOption,
  OperatorComputer,
  OperatorSource,
  PrecheckDoc,
  TaskPatch,
  TaskRecord,
  TaskStatus,
} from "./types";
import { NEW_CODE } from "./types";
import type { TaskUpload } from "@/components/run/types";

type MockBatch = {
  id: string;
  company: string;
  company_title: string;
  period: string;
  computer: string;
  status: BatchStatus;
  reason: string;
  created: number;
  started: number;
  finished: number;
  current: string;
  docs: DocSnapshot[];
};

export type MockOperatorSource = OperatorSource & {
  /** 推一步：正在做的那张出结果（已入账 / 停下），下一张开始；没有可做的就结束。 */
  step(batchId: string): void;
  /** 当前内存里的数据与调用记录（测试核对用）。 */
  readonly batches: ReadonlyMap<string, Readonly<MockBatch>>;
  readonly computerList: readonly OperatorComputer[];
  readonly calls: ReadonlyArray<readonly [string, ...unknown[]]>;
};

const COMPANIES: CompanyOption[] = [
  { id: "C-001", title: "บริษัท Pearnly จำกัด", tax_id: "0105561000001" },
  { id: "C-002", title: "นิภา เอ็กเช้นจ์ จำกัด", tax_id: "0305562000002" },
  { id: "C-003", title: "ทองไทย เทรดดิ้ง จำกัด", tax_id: "0105563000003" },
];

const DOCUMENTS: Record<string, DocumentOption[]> = {
  "C-001": [
    { id: "D-101", kind: "purchase", group: "purchase", date: "2026-10-03", party: "ไฟฟ้ารุ่งอรุณ ซัพพลาย", ref: "RA690922-01", total: "10700.00" },
    { id: "D-102", kind: "purchase", group: "purchase", date: "2026-10-05", party: "ป้ายดีมีเดีย แอดเวอร์ไทซิ่ง", ref: "PDM-INV-0926-018", total: "5350.00" },
    { id: "D-103", kind: "sales", group: "sales", date: "2026-10-06", party: "บ้านดีโฮมบิลเดอร์", ref: "IV6909-0018", total: "21400.00" },
    { id: "D-104-X", kind: "sales", group: "sales", date: "2026-10-08", party: "ท่อทองไพพ์", ref: "TT-6909-0104", total: "963.00" },
    { id: "D-105", kind: "purchase", group: "expense", date: "2026-10-26", party: "ปั๊มน้ำมัน", ref: "RC-2610", total: "1200.00" },
    { id: "D-106", kind: "purchase", group: "skip", date: "2026-10-28", party: "ร้านวัสดุ", ref: "QT-1102", total: "4500.00" },
  ],
  "C-002": [
    { id: "D-201", kind: "purchase", group: "purchase", date: "2026-10-02", party: "MINBURI ICE CO., LTD.", ref: "MB-8812", total: "1200.00" },
    { id: "D-202", kind: "purchase", group: "expense", date: "2026-10-04", party: "ค่าน้ำมัน", ref: "RC-9", total: "800.00" },
  ],
  "C-003": [
    { id: "D-301", kind: "purchase", group: "purchase", date: "2026-10-09", party: "ทองไทย การค้า", ref: "TT-1009", total: "3210.00" },
    { id: "D-302", kind: "sales", group: "sales", date: "2026-10-12", party: "สยามโฮม", ref: "IV-1012", total: "5400.00" },
  ],
};

const PRECHECK: Record<string, Omit<PrecheckDoc, "doc_id">> = {
  "D-102": {
    state: "choose",
    needed: [
      { kind: "party", subject: "S-PDM", label: "ป้ายดีมีเดีย แอดเวอร์ไทซิ่ง", options: [{ code: NEW_CODE, name: "" }, { code: "S-0012", name: "ป้ายดีมีเดีย" }] },
      { kind: "payment", subject: "S-PDM", label: "ป้ายดีมีเดีย แอดเวอร์ไทซิ่ง", options: [{ code: "cash", name: "" }, { code: "credit", name: "" }] },
    ],
  },
  "D-201": {
    state: "choose",
    needed: [
      { kind: "item", subject: "ICE20", label: "น้ำแข็งหลอด 20 กก.", options: [{ code: NEW_CODE, name: "" }, { code: "P-001", name: "น้ำแข็งก้อน" }] },
      { kind: "party", subject: "S-MB", label: "MINBURI ICE CO., LTD.", options: [{ code: NEW_CODE, name: "" }] },
    ],
  },
  "D-202": { state: "choose", needed: [{ kind: "account", subject: "S-FUEL", label: "ค่าน้ำมัน", options: [{ code: "53-02", name: "ค่าน้ำมัน" }] }] },
  "D-105": { state: "waiting", reason: "reading" },
  "D-301": { state: "blocked", reason: "push.result_unknown", message: "ผลการส่งครั้งก่อนไม่ชัดเจน ตรวจสอบใน Express" },
};

/** 站点的日期时间字符串（本地时间，同站点 task_* 回的样子）。 */
function siteTime(seconds: number): string {
  if (!seconds) return "";
  const d = new Date(seconds * 1000);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

type MockTask = { name: string; company: string; company_title: string; period: string; title: string; starred: boolean; created: number; modified: number };

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function newDoc(doc: DocumentOption): DocSnapshot {
  return {
    doc_id: doc.id,
    doc_type: "",
    status: "pending",
    docnum: "",
    voided: [],
    placeholders: [],
    reason: "",
    reports: [],
    seconds: 0,
    summary: { date: doc.date, party: doc.party, ref: doc.ref, total: doc.total, group: doc.group },
  };
}

export function createMockSource(options: { now?: () => number; local?: boolean } = {}): MockOperatorSource {
  const now = options.now ?? (() => Math.floor(Date.now() / 1000));
  const listeners = new Set<(snapshot: BatchSnapshot) => void>();
  const batches = new Map<string, MockBatch>();
  const documents = clone(DOCUMENTS);
  const computers: OperatorComputer[] = [
    { id: "PC-1", name: "OFFICE-PC", online: true, local: options.local ?? true, login: false },
    { id: "PC-2", name: "REC-LAPTOP", online: false, local: false, login: true },
  ];
  const calls: Array<readonly [string, ...unknown[]]> = [];
  const retried = new Set<string>();
  const resolved = new Set<string>();
  const tasks = new Map<string, MockTask>();
  const threads = new Map<string, string>();
  const taskListeners = new Set<(task: TaskRecord) => void>();
  let serial = 0;
  const uploads: Record<string, TaskUpload[]> = {};
  let docnum = 61;

  const find = (id: string) => Object.values(documents).flat().find((d) => d.id === id);

  function snapshotOf(b: MockBatch): BatchSnapshot {
    const counts: BatchSnapshot["counts"] = {};
    for (const d of b.docs) counts[d.status] = (counts[d.status] ?? 0) + 1;
    return clone({
      id: b.id,
      status: b.status,
      reason: b.reason,
      company: b.company,
      account_set: "",
      total: b.docs.length,
      counts,
      current: b.current,
      index: b.current ? b.docs.findIndex((d) => d.doc_id === b.current) + 1 : 0,
      events: [],
      started: b.started,
      finished: b.finished,
      elapsed: b.started ? (b.finished || now()) - b.started : 0,
      remaining_estimate: 0,
      docs: b.docs,
      company_title: b.company_title,
      created: b.created,
      start_at: 0,
      computer: b.computer,
      period: b.period,
    });
  }

  const emit = (b: MockBatch) => {
    const snap = snapshotOf(b);
    for (const listener of listeners) listener(snap);
  };
  const get = (id: string) => {
    const b = batches.get(id);
    if (!b) throw new Error(`no batch ${id}`);
    return b;
  };

  /** 下一张开始（没有就结束）。 */
  function next(b: MockBatch) {
    const doc = b.docs.find((d) => d.status === "pending");
    if (!doc) {
      Object.assign(b, { status: "finished", reason: "", current: "", finished: now() });
      return;
    }
    doc.status = "entering";
    b.current = doc.doc_id;
  }
  function begin(b: MockBatch) {
    Object.assign(b, { status: "running", reason: "", started: b.started || now() });
    if (!b.current) next(b);
  }

  // 两个已有任务：一个做完（一张需要处理）、一个停在登录没设置
  function seed(company: string, ids: string[], state: "finished" | "stopped", ago: number) {
    const b: MockBatch = {
      id: `B-${++serial}`,
      company,
      company_title: COMPANIES.find((c) => c.id === company)?.title ?? company,
      period: "2026-09",
      computer: "PC-1",
      status: state,
      reason: state === "stopped" ? "login_missing" : "",
      created: now() - ago,
      started: now() - ago,
      finished: state === "finished" ? now() - ago + 41 : 0,
      current: "",
      docs: ids.map((id) => newDoc(find(id)!)),
    };
    if (state === "finished") {
      b.docs.forEach((d) => {
        d.seconds = 13;
        if (d.doc_id.endsWith("-X")) {
          Object.assign(d, { status: "waiting", reason: "negative_stock", voided: ["HP0000031"], reports: [{ reason: "voided", docnum: "HP0000031" }] });
        } else Object.assign(d, { status: "saved", docnum: `RR00000${++docnum}` });
      });
    }
    batches.set(b.id, b);
    tasks.set(b.id, { name: b.id, company, company_title: b.company_title, period: b.period, title: "", starred: false, created: b.created, modified: b.created });
  }
  seed("C-001", ["D-101", "D-103", "D-104-X"], "finished", 86400 * 30);
  seed("C-002", ["D-201"], "stopped", 86400);

  /** 任务的状态：没开始的准备中；开始了的按批次（排队 / 进行中 / 停下等人或有要人处理的 = 需要你处理 / 已完成 / 已停止）。 */
  function statusOf(name: string): TaskStatus {
    const b = batches.get(name);
    if (!b) return "preparing";
    if (b.status === "running" || b.status === "paused") return "running";
    if (b.status === "ready") return "queued";
    if (b.reason || b.docs.some((d) => d.status === "waiting")) return "attention";
    return b.status === "stopped" ? "stopped" : "finished";
  }
  function recordOf(t: MockTask, documents = false): TaskRecord {
    const b = batches.get(t.name);
    const record: TaskRecord = {
      name: t.name,
      company: t.company,
      company_title: t.company_title,
      period: t.period,
      title: t.title,
      starred: t.starred,
      status: statusOf(t.name),
      computer: b?.computer ?? "",
      start_at: "",
      started_at: siteTime(b?.started ?? 0),
      finished_at: siteTime(b?.finished ?? 0),
      session_id: threads.get(t.name) ?? "",
      creation: siteTime(t.created),
      modified: siteTime(Math.max(t.modified, b?.started ?? 0, b?.finished ?? 0)),
    };
    if (!documents) return record;
    const state = (d: DocSnapshot) => (d.status === "saved" ? "saved" : d.status === "waiting" ? "not_done" : d.status === "skipped" ? "skipped" : "queued");
    return {
      ...record,
      documents: (b?.docs ?? []).map((d) => ({ doc_id: d.doc_id, doc_group: d.summary?.group ?? "", state: state(d), docnum: d.docnum,
        voided_docnum: d.voided[0] ?? "", reason: d.reason, ref: d.summary?.ref ?? "", party: d.summary?.party ?? "", blocked_by: d.blocked ?? "" })),
    };
  }
  const task = (name: string) => {
    const t = tasks.get(name);
    if (!t) throw new Error(`no task ${name}`);
    return t;
  };

  const source: MockOperatorSource = {
    get batches() {
      return batches;
    },
    get computerList() {
      return computers;
    },
    get calls() {
      return calls;
    },

    async listBatches(query = {}) {
      const rows: BatchListItem[] = [...batches.values()]
        .sort((a, b) => b.created - a.created)
        .map((b) => {
          const s = snapshotOf(b);
          return { id: s.id, status: s.status, reason: s.reason, company: s.company, total: s.total, counts: s.counts, docs: s.docs,
            company_title: b.company_title, created: b.created, period: b.period };
        });
      const start = query.start ?? 0;
      return rows.slice(start, start + (query.limit ?? 50));
    },
    async snapshot(batchId) {
      return snapshotOf(get(batchId));
    },
    async listTasks(start, limit) {
      const rows = [...tasks.values()].map((t) => recordOf(t)).sort((a, b) => b.modified.localeCompare(a.modified));
      return { tasks: rows.slice(start, start + limit), next: start + limit < rows.length ? start + limit : null };
    },
    async getTask(name) {
      return recordOf(task(name), true);
    },
    async createTask(input) {
      calls.push(["task_create", input.company, input.period]);
      const name = `T-${++serial}`;
      const at = now();
      tasks.set(name, { name, company: input.company, company_title: input.company_title, period: input.period, title: "", starred: false, created: at, modified: at });
      return recordOf(task(name), true);
    },
    async updateTask(name, patch: TaskPatch) {
      calls.push(["task_update", name, patch]);
      const t = task(name);
      if ((patch.company !== undefined || patch.period !== undefined) && statusOf(name) !== "preparing") throw new Error("started");
      Object.assign(t, Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined)), { modified: patch.starred === undefined ? now() : t.modified });
      const record = recordOf(t, true);
      for (const listener of taskListeners) listener(record);
      return record;
    },
    subscribeTasks(onChange) {
      taskListeners.add(onChange);
      return () => void taskListeners.delete(onChange);
    },
    async resolve(company, docId, entered, batchId) {
      calls.push(["resolve", company, docId, entered, ...(batchId ? [batchId] : [])]);
      resolved.add(docId);
    },
    async taskThread(name) {
      calls.push(["task_thread", name]);
      task(name);
      if (!threads.has(name)) threads.set(name, `sesn_${name}`);
      return threads.get(name)!;
    },
    async answerTask(name, docId, answers) {
      calls.push(["task_answer", name, docId, answers]);
      const doc = get(name).docs.find((d) => d.doc_id === docId);
      if (!doc || doc.status !== "waiting") throw new Error("not waiting");
      Object.assign(doc, { status: "pending", reason: "" });
      return recordOf(task(name), true);
    },
    subscribe(onChange) {
      listeners.add(onChange);
      return () => listeners.delete(onChange);
    },

    async companies(search, start, limit) {
      const needle = search.trim().toLowerCase();
      return COMPANIES.filter((c) => !needle || `${c.title} ${c.tax_id ?? ""}`.toLowerCase().includes(needle)).slice(start, start + limit);
    },
    async documents(company, period, start, limit) {
      const rows = (documents[company] ?? []).filter((d) => !period || String(d.date ?? "").startsWith(period));
      const page = rows.slice(start, start + limit);
      return { documents: clone(page), next: start + limit < rows.length ? start + limit : null, total: rows.length };
    },
    async precheck(company, docIds) {
      calls.push(["precheck", company, [...docIds]]);
      const documents = docIds.map((id) => clone({ doc_id: id, ...((!resolved.has(id) && PRECHECK[id]) || { state: "ready" as const }) }));
      const title = COMPANIES.find((c) => c.id === company)?.title ?? company;
      return { documents, target: { account_set: `${company}-2026`, account_set_title: `${title} 2026`, connection_title: "Express", computer: "" } };
    },
    async regroup(company, docId, group: DocGroup) {
      calls.push(["regroup", company, docId, group]);
      const doc = (documents[company] ?? []).find((d) => d.id === docId);
      if (doc) doc.group = group;
    },
    async intake({ company, period, task, file, onProgress }) {
      calls.push(["intake", company, period, file.name]);
      onProgress?.(100);
      (documents[company] ??= []).push({ id: `U-${++serial}`, kind: "purchase", group: "purchase", date: `${period}-15`, ref: file.name });
      (uploads[task] ??= []).push({
        original: `O-${serial}`, filename: file.name, size: file.size, job: `J-${serial}`, uploaded_at: new Date(now() * 1000).toISOString(),
        status: "done", pages_done: 1, page_count: 1, documents: [{ name: `U-${serial}`, date: `${period}-15`, in_period: true, in_sources: true }],
      });
    },
    async taskUploads(name) {
      return clone(uploads[name] ?? []);
    },
    async computers() {
      return clone(computers);
    },
    async authorizeLogin(target: AuthorizeTarget) {
      calls.push(["authorize", target]);
      const id = "computer" in target ? target.computer : get(target.batch).computer;
      const pc = computers.find((c) => c.id === id);
      if (pc) pc.login = true;
      return { ok: !!pc };
    },
    async createBatch(input: CreateBatchInput) {
      calls.push(["start", input]);
      const docs = input.doc_ids.map((id) => find(id)).filter((d): d is DocumentOption => !!d);
      const b: MockBatch = {
        id: input.task || `B-${++serial}`,
        company: input.company,
        company_title: input.company_title || COMPANIES.find((c) => c.id === input.company)?.title || input.company,
        period: input.period,
        computer: input.computer,
        status: "ready",
        reason: "",
        created: now(),
        started: 0,
        finished: 0,
        current: "",
        docs: docs.map((d) => newDoc(d)),
      };
      batches.set(b.id, b);
      if (!input.start_at) begin(b);
      emit(b);
      return { id: b.id };
    },

    async start(batchId) {
      calls.push(["resume", batchId]);
      const b = get(batchId);
      if (b.status !== "finished") begin(b);
      emit(b);
    },
    async stop(batchId) {
      calls.push(["stop", batchId]);
      const b = get(batchId);
      const doc = b.docs.find((d) => d.doc_id === b.current);
      if (doc) doc.status = "pending";
      Object.assign(b, { status: "stopped", reason: "", current: "" });
      emit(b);
    },
    async skip(batchId, docId) {
      calls.push(["skip", batchId, docId]);
      const b = get(batchId);
      const doc = b.docs.find((d) => d.doc_id === docId);
      if (doc && (doc.status === "pending" || doc.status === "waiting")) doc.status = "skipped";
      emit(b);
    },
    async retry(batchId, docId) {
      calls.push(["retry", batchId, docId]);
      const b = get(batchId);
      const doc = b.docs.find((d) => d.doc_id === docId && d.status === "waiting");
      if (doc) {
        Object.assign(doc, { status: "pending", reason: "" });
        retried.add(doc.doc_id);
      }
      if (b.status !== "running") begin(b);
      emit(b);
    },
    async resume(batchId) {
      calls.push(["task_resume", batchId]);
      const b = get(batchId);
      // 因整批停没录成的（卡住的那张 + 因停止跳过的）交回去重录；别的不动
      for (const doc of b.docs) {
        if ((doc.status === "skipped" && doc.reason === "stopped") || (doc.status === "waiting" && doc.reason === b.reason)) {
          Object.assign(doc, { status: "pending", reason: "" });
        }
      }
      begin(b);
      emit(b);
    },
    async remove(batchId) {
      calls.push(["delete", batchId]);
      const b = batches.get(batchId);
      if (!b && !tasks.has(batchId)) throw new Error(`no task ${batchId}`);
      if (b && b.status !== "finished" && b.status !== "stopped") throw new Error("running");
      batches.delete(batchId);
      tasks.delete(batchId);
    },

    step(batchId) {
      const b = get(batchId);
      if (b.status !== "running") return;
      const doc = b.docs.find((d) => d.doc_id === b.current);
      if (doc) {
        doc.seconds = 13;
        if (doc.doc_id.endsWith("-X") && !retried.has(doc.doc_id)) {
          Object.assign(doc, { status: "waiting", reason: "negative_stock", voided: [`HP00000${++docnum}`] });
        } else {
          Object.assign(doc, { status: "saved", docnum: `RR00000${++docnum}` });
        }
        b.current = "";
      }
      next(b);
      emit(b);
    },
  };
  return source;
}
