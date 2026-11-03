"""CC「实操」工作区（components/operator）：纯逻辑、假数据源下的三语文案与渲染；真实数据源（live-source.ts）在假站点、假桥下
的行为；左上角「直写 / 实操」两个图标与快捷键（lib/nav.ts）；原因代码与 CC 桌面版 contract.py 一致。

断言的是应有状态：三语都有词、原因码不外露、旧词不出现、开始前确认答完才能开始、跳过 / 重试交给桥、右栏分区处理完收起、
窄屏抽屉。设计稿 https://claude.ai/artifact/SgtxECheQE4e6gtF1G4WKW。

    python3 tests/test_cc_operator.py
"""
import re
import sys
import unittest

from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")
from test_assistant_ui import SOCKET_STUB, run_ts  # noqa: E402

SRC = Path(ROOT) / "frontend/assistant/src"

LOGIC = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createMockSource } from "@/components/operator/mock-source";
import {
  allAnswered, answersFor, batchActivity, batchNeedsHuman, batchStatusText, deletable, docRow, docsToStart, duration, finishLine, groupDocuments, groupOf, isRawCode,
  kindTitle, needsYou, optionLabel, questionsOf, reasonText, reportCsv, stoppedDocs, taskMeta, taskTone,
} from "@/components/operator/model";
import { canStart, draftStartInput, initialComputer, startBlocker } from "@/components/operator/controller";
import { BATCH_STOPPERS, levelOf, NEW_CODE, REASONS } from "@/components/operator/types";
import { canDelete, taskSidebar, taskTitle, titleOf } from "@/components/operator/operator-sidebar";
import { taskActivity } from "@/components/operator/tasks";
import { stoppedDocs as stopped2, unknownDocs } from "@/components/operator/model";
import { collectPrefs } from "@/lib/prefs-sync";
import { useSidebarOrder } from "@/lib/managed-agents/sidebar-order";
import { taskOrder } from "@/components/operator/prefs";
import { choiceSection } from "@/components/company-cards";
import { co, op, steps } from "@/i18n";

const g = globalThis as any;

/** 词典里所有给人看的字（函数按样例参数展开）。 */
function words(value: unknown): string[] {
  if (typeof value === "string") return [value];
  if (typeof value === "function") return [String((value as (...a: unknown[]) => unknown)(3, 2, "1 min"))];
  if (value && typeof value === "object") return Object.values(value).flatMap(words);
  return [];
}

(async () => {
  const lang = document.documentElement.lang;
  // 级别与 contract.py 一致；新原因 company_pick 必须人工；不认识的按必须人工
  assert.equal(levelOf("company_pick"), "human");
  assert.equal(levelOf("negative_stock"), "human");
  assert.equal(levelOf("duplicate_ref"), "auto_reported");
  assert.equal(levelOf("screen_locked"), "auto");
  assert.equal(levelOf("brand_new"), "human");
  assert.equal(Object.keys(REASONS).length, 31);
  assert.deepEqual([...BATCH_STOPPERS].sort(), ["account_in_use", "account_set_unavailable", "budget", "company_mismatch", "license", "login_failed", "login_missing", "network_down"]);
  // 每个原因代码都有这种语言的文案，且不是代码本身
  for (const code of Object.keys(REASONS)) {
    assert.ok(op.reason[code], code);
    assert.notEqual(op.reason[code], code);
    assert.equal(reasonText(code), op.reason[code]);
  }
  // 原码绝不外露：内部代码、choice_needed:…、reading 一类都换成文案；认不出的写「需要处理」；站点给的已翻译原文照用
  assert.equal(reasonText("[OPERATOR_LOGIN_TIMEOUT]"), op.unknownReason);
  assert.equal(reasonText("", "[OPERATOR_LOGIN_TIMEOUT] login"), op.unknownReason);
  assert.equal(reasonText("choice_needed:item"), op.unknownReason);
  assert.equal(reasonText("", "choice_needed:item"), op.unknownReason);
  assert.equal(reasonText("reading"), op.refused.reading);
  assert.equal(reasonText("some_new_code"), op.unknownReason);
  assert.equal(reasonText("already_sent"), op.refused.already);
  assert.equal(reasonText("", "Traceback (most recent call last)"), op.unknownReason);
  assert.equal(reasonText("", "ใบกำกับภาษีไม่ครบ"), "ใบกำกับภาษีไม่ครบ");
  assert.equal(isRawCode("OPERATOR_X"), true);
  assert.equal(isRawCode("Choice needed"), false);
  // 三语都有：每个键这种语言都有字，且不含旧词（录、代录、批次、照录、挂起、转人工、读回 / คีย์ / batch、on hold、read-back）
  const all = words(op);
  assert.ok(all.length > 100, String(all.length));
  assert.ok(all.every((w) => w.trim().length > 0));
  const banned = lang === "zh" ? /(?<!登)录|批次|挂起|转人工|读回/ : lang === "th" ? /คีย์|ชุดบันทึก|บันทึกแทน/ : /\bbatch|on hold|read-?back|auto entry|re-?enter/i;
  for (const w of all) assert.doesNotMatch(w, banned, w);
  // 「显示 Express 窗口」去掉了（Express 是任务栏里的真实窗口，Claude 没有这种按钮）：三种语言都没有这个词条
  assert.equal("showExpress" in op, false);
  for (const w of all) assert.doesNotMatch(w, /显示 Express 窗口|Show Express|แสดงหน้าต่าง Express/, w);
  // 定稿用词（中文）
  if (lang === "zh") {
    assert.equal(op.direct, "直写");
    assert.equal(op.handsOn, "实操");
    assert.equal(op.newTask, "新建任务");
    assert.equal(op.tasks, "任务");
    assert.equal(op.placeholder, "这次要处理哪些单据？");
    assert.deepEqual(op.state, { preparing: "准备中", running: "进行中", attention: "需要你处理", done: "已完成" });
    assert.deepEqual(op.section, { files: "资料", confirm: "开始前确认", handle: "需要处理", computer: "电脑与 Express", report: "报告" });
    assert.deepEqual(op.group, { purchase: "采购", sales: "销售", expense: "费用", payment: "收付款", skip: "不入账" });
    assert.deepEqual([op.start, op.stop, op.retry, op.skip, op.exportReport], ["开始", "停止", "重试", "跳过", "导出报告"]);
    assert.equal(op.finish(3, 1, "41 秒"), "完成 3 张，1 张需要处理 · 用时 41 秒");
    assert.deepEqual([op.doc.saved, op.doc.notDone, op.doc.waiting, op.doc.entering], ["已入账", "未完成", "等待", "进行中"]);
    assert.deepEqual([op.expressLogin, op.loginSet, op.loginUnset, op.setUp, op.change], ["Express 登录", "已设置", "未设置", "设置", "更换"]);
    assert.equal(op.deleteTask, "删除任务？");
    assert.equal(op.queued, "等电脑空闲");
  }
  if (lang === "th") {
    assert.equal(op.direct, "บันทึกตรง");
    assert.equal(op.directHint, "บันทึกเข้าบัญชี Express โดยตรง");
    assert.equal(op.handsOn, "ทำใน Express");
    assert.equal(op.handsOnHint, "กรอกทีละใบในหน้าต่าง Express");
  }
  if (lang === "en") {
    assert.deepEqual([op.direct, op.directHint, op.handsOn, op.handsOnHint],
      ["Direct", "Write straight into the Express books", "Hands-on", "Fill in each document in the Express window"]);
  }

  // 公司卡片「记住的选择」：公司规则的资料分组（kind group，只能删）有自己的小节标题
  assert.equal(choiceSection({ source: "rule", kind: "group" }), "group");
  assert.equal(co.kind.group, { zh: "资料分组", en: "Document groups", th: "กลุ่มเอกสาร" }[lang]);

  // 资料分组：站点给 group 用它；没给按 kind；不入账的不交；跳过的不交
  assert.equal(groupOf({ group: "expense", kind: "purchase" }), "expense");
  assert.equal(groupOf({ kind: "sales" }), "sales");
  assert.equal(groupOf({ kind: "" }), "purchase");
  const src = createMockSource({ now: () => 1_800_000_000 });
  const page = await src.documents("C-001", "2026-10", 0, 50);
  assert.equal(page.documents.length, 6);
  assert.deepEqual(groupDocuments(page.documents).map(([group, rows]) => [group, rows.length]),
    [["purchase", 2], ["sales", 2], ["expense", 1], ["skip", 1]]);
  assert.deepEqual(groupDocuments(page.documents, true).map(([group]) => group), ["purchase", "sales", "expense", "payment", "skip"]);
  assert.deepEqual(docsToStart(page.documents, ["D-103"]), ["D-101", "D-102", "D-104-X", "D-105"]);

  // 开始前确认：同一类同一对象几张单只问一次；按类排（商品、供应商 / 客户……）；答完才能开始
  const two = await src.documents("C-002", "2026-10", 0, 50);
  const prechecks = await src.precheck("C-002", two.documents.map((d) => d.id));
  const questions = questionsOf([...prechecks, { doc_id: "X", state: "choose", needed: [prechecks[0]!.needed![0]!] }]);
  assert.deepEqual(questions.map((q) => q.kind), ["item", "party", "account"]);
  assert.deepEqual(questions[0]!.doc_ids, ["D-201", "X"]);
  assert.equal(kindTitle("party", ["purchase"]), op.kind.supplier);
  assert.equal(kindTitle("party", ["sales"]), op.kind.customer);
  assert.equal(kindTitle("party", ["sales", "purchase"]), op.kind.party);
  assert.equal(kindTitle("nonsense"), op.kind.other);
  assert.equal(optionLabel("item", { code: NEW_CODE, name: "" }), op.option.newItem);
  assert.equal(optionLabel("party", { code: NEW_CODE, name: "" }), op.option.new);
  assert.equal(optionLabel("payment", { code: "cash", name: "" }), co.payCash);
  assert.equal(optionLabel("payment", { code: "credit", name: "" }), co.payCredit);
  assert.equal(optionLabel("account", { code: "53-02", name: "ค่าน้ำมัน" }), "53-02 ค่าน้ำมัน");
  let answers: Record<string, string> = {};
  const pcs = await src.computers("C-002");
  assert.equal(initialComputer(pcs), "PC-1");
  const pc = pcs.find((p) => p.id === "PC-1")!;
  assert.equal(allAnswered(questions, answers), false);
  assert.equal(canStart({ docs: 2, questions, answers, computer: { ...pc, login: true } }), false);   // 没答完
  for (const q of questions) answers = { ...answers, [q.key]: q.options[0]!.code };
  assert.equal(allAnswered(questions, answers), true);
  assert.equal(canStart({ docs: 2, questions, answers, computer: pc }), false);                       // Express 登录未设置
  assert.equal(canStart({ docs: 2, questions, answers, computer: { ...pc, login: true } }), true);
  assert.equal(canStart({ docs: 2, questions, answers, computer: { ...pc, login: null } }), true);    // 这一页不知道的不拦
  assert.equal(canStart({ docs: 0, questions, answers, computer: { ...pc, login: true } }), false);
  assert.equal(canStart({ docs: 2, questions, answers, computer: { ...pc, online: false, login: true } }), false);
  const given = answersFor(questions, answers);
  assert.deepEqual(given["D-201"]!.map((a) => a.kind), ["item", "party"]);
  assert.deepEqual(given["X"]!.map((a) => [a.kind, a.value]), [["item", NEW_CODE]]);
  assert.equal(given["D-201"]![0]!.label, "น้ำแข็งหลอด 20 กก.");
  const draft = { id: "T-9", company: "C-002", company_title: "T", period: "2026-10", prompt: "p", created: 1, computer: "",
    answers, skipped: ["D-202"] };
  const input = draftStartInput(draft, two.documents, questions, "PC-1");
  assert.deepEqual(input.doc_ids, ["D-201"]);
  assert.equal(input.computer, "PC-1");
  assert.equal(input.start_at, 0);
  assert.ok(input.answers["D-201"]);
  assert.equal(input.task, "T-9");                         // 批次号就用任务号：交单结论写进站点的任务
  assert.deepEqual(input.groups, { "D-201": "purchase" });
  // 「开始」一直能点：还不能开始时说还差什么（开始前确认还有几项——结果不明的单也算——、没有要处理的单、电脑离线、登录未设置）
  const ok = { ...pc, login: true };
  assert.equal(startBlocker({ docs: 2, questions, answers, computer: ok }), null);
  assert.deepEqual(startBlocker({ docs: 0, questions, answers, computer: ok }), { text: op.noDocs, section: "files" });
  assert.deepEqual(startBlocker({ docs: 2, questions, answers: {}, computer: ok }), { text: op.confirmLeft(questions.length), section: "confirm" });
  assert.deepEqual(startBlocker({ docs: 2, questions, answers, unknown: 1, computer: ok }), { text: op.confirmLeft(1), section: "confirm" });
  assert.equal(startBlocker({ docs: 2, questions, answers, computer: { ...ok, online: false } })!.section, "computer");
  assert.ok(startBlocker({ docs: 2, questions, answers, computer: { ...ok, online: false } })!.text.includes(op.offline));
  assert.deepEqual(startBlocker({ docs: 2, questions, answers, computer: pc }), { text: op.reason.login_missing, section: "computer" });
  assert.equal(canStart({ docs: 2, questions, answers, unknown: 1, computer: ok }), false);
  // 上次推送结果不明的单：在开始前确认里标已入账 / 没有入账，不进「需要处理」；站点给的那一句照用
  const c3 = await src.precheck("C-003", ["D-301", "D-302"]);
  assert.deepEqual(unknownDocs(c3), [{ doc_id: "D-301", message: "ผลการส่งครั้งก่อนไม่ชัดเจน ตรวจสอบใน Express" }]);
  assert.deepEqual(stopped2(c3), []);
  await src.resolve("C-003", "D-301", true);
  assert.deepEqual(src.calls.at(-1), ["resolve", "C-003", "D-301", true]);
  assert.deepEqual(unknownDocs(await src.precheck("C-003", ["D-301"])), []);
  // 预检停下的单（识别中）：原因换成文案；跳过的不再列
  const c1 = await src.precheck("C-001", ["D-101", "D-105"]);
  assert.deepEqual(stoppedDocs(c1), [{ doc_id: "D-105", reason: op.refused.reading }]);
  assert.deepEqual(stoppedDocs(c1, ["D-105"]), []);

  // 建任务、推进、停下等人、重试、跳过
  const { id } = await src.createBatch({ company: "C-001", company_title: "Pearnly", period: "2026-10",
    doc_ids: ["D-101", "D-104-X", "D-103"], computer: "PC-1", start_at: 0, answers: {} });
  let s = await src.snapshot(id);
  assert.equal(s.status, "running");
  assert.equal(taskTone(s), "running");
  assert.equal(taskMeta(s), `${op.state.running} 0 / 3`);
  assert.deepEqual(docRow(s.docs[0]!), { tone: "running", state: op.doc.entering, detail: "" });
  assert.deepEqual(docRow(s.docs[1]!), { tone: "waiting", state: op.doc.waiting, detail: "" });
  src.step(id);
  src.step(id);
  src.step(id);
  s = await src.snapshot(id);
  assert.equal(s.status, "finished");
  const saved = docRow(s.docs[0]!);
  assert.equal(saved.state, op.doc.saved);
  assert.match(saved.detail, new RegExp(`^RR\\d+ · ${op.match}$`));
  const held = docRow(s.docs[1]!);
  assert.equal(held.tone, "attention");
  assert.equal(held.state, op.doc.notDone);
  assert.ok(held.detail.startsWith(op.reason.negative_stock!), held.detail);
  assert.ok(held.detail.includes(op.voidedNo(s.docs[1]!.voided[0]!)), held.detail);
  assert.equal(needsYou(s.docs[1]!), true);
  assert.equal(taskTone(s), "attention");                 // 做完了还有要你处理的：橙
  assert.equal(taskMeta(s), op.state.attention);
  assert.equal(deletable(s), true);
  assert.equal(finishLine(s), op.finish(2, 1, duration(s.elapsed)));
  const csv = reportCsv(s);
  assert.ok(csv.startsWith("\ufeff" + op.column.ref));
  assert.ok(csv.includes(op.doc.notDone!) && csv.includes(op.reason.negative_stock!));
  assert.doesNotMatch(csv, /negative_stock|\[OPERATOR/);
  await src.retry(id, "D-104-X");
  s = await src.snapshot(id);
  assert.equal(s.status, "running");
  src.step(id);
  s = await src.snapshot(id);
  assert.equal(s.status, "finished");
  assert.equal(taskTone(s), "done");
  assert.equal(taskMeta(s), op.state.done);
  assert.deepEqual(src.calls.filter(([m]) => m === "retry"), [["retry", id, "D-104-X"]]);
  // 整个任务停在 Express 登录：需要你处理，不能删
  const stopped = (await src.listBatches()).find((b) => b.reason === "login_missing")!;
  assert.equal(batchNeedsHuman(stopped), true);
  assert.equal(taskTone(stopped), "attention");
  // 授权好了整批继续：没录成的交回去接着录
  const elsewhere = createMockSource({ now: () => 1_800_000_000 });
  const resumed = (await elsewhere.listBatches()).find((b) => b.reason === "login_missing")!;
  await elsewhere.resume(resumed.id);
  assert.deepEqual(elsewhere.calls.at(-1), ["task_resume", resumed.id]);
  assert.equal((await elsewhere.snapshot(resumed.id)).status, "running");
  // 排队：交出去了、电脑还没开始，或账套正被别的电脑写
  assert.equal(taskMeta({ status: "ready", reason: "", docs: [], total: 1, remote: true }), op.queued);
  assert.equal(taskMeta({ status: "paused", reason: "account_busy", docs: [], total: 1 }), op.queued);
  assert.equal(taskMeta({ status: "ready", reason: "", docs: [], total: 1 }), op.state.preparing);
  // 被同一份账套里一张结果不明的单挡着（站点给的「单号 · 对方」，ADR-021 11-02 修订）：任务状态与这张单那一行都写出是哪张，
  // 不只说等电脑空闲；用现有词条（结果不明），不加新文案
  const waitingDoc = { doc_id: "D-2", doc_type: "", status: "pending", docnum: "", voided: [], placeholders: [], reason: "", reports: [],
    seconds: 0, blocked: "INV-7 · ABC" } as any;
  const blockedLine = `INV-7 · ABC · ${co.ledger.code.result_unknown}`;
  assert.equal(taskMeta({ status: "ready", reason: "", docs: [waitingDoc], total: 1, remote: true }), blockedLine);
  assert.deepEqual(docRow(waitingDoc), { tone: "waiting", state: op.doc.waiting, detail: blockedLine });
  assert.equal(taskMeta({ status: "ready", reason: "", docs: [{ ...waitingDoc, blocked: undefined }], total: 1, remote: true }), op.queued);
  // 对话框里的工作状态（同对话的状态行）：在录 = 计时 · 这张单；排队、暂停后等着自己恢复 = 计时 · 等电脑空闲；
  // 停下等人 = 需要您的输入；整个任务因必须人工的原因停了 = 错误框；完成、用户自己停的 = 不显示
  const act = { reason: "", total: 2, current: "", started: 100, created: 90 };
  const doc = { doc_id: "D-1", doc_type: "", status: "entering", docnum: "", voided: [], placeholders: [], reason: "", reports: [], seconds: 0,
    summary: { ref: "INV-9", party: "ABC" } } as any;
  assert.deepEqual(batchActivity({ ...act, status: "running", current: "D-1", docs: [doc] }), { kind: "tool", since: 100, step: "INV-9 · ABC" });
  assert.deepEqual(batchActivity({ ...act, status: "running", docs: [doc] }), { kind: "tool", since: 100, step: "" });
  assert.deepEqual(batchActivity({ ...act, status: "paused", reason: "user_active", docs: [] }), { kind: "queued", since: 100, step: op.queued });
  assert.deepEqual(batchActivity({ ...act, status: "ready", started: 0, docs: [], remote: true }), { kind: "queued", since: 90, step: op.queued });
  assert.deepEqual(batchActivity({ ...act, status: "paused", reason: "unknown_screen", docs: [] }), { kind: "input" });
  assert.deepEqual(batchActivity({ ...act, status: "stopped", reason: "license", docs: [] }), { kind: "error", text: op.reason.license });
  assert.deepEqual(batchActivity({ ...act, status: "stopped", docs: [] }), { kind: "hidden" });
  assert.deepEqual(batchActivity({ ...act, status: "finished", docs: [] }), { kind: "hidden" });
  assert.deepEqual(batchActivity(null), { kind: "hidden" });
  assert.equal(batchStatusText({ kind: "tool", since: 0, step: "INV-9 · ABC" }, 12.7), `${steps.elapsed(12)} · INV-9 · ABC`);
  assert.equal(batchStatusText({ kind: "tool", since: 0, step: "" }, 5), steps.elapsed(5));
  assert.equal(deletable({ status: "running" }), false);

  // 侧栏：任务（站点的任务记录）按公司分组，同对话侧栏的行：只有标题（改过的名字优先，否则「公司 · 月份」），没有第二行、状态点
  const made = await src.createTask({ company: "C-003", company_title: "ทองไทย เทรดดิ้ง จำกัด", period: "2026-10" });
  assert.equal(made.status, "preparing");
  const recs = (await src.listTasks(0, 50)).tasks;
  assert.equal(recs[0]!.name, made.name);                  // 新的在前
  const side = taskSidebar(recs, { [made.name]: true });
  assert.deepEqual(side.projects.map((p) => p.id).sort(), ["C-001", "C-002", "C-003"]);
  for (const row of side.rows) {
    assert.deepEqual(Object.keys(row).sort(), ["created", "group", "index", "key", "period", "starred", "time", "title"]);
    assert.equal(row.group, recs[row.index]!.company);
    assert.equal(row.key, recs[row.index]!.name);
  }
  assert.equal(side.rows[0]!.starred, true);                // 置顶请求还没回：先按点的样子摆
  assert.equal(titleOf({ title: "", company: "C-1", company_title: "Siam", period: "2026-10" }), taskTitle("Siam", "2026-10"));
  assert.equal(titleOf({ title: " Oct close ", company: "C-1", company_title: "Siam", period: "2026-10" }), "Oct close");
  await src.updateTask(made.name, { title: "Oct close", starred: true });
  const renamed = (await src.listTasks(0, 50)).tasks.find((t) => t.name === made.name)!;
  assert.deepEqual([renamed.title, renamed.starred], ["Oct close", true]);
  // 交出去还没收尾的（等电脑空闲、进行中——这台电脑上在跑的按桥上的）不能删
  assert.equal(canDelete({ status: "queued" }), false);
  assert.equal(canDelete({ status: "running" }), false);
  assert.equal(canDelete({ status: "preparing" }), true);
  assert.equal(canDelete({ status: "finished" }), true);
  assert.equal(canDelete({ status: "attention" }, "running"), false);
  // 行尾只有对话行那两种标记：在跑脉冲点；结束了、这台设备还没看过蓝点（第一次用之前结束的不算）；正开着的不标
  const ended = { name: "X", status: "finished" as const, finished_at: "2026-10-27 10:00:00", modified: "2026-10-27 10:00:00" };
  const base = Math.floor(Date.parse("2026-10-27T09:00:00") / 1000);
  assert.equal(taskActivity({ ...ended, status: "running" }, undefined, { base, seen: {} }, false), "running");
  assert.equal(taskActivity({ ...ended, status: "queued" }, "running", { base, seen: {} }, false), "running");
  assert.equal(taskActivity(ended, undefined, { base, seen: {} }, false), "unread");
  assert.equal(taskActivity(ended, undefined, { base, seen: { X: "finished" } }, false), undefined);
  assert.equal(taskActivity(ended, undefined, { base: base + 7200, seen: {} }, false), undefined);
  assert.equal(taskActivity(ended, undefined, { base, seen: {} }, true), undefined);
  assert.equal(taskActivity({ ...ended, status: "preparing" }, undefined, { base, seen: {} }, false), undefined);
  // 手排顺序：实操是另一份，任务号不进对话的手排顺序，同步进服务端偏好的 operator 份（order/ 开头的项）
  taskOrder.setPinnedOrder([made.name]);
  taskOrder.setCompanyOrder(["C-003"]);
  assert.ok(!useSidebarOrder.getState().pinned.includes(made.name));
  assert.ok(!useSidebarOrder.getState().companies.includes("C-003"));
  const prefs = collectPrefs();
  assert.ok(!JSON.stringify(prefs.order).includes(made.name));
  assert.deepEqual(prefs.operator!["order/pinned"], [made.name]);
  assert.deepEqual(prefs.operator!["order/companies"], ["C-003"]);
  assert.ok("cards/folds" in prefs.operator!);
  // 删除只删记录：录单中的不能删
  const run2 = await src.createBatch({ company: "C-002", period: "2026-10", doc_ids: ["D-201"], computer: "PC-1", start_at: 0, answers: {} });
  await assert.rejects(src.remove(run2.id));
  await src.remove(id);
  assert.ok(!(await src.listBatches()).some((b) => b.id === id));

  // 时长
  assert.equal(duration(40), op.dur.s(40));
  assert.equal(duration(200), op.dur.m(3, 20));
  assert.equal(duration(3900), op.dur.h(1, 5));
  g.done();
})().catch((e) => { console.error(e); process.exit(1); });
"""

LIVE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createLiveSource, mergedSnapshot, POLL_MS, SENT_STATES, startedSnapshot, taskOf, taskSnapshot, withBlocked } from "@/components/operator/live-source";
import { useProjects } from "@/lib/managed-agents/projects";
import { taskMeta } from "@/components/operator/model";
import { reasonText } from "@/components/operator/model";
import { modeForKey, noteViewMode, openOperator, ownMode, switchMode, useModePlace } from "@/lib/nav";
import { op } from "@/i18n";

const g = globalThis as any;
const gets: Array<[string, any]> = [];
const posts: Array<[string, any]> = [];
const uploads: Array<[string, any, string]> = [];
const DOCS = [
  { doc_id: "P:PO-1", kind: "purchase", date: "2026-10-03", party: "Thai Steel", ref: "SN-1", total: "10700.00", stopped: "" },
  { doc_id: "P:PO-2", kind: "purchase", group: "expense", date: "2026-10-04", party: "Fuel", ref: "SN-2", total: "700.00", stopped: "" },
  { doc_id: "S:SO-1", kind: "sales", date: "2026-10-05", party: "Korat", ref: "IV-9", total: "963.00", stopped: "needs_choice" },
];
const get = async (method: string, args: any) => {
  gets.push([method, args]);
  if (method === "pearnly_erp_portal.masters.cc_companies.companies") {
    const rows = [{ id: "CO-00001", title: "Siam Trading", tax_id: "0105536082808" }, { id: "CO-00002", title: "CO-00002" },
      { id: "CO-00003", title: "Korat" }];
    return rows.slice(args.start, args.start + args.limit);
  }
  if (method === "pearnly_cowork.companies.api.companies") return [{ id: "C-1", title: "Siam", tax_id: "0105" }];
  if (method.endsWith("express_operator.api.status")) {
    if (args.company === "OFF") throw new Error("offline");   // 断网重启：问不到站点
    return { documents: JSON.parse(args.doc_ids).map((id: string) => ({ doc_id: id, state: id === "z2" ? "cancelled" : "saved", docnum: "RR0" + id, ref: "INV-" + id, party: "ร้าน " + id })) };
  }
  if (method.endsWith("express_operator.api.task_list")) return { tasks: [{ ...TASK("R1"), starred: 1 }], next: 50 };
  if (method.endsWith("express_operator.api.task_get")) return TASK(args.name);
  if (method.endsWith("express_operator.api.documents")) {
    const rows = DOCS.slice(args.offset, args.offset + args.limit);
    return { documents: rows, next_offset: args.offset + args.limit < DOCS.length ? args.offset + args.limit : null, total: DOCS.length };
  }
  throw new Error("unexpected " + method);
};
/** 站点的任务记录（R1：网页上看、在别的电脑上等着的任务）。 */
const TASK = (name: string, status = "queued") => ({ name, product: "erp", company: "CO-00001", company_title: "Siam Trading", period: "2026-10",
  title: "", starred: 0, status, computer: "", start_at: "", started_at: "2026-10-27 09:00:00", finished_at: "", creation: "2026-10-27 08:59:00",
  modified: "2026-10-27 09:00:00",
  documents: [{ doc_id: "P:PO-1", doc_group: "purchase", state: "queued", docnum: "", voided_docnum: "", reason: "", ref: "SN-1", party: "Thai Steel" }] });
let resolvedRs = false;
const post = async (method: string, args: any) => {
  posts.push([method, args]);
  if (method.endsWith(".task_create")) return { ...TASK("T1", "preparing"), company: args.company, company_title: args.company_title, period: args.period, documents: [] };
  if (method.endsWith(".task_update")) return { ...TASK(args.name, "preparing"), title: args.title ?? "", starred: args.starred ?? 0 };
  if (method.endsWith(".task_running")) return TASK(args.name, "running");
  if (method.endsWith(".task_delete")) {
    if (args.name === "b9") throw new Error("Invalid request");   // 交出去还没收尾的：站点不让删
    return { deleted: args.name };
  }
  if (method.endsWith(".task_settle")) {
    if (args.name === "off") throw new Error("offline");             // 断网：问不到站点
    if (args.name === "R1") return { ...TASK("R1", "attention"), finished_at: "2026-10-27 09:30:00", documents: [
      { doc_id: "P:PO-1", doc_group: "purchase", state: "saved", docnum: "RR0101", voided_docnum: "", reason: "", ref: "SN-1", party: "Thai Steel" },
      { doc_id: "S:SO-1", doc_group: "sales", state: "not_done", docnum: "", voided_docnum: "IV0009", reason: "negative_stock", ref: "IV-9", party: "Korat" }] };
    // CC 重启时正录到一半的 rs1：收拾时作废了表头，站点定成结果不明（未完成），单号、对方按站点回的写
    if (args.name === "rs") return { ...TASK("rs", "attention"), documents: [{ doc_id: "rs1", doc_group: "purchase", state: "not_done",
      docnum: "", voided_docnum: "", reason: resolvedRs ? "confirmed_not_entered" : "push.result_unknown", ref: "TT-1", party: "ร้าน rs" }] };
    // CC 重启后留下的 z：站点问产品，z2 撤回了，其余已入账（单号、对方按站点回的写）
    return { ...TASK(args.name, "stopped"), documents: ["z1", "z2", "z3"].map((id) => ({ doc_id: id, doc_group: "purchase",
      state: id === "z2" ? "cancelled" : "saved", docnum: "RR0" + id, voided_docnum: "", reason: "", ref: "INV-" + id, party: "ร้าน " + id })) };
  }
  if (method.endsWith(".resolve")) {
    if (args.doc_id === "rs1") resolvedRs = true;
    return { ok: args.doc_id !== "S:SO-1" };
  }
  if (method.endsWith(".precheck")) {
    return { documents: JSON.parse(args.doc_ids).map((id: string) => id === "S:SO-1"
      ? { doc_id: id, state: "choose", needed: [{ kind: "party", subject: "K", label: "Korat", options: [{ code: "__new__", name: "" }] }] }
      : { doc_id: id, state: "ready" }) };
  }
  if (method.endsWith(".regroup")) return { ok: true };
  if (method.endsWith(".task_answer") || method.endsWith(".task_resume")) return TASK(args.name, "queued");
  return { batch: "b1", results: [
    { doc_id: "P:PO-1", status: "sent", reason: "" },
    { doc_id: "P:PO-2", status: "queued", reason: "" },
    { doc_id: "S:SO-1", status: "needs_choice", reason: "" },
  ] };
};
const upload = async (method: string, args: any, file: File) => {
  uploads.push([method, args, file.name]);
  return { ok: true };
};

/** 假桥：features 带不带 operator、operator_state 回什么由测试改。 */
function fakeBridge(features: string[]) {
  const calls: Array<[string, any]> = [];
  const state = { batches: [] as any[], login: false };
  const api = {
    state: async () => ({ features }),
    call: async (method: string, args: any = {}) => {
      calls.push([method, args]);
      if (method === "operator_state") return { ok: true, batches: JSON.parse(JSON.stringify(state.batches)), login: state.login };
      if (method === "operator_control") {
        const batch = state.batches.find((b) => b.id === args.batch);
        if (!batch) return { ok: false };
        if (args.action === "delete") {
          if (batch.status === "running") return { ok: false };
          state.batches = state.batches.filter((b) => b.id !== args.batch);
        }
        if (args.action === "settle") {
          if (batch.live !== false) return { ok: false };
          for (const d of batch.docs) {
            const r = args.results[d.doc_id];
            if (r?.state === "saved") Object.assign(d, { status: "saved", docnum: r.docnum });
            if (r?.state === "cancelled") Object.assign(d, { status: "skipped", reason: "stopped" });
          }
          batch.status = batch.docs.some((d: any) => d.status === "skipped") ? "stopped" : "finished";
        }
        return { ok: true };
      }
      if (method === "operator_authorize") { state.login = true; return { ok: true }; }
      return { ok: false };
    },
    onChanged: () => () => {},
  };
  return { api, calls, state };
}

function runner(id: string, status: string, docs: any[], created = 1_800_000_000): any {
  const counts: any = {};
  for (const d of docs) counts[d.status] = (counts[d.status] ?? 0) + 1;
  return { id, status, reason: "", company: "SIAM TRADING", account_set: "C:\\EXP\\A", total: docs.length, counts, created,
    current: docs.find((d) => d.status === "entering")?.doc_id ?? "", index: 0, events: [], pauses: [], started: created,
    finished: 0, elapsed: 30, remaining_estimate: 60,
    docs: docs.map((d) => ({ docnum: "", voided: [], placeholders: [], reason: "", reports: [], issues: [], seconds: 0, evidence: "none", ...d })) };
}

(async () => {
  // ── 网页 / 手机（没有桥）：公司走产品声明的公司接口（ERP 声明 masters.cc_companies，同侧栏「公司」，服务端分页、搜），
  //    资料、开始前确认、改分组、放资料走站点接口 ─────────────────
  useProjects.setState({ loaded: true, kind: "company", api: "pearnly_erp_portal.masters.cc_companies.", sections: ["pending", "notes", "desk"] });
  const web = createLiveSource({ product: "erp", bridge: async () => null, get, post, upload, now: () => 1_800_000_100 });
  const companies = await web.companies("si", 1, 2);
  assert.deepEqual(companies, [{ id: "CO-00002", title: "CO-00002", tax_id: undefined }, { id: "CO-00003", title: "Korat", tax_id: undefined }]);
  assert.deepEqual(gets[0], ["pearnly_erp_portal.masters.cc_companies.companies", { search: "si", start: 1, limit: 2 }]);
  const first = await web.documents("CO-00001", "2026-10", 0, 2);
  assert.deepEqual(gets[1], ["pearnly_core.express_operator.api.documents", { company: "CO-00001", period: "2026-10", offset: 0, limit: 2 }]);
  assert.equal(first.next, 2);
  assert.deepEqual(first.documents.map((d) => d.group), ["purchase", "expense"]);   // 没给 group 按 kind；给了用它
  const rest = await web.documents("CO-00001", "2026-10", 2, 2);
  assert.equal(rest.next, null);
  assert.equal(rest.documents[0]!.group, "sales");
  const ids = [...first.documents, ...rest.documents].map((d) => d.id);
  const checked = await web.precheck("CO-00001", ids);
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.precheck", { company: "CO-00001", doc_ids: JSON.stringify(ids) }]);
  assert.equal(checked.find((d) => d.doc_id === "S:SO-1")!.state, "choose");
  await web.regroup("CO-00001", "P:PO-1", "expense");
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.regroup", { company: "CO-00001", doc_id: "P:PO-1", group: "expense" }]);
  const file = new File(["x"], "bill.pdf", { type: "application/pdf" });
  await web.intake("CO-00001", "2026-10", [file]);
  assert.equal(uploads.length, 1);
  assert.equal(uploads[0]![0], "pearnly_core.express_operator.api.intake");
  assert.equal(uploads[0]![1].company, "CO-00001");
  assert.match(uploads[0]![1].request_key, /^[0-9a-f-]{36}$/);  // 每次上传一个新请求号（站点按它存原件、交识别）
  assert.equal(uploads[0]![2], "bill.pdf");
  // 电脑只有「本机」；没有桥时 Express 登录不知道（不拦开始）；没有「显示 Express 窗口」可点
  const pcs = await web.computers("CO-00001");
  assert.deepEqual(pcs.map((pc) => [pc.id, pc.name, pc.login, pc.local]), [["", op.thisComputer, null, false]]);
  assert.equal("showExpress" in web, false);
  // 开始：doc_ids、回答都是 JSON；建好先拼「准备中」，没收的停在需要处理、原因按回包、只能跳过
  const seen: any[] = [];
  const off = web.subscribe((s) => seen.push(s));
  await g.sleep(0);
  assert.equal(web.polling, false);                       // 没有桥：不定时读
  const answers = { "S:SO-1": [{ kind: "party", subject: "K", value: "__new__", label: "Korat" }] };
  const { id } = await web.createBatch({ company: "CO-00001", company_title: "Siam Trading", period: "2026-10",
    doc_ids: ids, computer: "", start_at: 0, answers });
  assert.equal(id, "b1");
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.start",
    { company: "CO-00001", doc_ids: JSON.stringify(ids), computer: "", start_at: 0, answers: JSON.stringify(answers), task: "", groups: "{}" }]);
  let s = await web.snapshot("b1");
  assert.equal(s.remote, true);
  assert.equal(s.status, "ready");
  assert.equal(s.period, "2026-10");
  assert.equal(s.company_title, "Siam Trading");
  assert.deepEqual(s.docs.map((d) => [d.doc_id, d.status, d.reason, !!d.refused]),
    [["P:PO-1", "pending", "", false], ["P:PO-2", "pending", "", false], ["S:SO-1", "waiting", "needs_choice", true]]);
  assert.equal(s.docs[1]!.summary!.group, "expense");
  assert.equal(reasonText(s.docs[2]!.reason), op.refused.needs_choice);
  assert.equal(seen.at(-1).id, "b1");
  await web.skip("b1", "S:SO-1");
  s = await web.snapshot("b1");
  assert.equal(s.docs[2]!.status, "skipped");
  // 网页上：停止没有对象（进度在执行的电脑上）；单张重试、整批继续交给站点按原批次号交回执行的电脑（同作答，没有答案）
  await assert.rejects(web.stop("b1"));
  await web.retry("b1", "P:PO-1");
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.task_answer", { name: "b1", doc_id: "P:PO-1", answers: "[]" }]);
  await web.resume("b1");
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.task_resume", { name: "b1" }]);
  const later = startedSnapshot({ id: "b2", company: "C", company_title: "", period: "", created: 1, start_at: 9, results: [{ doc_id: "x", status: "sent", reason: "" }], skipped: new Set() }, new Map());
  assert.equal(later.reason, "waiting_start");
  const none = startedSnapshot({ id: "b3", company: "C", company_title: "", period: "", created: 1, start_at: 0, results: [{ doc_id: "x", status: "not_permitted", reason: "" }], skipped: new Set() }, new Map());
  assert.equal(none.status, "finished");
  // 网页上删除：这一页建的直接忘掉
  await web.remove("b1");
  assert.deepEqual(await web.listBatches(), []);
  off();
  assert.deepEqual(await web.authorizeLogin({ computer: "" }), { ok: false });
  assert.ok(SENT_STATES.has("sent") && SENT_STATES.has("queued"));

  // ── 任务（站点的任务记录，跨设备）：列表、建、改名 / 置顶、结果不明的标记 ─────────────────────────────────────
  const page2 = await web.listTasks(0, 50);
  assert.deepEqual(gets.at(-1), ["pearnly_core.express_operator.api.task_list", { start: 0, limit: 50 }]);
  assert.deepEqual([page2.tasks[0]!.name, page2.tasks[0]!.starred, page2.next], ["R1", true, 50]);
  const made = await web.createTask({ company: "CO-00001", company_title: "Siam Trading", period: "2026-10" });
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.task_create", { company: "CO-00001", period: "2026-10", company_title: "Siam Trading" }]);
  assert.equal(made.status, "preparing");
  const changed: string[] = [];
  const offTasks = web.subscribeTasks((t) => changed.push(`${t.name}:${t.status}`));
  await web.updateTask("T1", { title: "Oct", starred: true });
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.task_update", { name: "T1", title: "Oct", starred: 1 }]);
  await web.resolve("CO-00001", "P:PO-2", true);
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.resolve", { company: "CO-00001", doc_id: "P:PO-2", entered: 1, docnum: "" }]);
  await web.resolve("CO-00001", "P:PO-2", false);
  assert.equal(posts.at(-1)![1].entered, 0);
  await assert.rejects(web.resolve("CO-00001", "S:SO-1", true));   // 站点说不是结果不明的单
  // 这台电脑上没在跑、还没收尾的（网页上看别的电脑在做的）：以站点为准收尾一次（task_settle），进度按任务记录里的交单结论
  const remote = await web.snapshot("R1");
  assert.deepEqual(posts.at(-1), ["pearnly_core.express_operator.api.task_settle", { name: "R1" }]);
  assert.equal(remote.remote, true);
  assert.equal(remote.status, "finished");
  assert.deepEqual(remote.docs.map((d) => [d.doc_id, d.status, d.docnum, d.reason]),
    [["P:PO-1", "saved", "RR0101", ""], ["S:SO-1", "waiting", "", "negative_stock"]]);
  assert.deepEqual(remote.docs[1]!.voided, ["IV0009"]);
  assert.equal(remote.docs[0]!.summary!.ref, "SN-1");
  assert.ok(changed.includes("R1:attention") && changed.includes("T1:preparing"), JSON.stringify(changed));
  const settles = posts.filter(([m]) => m.endsWith(".task_settle")).length;
  await web.getTask("R1");
  assert.equal(posts.filter(([m]) => m.endsWith(".task_settle")).length, settles);  // SETTLE_S 内不再问
  offTasks();
  assert.equal(taskSnapshot(taskOf({ ...TASK("Q"), documents: [] })).status, "ready");
  // 站点说排着的单被哪张结果不明的单挡着：只挂在排着的那张上（ADR-021 11-02 修订）
  const held = taskSnapshot(taskOf({ ...TASK("Q"), documents: [
    { doc_id: "D-1", state: "queued", blocked_by: "INV-7 · ABC" }, { doc_id: "D-2", state: "saved", docnum: "RR1", blocked_by: "x" }] }));
  assert.deepEqual(held.docs.map((d) => [d.status, d.blocked]), [["pending", "INV-7 · ABC"], ["saved", undefined]]);
  assert.deepEqual(withBlocked(held, new Map([["D-2", "y"]])).docs.map((d) => d.blocked), ["INV-7 · ABC", undefined]);
  assert.equal(taskOf({ name: "x", status: "weird" }).status, "preparing");

  // ── CC 桌面版窗口（桥支持实操）：Cowork 公司走 Cowork 声明的公司主档接口（同一条路）；任务来自桥（CC 重启后也在，带 created）──
  useProjects.setState({ loaded: true, kind: "company", api: "pearnly_cowork.companies.api.", sections: ["pending", "create"] });
  const fake = fakeBridge(["devices", "operator"]);
  const desk = createLiveSource({ product: "cowork", bridge: async () => fake.api as any, get, post, upload, now: () => 1_800_000_100 });
  assert.deepEqual(await desk.companies("", 0, 50), [{ id: "C-1", title: "Siam", tax_id: "0105" }]);
  assert.deepEqual(gets.at(-1), ["pearnly_cowork.companies.api.companies", { search: undefined, start: 0, limit: 50 }]);
  assert.deepEqual((await desk.computers("C-1")).map((pc) => [pc.login, pc.local]), [[false, true]]);
  assert.deepEqual(await desk.authorizeLogin({ computer: "" }), { ok: true });
  assert.equal((await desk.computers("C-1"))[0]!.login, true);
  // 本机最近的任务：按 created 新的在前（含 CC 重启前已完成的）
  fake.state.batches = [
    runner("old", "finished", [{ doc_id: "9", doc_type: "RR", status: "saved", docnum: "RR009" }], 1_700_000_000),
    runner("b9", "running", [{ doc_id: "77", doc_type: "HS", status: "entering" }], 1_800_000_050),
  ];
  assert.deepEqual((await desk.listBatches()).map((row) => row.id), ["b9", "old"]);
  await desk.documents("C-1", "2026-10", 0, 50);
  await desk.createBatch({ company: "C-1", company_title: "Siam", period: "2026-10", doc_ids: ids, computer: "", start_at: 0, answers: {} });
  fake.state.batches.push(runner("b1", "running", [{ doc_id: "P:PO-1", doc_type: "RR", status: "saved", docnum: "RR001" }, { doc_id: "P:PO-2", doc_type: "RR", status: "entering" }]));
  const got: string[] = [];
  const unsub = desk.subscribe((x) => got.push(`${x.id}:${x.status}:${x.counts.saved ?? 0}`));
  await g.sleep(0);
  assert.equal(desk.polling, true);                       // 有订阅、看得见、桥支持实操：定时读
  await desk.refresh();
  assert.ok(got.includes("b1:running:1"), JSON.stringify(got));
  const count = got.length;
  await desk.refresh();
  assert.equal(got.length, count);                        // 没变的不重复通知
  s = await desk.snapshot("b1");
  assert.equal(s.remote, false);
  assert.equal(s.company_title, "Siam");
  assert.equal(s.period, "2026-10");
  assert.equal(s.docs[0]!.summary!.ref, "SN-1");          // 要点按这一页的资料补
  assert.deepEqual(s.docs.map((d) => d.status), ["saved", "entering", "waiting"]);
  // 控制：停止、开始（暂停后）、跳过、删除交给桥（operator_control）；重试、整批停下后继续交给站点（CC 本机重录不了已经回执给
  // 站点的单），不经桥。页面不再调 operator_show（按钮去掉了），也不取实时画面
  const before = fake.calls.length;
  await desk.stop("b1");
  await desk.start("b1");
  await desk.skip("b1", "P:PO-2");
  await desk.retry("b1", "P:PO-2");
  await desk.resume("b1");
  assert.deepEqual(fake.calls.slice(before).filter(([m]) => m === "operator_control").map(([, a]) => [a.batch, a.action, a.doc]),
    [["b1", "stop", ""], ["b1", "resume", ""], ["b1", "skip", "P:PO-2"]]);
  assert.deepEqual(posts.slice(-2), [["pearnly_core.express_operator.api.task_answer", { name: "b1", doc_id: "P:PO-2", answers: "[]" }],
    ["pearnly_core.express_operator.api.task_resume", { name: "b1" }]]);
  assert.ok(!fake.calls.some(([m]) => m === "operator_show"));
  assert.ok(!fake.calls.some(([m]) => m === "operator_frame"));
  // 桥上看到这台电脑开始录了：站点上转「进行中」，每个任务只一次
  assert.equal(posts.filter(([m, a]) => m.endsWith(".task_running") && a.name === "b1").length, 1);
  assert.ok(posts.some(([m, a]) => m.endsWith(".task_running") && a.name === "b9"));
  // 这台电脑上跑完的：以站点为准收尾一次
  assert.ok(posts.some(([m, a]) => m.endsWith(".task_settle") && a.name === "old"));
  await assert.rejects(desk.remove("b9"));               // 录单中的：站点不让删、桥回 ok:false
  await desk.remove("old");
  assert.ok(posts.some(([m, a]) => m.endsWith(".task_delete") && a.name === "old"));
  assert.ok(fake.calls.some(([m, a]) => m === "operator_control" && a.action === "delete" && a.batch === "old"));
  assert.ok(!(await desk.listBatches()).some((row) => row.id === "old"));
  unsub();
  assert.equal(desk.polling, false);
  assert.ok(POLL_MS >= 1000);
  // CC 重启后留下、这台电脑没在跑的任务：不显示进行中；问站点（以站点为准）后交桥收尾；断网问不到就按排队显示
  fake.state.batches.push({ ...runner("z", "running", [{ doc_id: "z1", doc_type: "RR", status: "saved", docnum: "RR0z1" },
    { doc_id: "z2", doc_type: "RR", status: "entering" }, { doc_id: "z3", doc_type: "RR", status: "pending" }]), live: false, site: "C-1" });
  fake.state.batches.push({ ...runner("off", "running", [{ doc_id: "o1", doc_type: "RR", status: "pending" }]), live: false, site: "OFF" });
  // 录到一半被 CC 重启打断的（收拾时作废、记 cc_restarted）：同样问站点收尾，不一直停在「等电脑空闲」（10-29 测试机）
  fake.state.batches.push({ ...runner("rs", "running", [{ doc_id: "rs1", doc_type: "RR", status: "waiting", reason: "cc_restarted",
    voided: ["RR0042"] }]), live: false, site: "C-1" });
  const restored = await desk.listBatches();
  assert.ok(posts.some(([m, a]) => m.endsWith(".task_settle") && a.name === "rs"));
  assert.ok(fake.calls.some(([m, a]) => m === "operator_control" && a.action === "settle" && a.batch === "rs"
    && a.results.rs1.state === "not_done"));
  assert.equal(restored.find((row) => row.id === "rs")!.status, "finished");
  assert.equal((await desk.snapshot("rs")).docs.find((d) => d.doc_id === "rs1")!.summary!.ref, "TT-1");
  // 结果不明的那张在任务里标「没有入账」：走同一个核对接口，再以站点为准把这个任务收尾一次（不用等下一轮）
  const settlesBefore = posts.filter(([m, a]) => m.endsWith(".task_settle") && a.name === "rs").length;
  await desk.resolve("C-1", "rs1", false, "rs");
  assert.deepEqual(posts.filter(([m]) => m.endsWith(".resolve")).at(-1), ["pearnly_core.express_operator.api.resolve",
    { company: "C-1", doc_id: "rs1", entered: 0, docnum: "" }]);
  assert.equal(posts.filter(([m, a]) => m.endsWith(".task_settle") && a.name === "rs").length, settlesBefore + 1);
  assert.equal(fake.calls.filter(([m, a]) => m === "operator_control" && a.action === "settle" && a.batch === "rs").at(-1)![1]
    .results.rs1.reason, "confirmed_not_entered");
  assert.equal(restored.find((row) => row.id === "z")!.status, "stopped");
  // 以站点的任务记录为准（task_settle），不再逐张问；问不到的旧批次（off）才退回按单问（status）
  assert.ok(posts.some(([m, a]) => m.endsWith(".task_settle") && a.name === "z"));
  assert.ok(!gets.some(([m, a]) => m.endsWith(".status") && a.company === "C-1"));
  assert.ok(gets.some(([m, a]) => m.endsWith(".status") && a.company === "OFF"));
  assert.ok(fake.calls.some(([m, a]) => m === "operator_control" && a.action === "settle" && a.batch === "z" && a.results.z3.state === "saved"));
  // 这一页没有资料清单（CC 重启后）：单据按站点回的单号、对方显示，不是单据编号
  assert.equal((await desk.snapshot("z")).docs.find((d) => d.doc_id === "z3")!.summary!.ref, "INV-z3");
  const offline = restored.find((row) => row.id === "off")!;
  assert.equal(offline.status, "ready");
  assert.equal(taskMeta(offline), op.queued);             // 不是「进行中」
  // 没有 operator 的旧桌面版：当网页用
  const old = createLiveSource({ product: "cowork", bridge: async () => fakeBridge(["devices"]).api as any, get, post, upload });
  assert.equal((await old.computers("C-1"))[0]!.login, null);
  assert.deepEqual(await old.listBatches(), []);
  const bare = mergedSnapshot(runner("b7", "finished", []), undefined, new Map());
  assert.equal(bare.company_title, "SIAM TRADING");
  assert.equal(bare.created, 1_800_000_000);

  // ── 切换与快捷键：Ctrl / ⌘ + Shift + 1 / 2（按键位认），两种模式各记各的位置 ─────────────────────
  const key = (code: string, extra: any = {}) => ({ code, ctrlKey: true, metaKey: false, shiftKey: true, altKey: false, ...extra });
  assert.equal(modeForKey(key("Digit1")), "chat");
  assert.equal(modeForKey(key("Digit2")), "operator");
  assert.equal(modeForKey(key("Digit2", { ctrlKey: false, metaKey: true })), "operator");
  assert.equal(modeForKey(key("Digit2", { shiftKey: false })), null);
  assert.equal(modeForKey(key("Digit3")), null);
  // 模式自己的视图定模式；公司、待处理两种模式共有，不换模式（主会话 11-03）
  assert.equal(ownMode("chat"), "chat");
  assert.equal(ownMode("tasks"), "chat");
  assert.equal(ownMode("operator"), "operator");
  for (const shared of ["pending", "companies", "company"] as const) assert.equal(ownMode(shared), null);
  let view: any = "pending";
  const setView = (v: any) => { noteViewMode(v); view = v; };
  assert.equal(useModePlace.getState().mode, "chat");
  switchMode("operator", view, setView);
  assert.equal(view, "operator");
  assert.equal(useModePlace.getState().mode, "operator");
  useModePlace.setState({ batch: "b1" });
  // 代录模式下点侧栏共有的「公司」：留在代录模式
  setView("companies");
  assert.equal(useModePlace.getState().mode, "operator");
  switchMode("chat", view, setView);
  assert.equal(view, "pending");
  assert.equal(useModePlace.getState().mode, "chat");
  // 回到代录：回到它最后看的那一页（公司页），批次还记着
  switchMode("operator", view, setView);
  assert.equal(view, "companies");
  assert.equal(useModePlace.getState().batch, "b1");
  // 待处理页的「任务」一组从对话模式打开任务：记下对话视图，切到代录的那个任务
  switchMode("chat", view, setView);
  setView("pending");
  openOperator("b2", view, setView);
  assert.equal(view, "operator");
  assert.equal(useModePlace.getState().mode, "operator");
  assert.equal(useModePlace.getState().batch, "b2");
  assert.equal(useModePlace.getState().chatView, "pending");
  g.done();
})().catch((e) => { console.error(e); process.exit(1); });
"""

RENDER = r"""
import "cc-test-setup";
import "cc-dom-setup";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server.browser";
import { createElement as h, type ReactNode } from "react";
import { createMockSource } from "@/components/operator/mock-source";
import { ModeSwitch } from "@/components/operator/mode-switch";
import { draftOf, type Operator, OperatorValue } from "@/components/operator/controller";
import { OperatorPanel, useHandleItems, useUnknownItems } from "@/components/operator/panel";
import { OperatorDock, OperatorMain, OperatorTools } from "@/components/operator/workspace";
import { OperatorSidebar } from "@/components/operator/operator-sidebar";
import { finishLine, questionsOf } from "@/components/operator/model";
import { useTasks } from "@/components/operator/tasks";
import { setPrep } from "@/components/operator/store";
import { SidebarProvider } from "@/components/ui/sidebar";
import { useModePlace } from "@/lib/nav";
import { op, steps, t } from "@/i18n";

const g = globalThis as any;
const calls: string[] = [];
const noop = () => {};
function value(patch: Partial<Operator>): Operator {
  return {
    source: createMockSource({ now: () => 1_800_000_000 }), id: "", task: null, draft: null,
    data: { docs: null, next: null, prechecks: null, computers: null }, questions: [], ready: false, snapshot: null, busy: false, error: "",
    notice: "", computer: undefined, select: noop, create: noop, retarget: noop, say: noop, answer: noop, chooseComputer: noop, regroup: noop,
    intake: noop, loadMore: noop, resolve: (id, entered) => calls.push(`resolve:${id}:${entered}`),
    start: () => calls.push("start"), stop: () => calls.push("stop"), skip: (id) => calls.push(`skip:${id}`), retry: (id) => calls.push(`retry:${id}`),
    resume: () => calls.push("resume"), authorize: () => calls.push("authorize"), remove: async () => {},
    ...patch,
  };
}
const render = (v: Operator, node: ReactNode) =>
  renderToStaticMarkup(h(SidebarProvider, { open: true, onOpenChange: noop }, h(OperatorValue, { value: v }, node)));
const sections = (html: string) => [...html.matchAll(/data-section="(\w+)"( data-done="true")?[^>]*>\s*<button[^>]*aria-expanded="(\w+)"/g)]
  .map((m) => `${m[1]}:${m[2] ? "done" : "open"}:${m[3]}`);
const button = (html: string, slot: string) => html.match(new RegExp(`<button[^>]*data-slot="${slot}"[^>]*>`))?.[0] ?? "";

(async () => {
  const src = createMockSource({ now: () => 1_800_000_000 });
  // 左上角：两个图标的分段（底框 + 选中格），只显示图标，aria-label 是提示标题；选中态跟着 value
  for (const mode of ["chat", "operator"] as const) {
    const sw = renderToStaticMarkup(h(ModeSwitch, { value: mode, onChange: noop }));
    assert.match(sw, /class="cc-op-seg"/);
    assert.equal((sw.match(/role="radio"/g) ?? []).length, 2);
    assert.match(sw, new RegExp(`aria-checked="true" aria-label="${mode === "chat" ? op.direct : op.handsOn}" data-mode="${mode}"`));
    assert.match(sw, new RegExp(`aria-checked="false" aria-label="${mode === "chat" ? op.handsOn : op.direct}"`));
    assert.equal((sw.match(/<svg viewBox="0 0 24 24" fill="none" stroke="currentColor"/g) ?? []).length, 2);
    // 按钮里只有图标，没有字
    for (const m of sw.matchAll(/<button[^>]*>(.*?)<\/button>/g)) assert.doesNotMatch(m[1]!.replace(/<[^>]+>/g, ""), /\S/);
    // 右边是 Claude 桌面版 Code 的「</>」（用户 10-29）
    assert.match(sw, /<path d="m18 16 4-4-4-4"><\/path><path d="m6 8-4 4 4 4"><\/path><path d="m14.5 4-5 16"><\/path>/);
  }

  // ── 新建任务：选了公司、月份就建（不必先说一句话），右侧栏随即出资料、上传框、开始前确认与「开始」 ────────────────────
  const made = await src.createTask({ company: "C-002", company_title: "นิภา เอ็กเช้นจ์ จำกัด", period: "2026-10" });
  assert.equal(made.status, "preparing");
  const page = await src.documents("C-002", "2026-10", 0, 50);
  const prechecks = await src.precheck("C-002", page.documents.map((d) => d.id));
  const questions = questionsOf(prechecks);
  const computers = await src.computers("C-002");
  const draft = draftOf(made);                               // 没有说过话：prompt 空
  assert.equal(draft.prompt, "");
  const data = { docs: page.documents, next: null, prechecks, computers };
  const waiting = value({ id: made.name, task: made, draft, data, questions, ready: false, computer: computers[0] });
  let tools = render(waiting, h(OperatorTools));
  // 「开始」不在顶栏（在右侧栏报告下面）
  assert.doesNotMatch(tools, /cc-op-start|cc-op-show-express|cc-op-stop/);
  // 右侧栏并排开着：开关不在主栏顶栏，在右侧栏最上面那一行的右上角（右侧栏从窗口顶到底一整列）；左边是拖宽的分隔条
  assert.doesNotMatch(tools, /cc-panel-toggle/);
  const dock = render(waiting, h(OperatorDock));
  assert.match(dock, /<aside class="cc-op-panel" data-mode="docked"[^>]*><div role="separator"[^>]*data-edge="start"[^>]*class="cc-split[^"]*"[^>]*><\/div><div class="cc-topbar cc-op-panel-bar"><button[^>]*data-slot="cc-panel-toggle"[^>]*aria-pressed="true"/);
  // 滚动在里面那一层（分隔条不跟着滚走）
  assert.match(dock, /<div class="cc-op-panel-scroll" data-slot="cc-op-panel-scroll"><section class="cc-op-section" data-section="files"/);
  const custom = render(waiting, h(OperatorDock, { top: (toggle: ReactNode) => h("div", { "data-slot": "corner" }, toggle, "WIN") }));
  assert.match(custom, /<div data-slot="corner"><button[^>]*cc-panel-toggle[^>]*>.*?<\/button>WIN<\/div>/);
  // 主栏：没有说过话就没有气泡，资料一张一行，状态「准备中」
  let main = render(waiting, h(OperatorMain));
  assert.doesNotMatch(main, /cc-bubble-user/);
  assert.equal((main.match(/data-slot="cc-op-row"/g) ?? []).length, 2);
  assert.ok(main.includes(op.state.preparing!));
  // 右侧栏五个分区，按顺序；准备中：资料、开始前确认展开，电脑与 Express 展开（登录未设置），报告收着
  let panel = render(waiting, h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.deepEqual(sections(panel), ["files:open:true", "confirm:open:true", "handle:done:false", "computer:open:true", "report:open:false"]);
  for (const id of ["files", "confirm", "handle", "computer", "report"]) assert.ok(panel.includes(`>${op.section[id]}<`), id);
  assert.ok(panel.includes(op.drop) && panel.includes(op.dropTypes));
  for (const group of ["purchase", "expense"]) assert.ok(panel.includes(`>${op.group[group]}<`), group);
  assert.ok(panel.includes(`>${op.kind.item}<`) && panel.includes(`>${op.kind.supplier}<`) && panel.includes(`>${op.kind.account}<`));
  assert.ok(panel.includes(`>${op.option.newItem}<`));
  assert.ok(panel.includes(`${op.expressLogin}`) && panel.includes(`>${op.loginUnset}<`) && panel.includes(`>${op.setUp}<`));
  assert.doesNotMatch(panel, /__new__<|>item<|>party<|choice_needed|\[OPERATOR/);
  assert.doesNotMatch(panel, /data-mode="floating"|cc-op-scrim|cc-op-show-express/);
  // 「开始」：报告下面、靠右的一行，还不能开始也不置灰；点了说还差什么（写在它上面）
  const start = button(panel, "cc-op-start");
  assert.ok(start, panel);
  assert.doesNotMatch(start, /disabled/);
  assert.ok(panel.indexOf('data-section="report"') < panel.indexOf('data-slot="cc-op-start-row"'));
  assert.match(panel, /<div class="cc-op-start" data-slot="cc-op-start-row"><button[^>]*data-slot="cc-op-start"[^>]*>/);
  assert.doesNotMatch(panel, /cc-op-start-notice/);
  panel = render(value({ ...waiting, notice: op.confirmLeft(3) }), h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.match(panel, new RegExp(`<div class="cc-op-start" data-slot="cc-op-start-row"><div role="alert" class="cc-alert" data-size="sm" data-slot="cc-op-start-notice">${op.confirmLeft(3)}</div><button`));
  assert.doesNotMatch(button(panel, "cc-op-start"), /disabled/);
  panel = render(value({ ...waiting, busy: true }), h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.doesNotMatch(button(panel, "cc-op-start"), /disabled/);
  // 答完：开始前确认收起、标 ✓
  const answers = Object.fromEntries(questions.map((q) => [q.key, q.options[0]!.code]));
  panel = render(value({ ...waiting, draft: { ...draft, answers } }), h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.ok(sections(panel).includes("confirm:done:false"), sections(panel).join(","));
  assert.match(panel, new RegExp(`role="img" aria-label="${op.state.done}"`));

  // ── 上次推送结果不明的单：开始前确认里写站点那一句 +「已入账」「没有入账」，点了交给 resolve；不进「需要处理」 ────────────
  const t3 = await src.createTask({ company: "C-003", company_title: "ทองไทย เทรดดิ้ง จำกัด", period: "2026-10" });
  const docs3 = (await src.documents("C-003", "2026-10", 0, 50)).documents;
  const checks3 = await src.precheck("C-003", docs3.map((d) => d.id));
  const unknownValue = value({ id: t3.name, task: t3, draft: draftOf(t3), data: { docs: docs3, next: null, prechecks: checks3, computers }, computer: computers[0] });
  panel = render(unknownValue, h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.ok(sections(panel).includes("confirm:open:true"), sections(panel).join(","));
  assert.ok(sections(panel).includes("handle:done:false"), sections(panel).join(","));   // 不在「需要处理」
  assert.match(panel, /data-slot="cc-op-unknown"/);
  assert.ok(panel.includes("ผลการส่งครั้งก่อนไม่ชัดเจน ตรวจสอบใน Express"));
  assert.ok(panel.includes(`data-slot="cc-op-entered">${op.doc.saved}<`) && panel.includes(`data-slot="cc-op-not-entered">${op.notEntered}<`));
  assert.match(panel, /data-section="confirm"[^>]*>\s*<button[^>]*>.*?<span class="cc-op-section-summary">0 \/ 1<\/span>/);
  let unknown: any[] = [];
  const ProbeUnknown = () => { unknown = useUnknownItems(); return null; };
  render(unknownValue, h(ProbeUnknown));
  assert.deepEqual(unknown.map((i) => i.key), ["D-301"]);
  unknown[0].entered();
  unknown[0].notEntered();
  assert.deepEqual(calls.slice(-2), ["resolve:D-301:true", "resolve:D-301:false"]);

  // ── 拖宽：并排时宽度记在本机，打开时照记下的（只并排时有分隔条）──────────────────────────────────────────────
  g.localStorage.setItem("cc.operator.panel.width", "480");
  const wide = render(waiting, h(OperatorDock));
  assert.match(wide, /<aside class="cc-op-panel" data-mode="docked" data-slot="cc-op-panel" aria-label="[^"]*" style="--cc-op-panel-w:480px">/);
  g.localStorage.removeItem("cc.operator.panel.width");

  // ── 进行中 / 做完：每张单一行、完成行；需要处理有「重试」「跳过」，点了交给数据源 ───────────────────────────
  const { id } = await src.createBatch({ company: "C-001", company_title: "บริษัท Pearnly จำกัด", period: "2026-10",
    doc_ids: ["D-101", "D-104-X", "D-103"], computer: "PC-1", start_at: 0, answers: {} });
  src.step(id); src.step(id);
  let s = await src.snapshot(id);
  const running = value({ id, snapshot: s });
  tools = render(running, h(OperatorTools));
  assert.ok(tools.includes(`data-slot="cc-op-stop"`) && !tools.includes("cc-op-start"));
  main = render(running, h(OperatorMain));
  assert.equal((main.match(/data-slot="cc-op-row"/g) ?? []).length, 3);
  assert.ok(main.includes(`>${op.doc.saved}<`) && main.includes(`>${op.doc.notDone}<`) && main.includes(`>${op.doc.entering}<`));
  assert.ok(main.includes(op.reason.negative_stock!));
  // 实操的状态只在对话框里说，同对话的工作状态行（用户 10-30）：在动的小点 +「计时 · 这张单」，概况那一行不再带状态
  assert.match(main, /data-slot="cc-run-indicator" data-kind="tool"[^>]*><span class="typing-dots" role="status" aria-label="[^"]+">/);
  assert.match(main, new RegExp(`typing-dots-label[^"]*" aria-hidden="true"><span class="cc-marquee-text">[^<]+ · [^<]+<`));
  assert.ok(!main.includes(`${op.state.running} 2 / 3`));
  assert.equal((main.match(/data-slot="cc-run-indicator"/g) ?? []).length, 1);  // 一次只有一行
  assert.ok(main.includes(`placeholder="${op.placeholder}"`));
  assert.match(main, /class="aui-composer-input /);                       // 对话页那一个输入框
  assert.match(main, /data-slot="aui_composer-shell"/);
  assert.doesNotMatch(main, /cc-op-finish/);                             // 还没做完
  assert.doesNotMatch(main, /negative_stock|>waiting<|>entering<|>saved</);
  assert.doesNotMatch(main, /data-slot="cc-op-panel"/);                  // 右侧栏由外壳放在主栏那一列右边（OperatorDock）
  // 不做的：步骤向导、统计卡片、实时画面、接管提示
  assert.doesNotMatch(main, /cc-operator-live|cc-steps-card|cc-card p-|data-step=|cc-operator-totals/);
  // 开始以后：右侧栏没有「开始」（停止在顶栏）
  assert.doesNotMatch(render(running, h(OperatorPanel, { mode: "docked", title: "T", onClose: noop })), /cc-op-start/);
  let items: any[] = [];
  const Probe = () => { items = useHandleItems(); return null; };
  render(running, h(Probe));
  assert.deepEqual(items.map((i) => i.key), ["D-104-X"]);
  items[0].retry();
  items[0].skip();
  assert.deepEqual(calls.slice(-2), ["retry:D-104-X", "skip:D-104-X"]);
  src.step(id);
  s = await src.snapshot(id);
  main = render(value({ id, snapshot: s }), h(OperatorMain));
  assert.ok(main.includes(`data-slot="cc-op-finish">${finishLine(s)}<`), main);
  assert.doesNotMatch(main, /cc-run-indicator|cc-op-run-error/);          // 做完：状态行消失
  panel = render(value({ id, snapshot: s }), h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.deepEqual(sections(panel).map((x) => x.split(":").slice(0, 2).join(":")), ["files:done", "confirm:done", "handle:open", "computer:open", "report:open"]);
  assert.ok(panel.includes(`>${op.exportReport}<`) && panel.includes(`>${op.retry}<`) && panel.includes(`>${op.skip}<`));
  // 停下还有没做的单（续做）：「开始」照样在报告下面
  await src.stop(id);
  const stoppedRun = await src.createBatch({ company: "C-001", period: "2026-10", doc_ids: ["D-101", "D-103"], computer: "PC-1", start_at: 0, answers: {} });
  await src.stop(stoppedRun.id);
  panel = render(value({ id: stoppedRun.id, snapshot: await src.snapshot(stoppedRun.id) }), h(OperatorPanel, { mode: "docked", title: "T", onClose: noop }));
  assert.ok(panel.indexOf('data-section="report"') < panel.indexOf('data-slot="cc-op-start"'));
  // 整个任务停在 Express 登录：需要处理里「设置」，点了设置登录
  const login = (await src.listBatches()).find((b) => b.reason === "login_missing")!;
  render(value({ id: login.id, snapshot: await src.snapshot(login.id) }), h(Probe));
  // 整个任务因必须人工的原因停了：对话框里是错误框（同对话出错），不是状态行
  main = render(value({ id: login.id, snapshot: await src.snapshot(login.id) }), h(OperatorMain));
  assert.ok(main.includes(`data-slot="cc-op-run-error" class="cc-alert"><p class="line-clamp-(--cc-alert-lines)">${op.reason.login_missing}</p>`), main);
  assert.doesNotMatch(main, /cc-run-indicator/);
  assert.equal(items[0].retryLabel, op.setUp);
  items[0].retry();
  assert.equal(calls.at(-1), "authorize");
  // 整个任务因别的整批停原因停了（授权名额、服务器……）：「重试」是整批继续（站点把没录成的交回去），不是桥上的开始
  render(value({ id: login.id, snapshot: { ...(await src.snapshot(login.id)), reason: "license" } }), h(Probe));
  assert.equal(items[0].key, "batch");
  assert.equal(items[0].retryLabel, undefined);
  items[0].retry();
  assert.equal(calls.at(-1), "resume");
  // 网页、手机上看的任务（remote）与 CC 重启后恢复出来的任务：停下等人的单照样能重试（交给站点）
  const held = { ...(await src.snapshot(login.id)), status: "finished" as const, reason: "", remote: true,
    docs: [{ doc_id: "R-1", doc_type: "RR", status: "waiting" as const, reason: "negative_stock", docnum: "", voided: [], placeholders: [],
      reports: [], issues: [], seconds: 0, evidence: "none" as const }] };
  render(value({ id: login.id, snapshot: held }), h(Probe));
  assert.deepEqual(items.map((i) => [i.key, !!i.retry, !!i.skip]), [["R-1", true, false]]);
  render(value({ id: login.id, snapshot: { ...held, remote: false, live: false } }), h(Probe));
  assert.deepEqual(items.map((i) => [i.key, !!i.retry, !!i.skip]), [["R-1", true, true]]);

  // ── 窄屏：右侧栏从右边滑出（遮罩 + 关闭），手机盖满屏；都没有分隔条 ─────────────────────────────────────────
  const narrow = render(running, h(OperatorPanel, { mode: "floating", title: "T", onClose: noop }));
  assert.match(narrow, /class="cc-op-scrim"/);
  assert.match(narrow, /class="cc-op-panel" data-mode="floating"/);
  assert.ok(narrow.includes(`aria-label="${t("close")}"`));
  assert.doesNotMatch(narrow, /role="separator"/);
  const phone = render(running, h(OperatorPanel, { mode: "phone", title: "T", onClose: noop }));
  assert.match(phone, /data-mode="phone"/);
  assert.doesNotMatch(phone, /cc-op-scrim|role="separator"/);

  // ── 新建页：大输入框居中 + 公司、月份（选了公司就建任务）；没选公司发不出 ─────────────────────────────────────
  const fresh = render(value({}), h(OperatorMain));
  assert.match(fresh, /data-slot="cc-op-new"/);
  assert.ok(fresh.includes(`placeholder="${op.placeholder}"`));
  assert.match(fresh, /data-slot="cc-chin-company"/);
  assert.match(fresh, /data-slot="cc-period-chip"/);
  assert.match(fresh.match(/<button[^>]*aui-composer-send[^>]*>/)![0], /disabled=""/);
  assert.doesNotMatch(fresh, /cc-chin-computer/);

  // ── 侧栏的列表：同对话侧栏（按公司分组的行；「新建任务」在两种模式共用的导航里），行只有标题，没有第二行和状态点 ──────────────
  const tasks = (await src.listTasks(0, 50)).tasks;
  // 静态渲染读的是各仓的初始值（zustand 的 getServerSnapshot）：把任务列表、正开着的任务放进初始值
  Object.assign(useTasks.getInitialState(), { rows: tasks, loaded: true, next: null });
  Object.assign(useModePlace.getInitialState(), { batch: made.name });
  setPrep(made.name, { prompt: "" });
  const side = render(value({ id: made.name, task: made }), h(OperatorSidebar));
  assert.doesNotMatch(side, /data-slot="cc-operator-new"/);
  assert.match(side, /data-slot="aui_thread-list-root"/);
  const rowsHtml = [...side.matchAll(/<div[^>]*data-slot="aui_thread-list-item"[^>]*>.*?<\/button><button[^>]*aui_thread-list-item-more/g)];
  assert.ok(rowsHtml.length >= 3, side);
  assert.equal((side.match(/data-slot="cc-project"/g) ?? []).length, 3);    // 三家公司各一组
  assert.match(side, /class="cc-row-trigger /);
  assert.doesNotMatch(side, /cc-row-meta|cc-op-dot|cc-op-task|data-tone=/);
  for (const name of [op.state.preparing, op.state.attention, op.state.running, op.queued]) assert.ok(!side.includes(`>${name}<`), name);
  assert.match(side, new RegExp(`data-row-id="${made.name}"[^>]*`));
  assert.match(side, new RegExp(`aria-current="true" data-row-id="${made.name}"`));   // 正开着的那一行
  g.done();
})().catch((e) => { console.error(e); process.exit(1); });
"""

DOM_SETUP = r"""
const g = globalThis as any;
g.addEventListener ??= () => {};
g.removeEventListener ??= () => {};
const store = new Map<string, string>();
g.localStorage ??= { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, String(v)), removeItem: (k: string) => void store.delete(k), clear: () => store.clear() };
g.requestAnimationFrame ??= (f: () => void) => setTimeout(f, 0);
g.cancelAnimationFrame ??= (id: number) => clearTimeout(id);
g.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
g.navigator ??= { userAgent: "node", onLine: true };
"""

STUBS = {"socket.io-client": SOCKET_STUB, "cc-dom-setup": DOM_SETUP, "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined; '
         'export const loadProfile = async () => null; export const assistantMascot = () => null; export const assistantLogo = () => null;'}

OLD_WORDS = re.compile(r"代录|录入|已录入|再录一次|不录了|录入前确认|录入后处理|等开始|正在录入|批次|照录|挂起|转人工|读回")


class SameReasonsAsDesktop(unittest.TestCase):
    def test_typescript_reasons_match_contract_py(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "op_contract", Path(ROOT) / "companion/src/companion/express_operator/contract.py")
        contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contract)
        ts = (SRC / "components/operator/types.ts").read_text(encoding="utf-8")
        block = ts[ts.index("export const REASONS"):]
        block = block[:block.index("};")]
        found = dict(re.findall(r"^\s+(\w+): \"(\w+)\"", block, re.M))
        self.assertEqual(found, contract.REASONS)


class SourceRules(unittest.TestCase):
    def test_hands_on_panel_is_a_full_height_column_like_claude(self):
        # 实操：主栏一列（顶栏止于右侧栏分隔线）+ 右侧栏从窗口顶到底一整列，分隔线不断；右侧栏开着时开关与窗口按钮在它右上角。
        # 对话页的会话栏照旧（.cc-panel 不动）
        shell = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        frame = shell.split('<div className="relative flex min-h-0 min-w-0 flex-1 flex-row" data-slot="cc-op-frame">', 1)[1].split("  ) : (", 1)[0]
        column, dock = frame.split("<OperatorDock", 1)
        self.assertIn("<MainTopBar nav={nav} window={win} chrome={chrome} tools={tools} />", column)
        self.assertIn("<OperatorMain />", column)
        self.assertNotIn("MainTopBar", dock)
        self.assertIn('<div className="cc-titlebar cc-op-panel-bar justify-end pe-0" {...dragHandlers(win.toggle)}>\n                  {toggle}\n'
                      '                  <WindowButtons maximized={win.maximized} toggle={win.toggle} />', dock)
        self.assertIn("{!(operator && docked) && <WindowButtons maximized={win.maximized} toggle={win.toggle} />}", shell)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertIn(".cc-op-panel-bar { position: sticky; top: 0;", css)
        workspace = (SRC / "components/operator/workspace.tsx").read_text(encoding="utf-8")
        self.assertIn("{!docked && <PanelToggle />}", workspace)
        self.assertNotIn("<OperatorPanel", workspace.split("export const OperatorMain", 1)[1])

    def test_show_express_button_is_gone(self):
        # 「显示 Express 窗口」两个按钮与词条都删了；桥那边的 operator_show 不动（companion）
        for path in list((SRC / "components/operator").glob("*.ts*")) + [SRC / "assistant.tsx", SRC / "i18n.ts"]:
            text = path.read_text(encoding="utf-8")
            for word in ("showExpress", "operator_show", "cc-op-show-express", "显示 Express 窗口", "Show Express", "แสดงหน้าต่าง Express"):
                self.assertNotIn(word, text, f"{path.name}: {word}")

    def test_old_words_never_reach_the_page(self):
        # 界面上「录 / 代录 / 批次 / 照录 / 挂起 / 转人工 / 读回」不再出现：词典里的中文、组件里的字面量都不许
        i18n = (SRC / "i18n.ts").read_text(encoding="utf-8")
        block = i18n[i18n.index("const OPERATOR = {"):]
        zh = block[block.index("  zh: {"):block.index("  th: {")]
        for line in zh.splitlines():
            code = line.split("//")[0]
            for text in re.findall(r'"([^"]*)"|`([^`]*)`', code):
                self.assertIsNone(OLD_WORDS.search("".join(text)), line)
        th = block[block.index("  th: {"):]
        self.assertNotIn("คีย์", th)
        self.assertNotIn("บันทึกแทน", th)
        for path in sorted((SRC / "components/operator").glob("*.ts*")):
            text = path.read_text(encoding="utf-8")
            code = "\n".join(line.split("//")[0] for line in text.splitlines())
            for literal in re.findall(r'"([^"\n]*)"', code):
                self.assertIsNone(re.search(r"[\u4e00-\u9fff\u0e00-\u0e7f]", literal) if path.name != "mock-source.ts" else None,
                                  f"{path.name}: {literal}")

    def test_live_frame_and_wizard_are_gone(self):
        # 实时画面去掉了（不再调 operator_frame）；步骤向导、统计卡片、批次页那几个旧组件删掉
        operator = SRC / "components/operator"
        for name in ("prepare-flow.tsx", "run-view.tsx", "report-view.tsx", "batch-page.tsx", "parts.tsx"):
            self.assertFalse((operator / name).exists(), name)
        for path in operator.glob("*.ts*"):
            self.assertNotIn("operator_frame", path.read_text(encoding="utf-8"), path.name)

    def test_switch_sits_on_the_title_row_and_follows_it(self):
        # 两个图标在侧栏最上面那一行（☰、侧栏开关、←、→）右端；侧栏收起时跟着那一行在顶栏；原来那排文字分段去掉
        shell = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        self.assertIn('<TitleBarLeft nav={nav} />\n              {/* 直写 / 实操：→ 右边、靠这一行右端 */}\n              <Modes className="ms-auto" />', shell)
        self.assertIn("{!open && <TitleBarLeft nav={nav} navToggle />}\n          {/* 侧栏收起：直写 / 实操跟着那一行留在顶栏 */}\n          {!open && <Modes />}", shell)
        self.assertNotIn("桌面窗口的标题栏放不下：切换在它下面一行", shell)
        self.assertIn("modeForKey(event)", shell)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertIn("--cc-op-seg-btn: 30px;", css)
        self.assertIn(".cc-op-seg-btn svg { width: var(--cc-icon-md); height: var(--cc-icon-md); stroke-width: var(--cc-op-seg-stroke); }", css)
        self.assertIn('.cc-op-seg-btn[aria-checked="true"] { background: var(--cc-bg-surface); border-color: var(--cc-line); color: var(--cc-ink); }', css)
        self.assertRegex(css, r"--cc-icon-md: 18px;")

    def test_panel_has_its_own_classes(self):
        # 实操右侧栏用自己的类（与对话页会话栏 .cc-panel 分开）：整张贴右边、左边 1px 线、分区之间 1px 线、窄屏从右边整高滑出
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        rule = css.split("  .cc-op-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("border-inline-start: var(--cc-ring-w) solid var(--cc-line);", rule)
        self.assertIn("border-block-end: var(--cc-ring-w) solid var(--cc-line);", css.split("  .cc-op-section {", 1)[1].split("}", 1)[0])
        floating = css.split('  .cc-op-panel[data-mode="floating"] {', 1)[1].split("}", 1)[0]
        self.assertIn("position: absolute; inset-block: 0; inset-inline-end: 0;", floating)
        self.assertIn("animation: cc-op-slide-in", floating)
        self.assertIn("--cc-op-narrow: 900px;", css)
        panel = (SRC / "components/operator/panel.tsx").read_text(encoding="utf-8")
        self.assertNotRegex(panel, r'className="cc-panel[ "-]')


class SharedWithChatRules(unittest.TestCase):
    """实操复用对话侧的组件与逻辑（10-27 主会话审定）：侧栏、输入框、拖宽分隔条同一套，只是数据换成任务。"""

    def test_sidebar_uses_the_chat_sidebar_layer(self):
        side = (SRC / "components/operator/operator-sidebar.tsx").read_text(encoding="utf-8")
        for name in ("SidebarGroups", "RowShell", "RowMenu", "BulkMenu", "InlineRename", "ListMore", "usePinRowDrag", "useGroupRowDrag",
                     "ActivityMark", "RowScopeContext"):
            self.assertIn(name, side.split('} from "@/components/assistant-ui/thread-list";', 1)[0], name)
        self.assertIn("CompanyHeaderView", side)
        self.assertIn('askDelete(ids, () => runInOrder(ids, (name) => latest.current.o.remove(name), report).then(clearSelection), {', side)
        self.assertIn("title: op.deleteTask,", side)
        self.assertIn("await taskOrder.starChat(item, starred, after);", side)   # 置顶同对话的 starChat（取消置顶回组顶）
        # 行没有第二行、状态点；自己的确认弹窗、行样式都去掉了
        for gone in ("cc-row-meta", "cc-op-dot", "cc-op-task", "taskMeta", "taskTone", "ConfirmDelete", "DialogPrimitive"):
            self.assertNotIn(gone, side, gone)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        self.assertNotIn(".cc-op-dot", css)
        self.assertNotIn(".cc-op-task", css)
        # 手排顺序、折叠是实操自己的实例，同步进服务端偏好的 operator 份
        prefs = (SRC / "components/operator/prefs.ts").read_text(encoding="utf-8")
        self.assertIn('pref: OPERATOR_PREF,\n  prefix: "order/",', prefs)
        self.assertIn('createSidebarFold("cc.operator.sidebar.collapsed")', prefs)
        self.assertIn('"cards/",', prefs)
        order = (SRC / "lib/managed-agents/sidebar-order.ts").read_text(encoding="utf-8")
        self.assertIn('const chat = createSidebarOrder({ key: orderKey, pref: "order", seenGroups: projectsSeen });', order)

    def test_shell_wires_search_keys_and_new_task(self):
        shell = (SRC / "assistant.tsx").read_text(encoding="utf-8")
        self.assertIn("<OperatorNav search={search} />", shell)
        self.assertIn("<OperatorNav search={search} onPick={close} />", shell)
        self.assertIn('if (mode === "operator") return;', shell.split("function useBackToChatOnPick(", 1)[1].split("\n}\n", 1)[0])
        # 两种模式共用一个导航框架（主会话 11-03）：电脑侧栏、手机抽屉都先出 SidebarNav，下面才按模式换列表
        self.assertIn("<SidebarNav />\n        {operator ? <OperatorNav search={search} /> : <ThreadList search={search} />}", shell)
        self.assertIn("<SidebarNav mobile onPick={close} />\n        {operator ? <OperatorNav search={search} onPick={close} /> : <ThreadList search={search} />}", shell)
        nav = shell.split("function SidebarNav(", 1)[1].split("\n}\n", 1)[0]
        # 共有的在上（新建 → 待处理 → 公司），模式自己的（定时任务）在最下面：切模式时上面几行一格不挪
        order = [nav.index(name) for name in ("<NewNav />", "<PendingNav ", "<CompanyNav ", "<ScheduledNav ")]
        self.assertEqual(order, sorted(order))
        scheduled = shell.split("function ScheduledNav(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('if (!hasScheduledTasks(profile) || mode !== "chat") return null;', scheduled)
        self.assertIn('return !!profile?.sections?.includes("tasks");', shell)   # 定时任务按产品声明，不按产品名
        self.assertNotIn('profile?.product === "cowork"', shell.split("function hasScheduledTasks(", 1)[1].split("\n}\n", 1)[0])
        pending = shell.split("function PendingNav(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("usePendingCounts((s) => s.total) + useAttention((s) => s.total)", pending)
        # 新建按模式；代录模式的 Ctrl / ⌘ + N 在外壳里接（看着公司、待处理页时也在），主栏里不再各接一份
        self.assertIn('data-slot="cc-operator-new"', shell.split("function NewTaskNav(", 1)[1].split("\n}\n", 1)[0])
        self.assertIn('openOperator("", view, setView);', shell.split("function useModeKeys(", 1)[1].split("\n}\n", 1)[0])
        work = (SRC / "components/operator/workspace.tsx").read_text(encoding="utf-8")
        self.assertNotIn('e.code !== "KeyN"', work)
        self.assertIn("{operator ? op.newTask : t(\"newChatPill\")}", shell)
        self.assertNotIn("{searching && !operator && (", shell)

    def test_typed_words_go_to_the_task_conversation_and_mid_run_choices_show_in_the_log(self):
        # 对话框甲（主会话 10-29 审定）：打的字交给这个任务的对话（同对话页的会话控制器），资料照旧进资料区；回复画在单据行下面；
        # 录到中途停下等人选的单在任务记录里出选项卡片，点确定交回任务（task_answer，不调模型）；跳过 / 重做由执行的电脑取走照桥做
        work = (SRC / "components/operator/workspace.tsx").read_text(encoding="utf-8")
        sender = work.split("function sender(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (text) void thread.send(text)", sender)
        self.assertNotIn("o.create(", sender)                                   # 开始以后打字不再另建任务
        self.assertIn("<ThreadMessages />", work)
        self.assertIn("{waiting.length > 0 && <TaskChoices waiting={waiting} />}", work)
        self.assertIn("messages: thread.snapshot.messages", work)
        thread = (SRC / "components/operator/task-thread.tsx").read_text(encoding="utf-8")
        self.assertIn("await source.taskThread(name)", thread)                 # 第一次打字时才建对话
        self.assertIn("await getSessionController(id).sendMessage(text, [])", thread)
        self.assertIn("o.source.answerTask(task.name, docId, list)", thread)
        # 状态只在对话框里说、同对话那一条（用户 10-30）：回复下面的状态行认这段任务对话；实操状态用同一个 RunLine，
        # 对话这一轮在跑时让给对话；等选项卡片时卡片下面说「需要您的输入」
        self.assertIn("<SessionIdOverride.Provider value={thread.sessionId}>", work)
        self.assertIn("<ThreadMessages />\n          <TaskRunIndicator chatBusy={chatBusy} />", work)
        self.assertIn('<RunLine kind="input" className={RUN_GAP} />', thread)
        self.assertIn("if (chatBusy || activity.kind === \"hidden\") return null;", thread)
        provider = (SRC / "lib/managed-agents/runtime-provider.tsx").read_text(encoding="utf-8")
        for hook in ("export function useActiveSessionSnapshot", "export function useOpenState"):
            self.assertIn("useShownSessionId();", provider.split(hook, 1)[1].split("\n}\n", 1)[0])
        chat = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("return <RunLine kind={activity.kind} text={text} label={label} className={className} />;", chat)
        live = (SRC / "components/operator/live-source.ts").read_text(encoding="utf-8")
        self.assertIn('post<{ controls?: Array<{ action?: string; doc_id?: string }> }>(API + "task_controls"', live)
        self.assertIn("if (s.remote || isFinal(s.status)", live)               # 只有执行的那台电脑取

    def test_composer_and_split_handle_are_the_chat_ones(self):
        work = (SRC / "components/operator/workspace.tsx").read_text(encoding="utf-8")
        self.assertIn('import { Composer, ThreadMessages } from "@/components/assistant-ui/thread";', work)
        self.assertIn("<Composer placeholder={op.placeholder} draft={false} chin={chin} />", work)
        self.assertIn("useExternalStoreRuntime<ThreadMessageLike>({", work)
        self.assertIn("convertMessage: (m) => m,", work)                     # 同对话页：会话控制器的消息原样交给组件库转换
        self.assertNotIn("<textarea", work)                                     # 不再自写输入框
        thread = (SRC / "components/assistant-ui/thread.tsx").read_text(encoding="utf-8")
        self.assertIn("{draft && <DraftWatcher />}", thread)
        self.assertIn("const placeholder = fixed ?? (hasMessages ? t(\"reply\") : t(\"placeholder\"));", thread)
        panel = (SRC / "components/operator/panel.tsx").read_text(encoding="utf-8")
        self.assertIn('<SplitHandle size={operatorPanelSize} edge="start" onChange={width.update} />', panel)
        handle = (SRC / "components/split-handle.tsx").read_text(encoding="utf-8")
        self.assertIn('key: "cc.operator.panel.width",', handle)
        self.assertIn('cssVar: "--cc-op-panel-w",', handle)
        css = (SRC / "globals.css").read_text(encoding="utf-8")
        rule = css.split("  .cc-op-panel {", 1)[1].split("}", 1)[0]
        self.assertIn("position: relative;", rule)
        self.assertNotIn("overflow-y", rule)                                   # 滚动在里面那一层
        self.assertIn(".cc-op-panel-scroll { display: flex; flex: 1; flex-direction: column; min-height: 0; overflow-y: auto; }", css)
        self.assertRegex(css, r"--cc-op-panel-min-w: \d+px;")
        self.assertRegex(css, r"--cc-op-panel-max-w: \d+px;")


class OperatorTests(unittest.TestCase):
    def test_logic_and_mock_source(self):
        for lang in ("en", "zh", "th"):
            run_ts(LOGIC, stubs=STUBS, lang=lang)

    def test_components_render(self):
        for lang in ("zh", "th", "en"):
            run_ts(RENDER, stubs=STUBS, lang=lang)

    def test_live_source_and_mode_switch(self):
        for lang in ("en", "zh", "th"):
            run_ts(LIVE, stubs=STUBS, lang=lang)


if __name__ == "__main__":
    unittest.main()
