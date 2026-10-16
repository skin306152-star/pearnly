"""数字会计员第 1 期月结台账的页面一侧（docs/work/2026-10-15-digital-accountant-plan.md；外壳设计文档 3.13 / 4.4 / 4.5）：

- 右侧会话栏「本月」卡（对话挂的公司与月份）与公司页概览第一节（月份胶囊，默认上个月）共用 components/month-ledger.tsx；
- 四段（收单 / 录账 / 核对 / 报税）一行一段：状态点 + 段名 + 12px 淡色短计数；服务端给 null 的段不列；
- 异常按严重度分组（要处理 → 看一眼 → 知悉），一行 26：码名、单据 / 对方、金额，行尾去处；choice_needed 当场选（待处理的
  ChooseRow，F18 规则），任务到任务页，其余交给 CC（预填不发送）；知悉的淡色；
- 会话栏里整个月什么都没有时不显示；跟着推送 / 定时任务的站点事件重取（只在挂着时）；
- 服务端只给码：每个码在三种语言里都有短名（码表从 companies/exceptions.py 读）；保留的码不出现。

假数据就是服务端测试断言过的真实输出（tests/fixtures/cc-month-contract.json）。做法同 tests/test_assistant_ui.py。
"""
import json
import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_assistant_ui import ESBUILD, MA_SETUP, NODE, ROOT, block, keys, read, run_ts  # noqa: E402

COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
FIXTURE = (ROOT / "tests/fixtures/cc-month-contract.json").read_text(encoding="utf-8")


def server_codes():
    """companies/exceptions.py 的 CODES 键（account_match 的常量换成码）与 RESERVED。"""
    source = (COWORK / "companies/exceptions.py").read_text(encoding="utf-8")
    table = source.split("CODES = {", 1)[1].split("\n}", 1)[0]
    match = (COWORK / "targets/account_match.py").read_text(encoding="utf-8")
    constants = dict(re.findall(r'^(\w+_MISMATCH) = "(\w+)"', match, re.M))
    codes = set(re.findall(r'^\s+"(\w+)": \(', table, re.M))
    codes |= {constants[name] for name in re.findall(r"^\s+account_match\.(\w+): \(", table, re.M)}
    reserved = set(re.findall(r'"(\w+)"', source.split("RESERVED = (", 1)[1].split(")", 1)[0]))
    return codes, reserved


class LedgerSourceTests(unittest.TestCase):
    def test_every_code_stage_state_and_severity_has_words_in_every_language(self):
        codes, reserved = server_codes()
        self.assertEqual(len(codes), 21)
        self.assertEqual(reserved, {"missing_document", "rule_conflict", "month_reopened"})
        company = block(read("i18n.ts"), "const COMPANY = {")
        sets = {}
        for lang in ("en", "zh", "th"):
            ledger = block(block(company, f"  {lang}: {{"), "    ledger: {")
            self.assertEqual(keys(block(ledger, "code: {")) & codes, codes, lang)
            self.assertFalse(keys(block(ledger, "code: {")) & reserved, lang)  # 第 4–6 期的码不先写
            self.assertEqual(keys(block(ledger, "stage: {")), {"intake", "entry", "verify", "tax"}, lang)
            self.assertEqual(keys(block(ledger, "state: {")), {"empty", "open", "attention", "clear"}, lang)
            self.assertEqual(keys(block(ledger, "severity: {")), {"action", "warn", "info"}, lang)
            sets[lang] = keys(ledger)
        self.assertEqual(sets["en"], sets["zh"])
        self.assertEqual(sets["en"], sets["th"])
        self.assertTrue({"title", "docs", "issues", "entered", "recognizedOnly", "readBack", "readCount", "readFailed", "reading",
                         "due", "openTask", "partial", "moreItems", "readbackText", "duplicateText"} <= sets["en"])
        i18n = read("i18n.ts")
        self.assertIn('stage: { intake: "收单", entry: "录账", verify: "核对", tax: "报税" }', i18n)
        # 已有文案不改：卡片标题仍是「本月」，交给 CC、显示更多沿用
        self.assertIn('thisMonth: "本月"', i18n)
        self.assertIn('handToCC: "交给 CC"', i18n)
        # 同一张单在相邻的「待处理」卡里叫什么，这里就叫什么（服务端已有词条 cowork.company.pending.not_pushed）
        tsv = (COWORK / "i18n/messages.tsv").read_text(encoding="utf-8")
        self.assertIn("Not pushed yet\t还没推送\tยังไม่ได้ส่ง\t", tsv)
        for words in ('not_pushed: "Not pushed yet"', 'not_pushed: "还没推送"', 'not_pushed: "ยังไม่ได้ส่ง"'):
            self.assertIn(words, i18n)
        # 泰文的「在 Express 里改了 / 删了 / 作废了」是被动（不是叫人去删）
        for words in ('express_changed: "ถูกแก้ไขใน Express"', 'express_deleted: "ถูกลบใน Express"', 'express_voided: "ถูกยกเลิกใน Express"'):
            self.assertIn(words, i18n)

    def test_panel_and_company_page_use_the_same_ledger(self):
        panel = read("components/session-panel.tsx")
        self.assertIn("month: <MonthCard company={company} period={period} />,", panel)
        self.assertNotIn("const MonthCard: FC = () => null;", panel)
        # 公司页的「月结」卡在公司页的会话栏里，月份胶囊与输入框的同一个（公司页自己的月份）
        self.assertIn("month: <MonthSection company={company} period={period} onPeriod={setPagePeriod} />,", panel)
        self.assertIn("{withPeriod && <PeriodChip look=\"cc-chin-btn\" side=\"top\" value={period} onChange={setPagePeriod} />}",
                      read("components/company-page.tsx"))
        ledger = read("components/month-ledger.tsx")
        self.assertIn('extra={<PeriodChip value={period} onChange={onPeriod} allowNone={false} look="cc-chin-btn" />}', ledger)
        self.assertIn("summary={month ? monthSummary(month) : undefined}", ledger)
        # 段行右侧的状态完整显示，左边段名让位、放不下时由它滚；同一行不会两处一起滚（用户 10-16 截图）
        self.assertEqual(ledger.count('className="cc-panel-extra cc-month-status"'), 1)
        css = read("globals.css")
        self.assertIn('[data-slot="cc-month-stage"] > .cc-panel-extra.cc-month-status { max-width: calc(100% - 4rem); }', css)
        # 账套行两行（第 8 轮：1024 宽、右侧栏 280 时状态要 178px 只给了 156px、账套名压到 60px）：账套名一行、状态一行（12px 淡色），
        # 各自全宽、各自是一行悬停滚动的范围
        self.assertIn('<div key={`${set.connection}|${set.key}`} className="cc-panel-row cc-panel-indent cc-month-set" data-slot="cc-month-set">\n'
                      '            <Marquee className="cc-month-set-name" data-marquee-host="">{set.label || set.key}</Marquee>\n'
                      '            <Marquee className="cc-month-set-status" data-marquee-host="">{setSummary(set)}</Marquee>', ledger)
        self.assertIn(".cc-panel-row.cc-month-set { flex-direction: column; align-items: stretch; gap: 0; padding-block: 0.1875rem; }", css)
        status = css.split("  .cc-month-set-status {", 1)[1].split("}", 1)[0]
        for part in ("min-width: 0; overflow: hidden; white-space: nowrap; color: var(--cc-ink-3);",
                     "font-size: var(--cc-text-caption); line-height: var(--cc-text-caption-lh);"):
            self.assertIn(part, status)
        self.assertNotIn("max-width", status)
        self.assertIn("const period = isPeriod(chosen) ? chosen : lastMonth();", ledger)  # 页面「不指定」时看上个月
        # 会话栏：没取到、空月不显示；出错显示原因
        card = ledger.split("export const MonthCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!month && !error) return null;", card)
        self.assertIn("if (month && emptyMonth(month) && !error) return null;", card)
        self.assertIn('<p role="alert"', card)
        # 卡头：当月写「本月」，对话挂的是别的月写那个月
        self.assertIn("const title = period === periodOf(new Date()) ? co.thisMonth : monthLabel(period);", card)
        # 重复单号只是知悉：单独一句「核对同号」，不用「处理这几张」；草稿用表名，不带内部码
        self.assertIn("start(filing, duplicatePrefill(item), false);", ledger)
        self.assertIn("return co.ledger.duplicateText(docList(lines));", ledger)
        self.assertIn('co.draftText(FORM_NAME[item.kind ?? ""] ?? item.kind ?? "", month)', ledger)
        # 当场选沿用待处理的 ChooseRow（F18 规则在它里面）；交给 CC 同一个 useAskCC
        self.assertIn("<ChooseRow item={pendingOf(item, company)} head={<ItemHead item={item} />}", ledger)
        self.assertIn("const ask = useAskCC(company, title);", ledger)
        pending = read("components/pending.tsx")
        self.assertIn("export const ChooseRow: FC<", pending)
        self.assertIn("export function useAskCC(", pending)
        self.assertIn("initialChoice(next, need)", pending)

    def test_reads_are_live_only_while_mounted_and_use_get(self):
        month = read("lib/month.ts")
        self.assertIn('const API = "pearnly_cowork.companies.api.month";', month)
        self.assertIn("return getMethod<Month>(API, { company, period, limit });", month)
        # 推送状态与定时任务的站点事件（PENDING_EVENTS 含 TASK_EVENTS 的那一个）；没有月份时不挂
        # 整月现算比较重：两次至少隔 15 秒、空闲 10 分钟才兜底一次；还在回读时有上限地定时重取（回读完成不发站点事件）
        self.assertIn("useLiveRefresh(reload, { events: ok ? PENDING_EVENTS : [], busy, busyMs: REFRESH_MIN_MS, idleMs: IDLE_MS, minMs: REFRESH_MIN_MS });", month)
        self.assertIn("export const REFRESH_MIN_MS = 15_000;", month)
        self.assertIn("export const IDLE_MS = 10 * 60_000;", month)
        self.assertIn("const busy = reading && Date.now() - since.current < READING_POLL_MS;", month)
        # 同一个错误不重设，role="alert" 不反复播报
        self.assertIn("before.key === key && before.error === error ? before :", month)
        companies = read("lib/companies.ts")
        self.assertIn('PENDING_EVENTS = ["cowork_intake_changed", "cowork_folder_changed"] as const;', companies)
        self.assertIn('TASK_EVENTS = ["cowork_folder_changed"] as const;', companies)

    def test_styles_use_the_measured_tokens(self):
        css = read("globals.css")
        section = css.split("/* 月结台账（数字会计员第 1 期", 1)[1].split("\n\n", 1)[0]
        self.assertNotRegex(re.sub(r"/\*.*?\*/", "", "/*" + section, flags=re.S), r"\d+px")  # 尺寸只引用变量（注释不算）
        self.assertIn("width: var(--cc-row-pulse); height: var(--cc-row-pulse);", section)  # 状态点同行尾脉冲点
        self.assertIn("width: var(--cc-icon-sm); height: var(--cc-icon-sm);", section)     # 20 槽里的 16
        for state, token in (("open", "processing"), ("attention", "attention"), ("clear", "online")):
            self.assertIn(f'.cc-month-dot[data-state="{state}"]::before {{ background: var(--cc-status-{token}); }}', section)
        self.assertIn('.cc-month-dot[data-state="empty"]::before { box-shadow: var(--cc-dot-ring); }', section)
        self.assertIn("font-size: var(--cc-text-caption); line-height: var(--cc-text-caption-lh);", section)  # 12px 淡色
        self.assertIn('.cc-panel-row[data-severity="info"] { color: var(--cc-ink-3); }', section)
        # 行就是会话栏的行（26、13/19）
        ledger = read("components/month-ledger.tsx")
        self.assertIn('className="cc-panel-row" data-slot="cc-month-stage"', ledger)
        self.assertIn('className="cc-panel-row" data-slot="cc-month-item"', ledger)
        self.assertIn('className={head ? "cc-pending-toggle cc-panel-row"', read("components/pending.tsx"))


STUBS = {"socket.io-client": "export function io() { return { on() { return this; }, off() { return this; }, connected: true }; }",
         "@/lib/profile": 'export const APP_NAME = "CC"; export const useProfile = () => undefined;',
         "cc-ma-setup": MA_SETUP, "cc-month-fixture": "export default " + FIXTURE + ";"}

RENDER = r"""
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.browser";
import fixture from "cc-month-fixture";
import { LedgerBody, GROUP_SHOWN, GROUP_SHOWN_BY, stageSummary, codeName } from "@/components/month-ledger";
import { choosable } from "@/components/pending";
import { attentionCount, dayShort, dayTime, emptyMonth, exceptionSubject, groupExceptions, loadMonth, pendingOf, readingNow, shownStages, taxDue, unlisted } from "@/lib/month";

const g = globalThis as any;
const month: any = fixture.month;
const noop = () => {};
const html = (m: any) => renderToStaticMarkup(createElement(LedgerBody, { month: m, company: "COMPANY", onAct: noop, onDone: noop, onChanged: noop }));
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, "\n").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").split("\n").map((s) => s.trim()).filter(Boolean);
const rows = (markup: string, slot: string) => [...markup.matchAll(new RegExp(`<(?:div|button)[^>]*data-slot="${slot}"[^>]*>([\\s\\S]*?)</(?:div|button)>`, "g"))];
"""


@unittest.skipUnless(NODE and ESBUILD.is_file(), "要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class LedgerBehaviourTests(unittest.TestCase):
    def run_page(self, body, lang="en"):
        run_ts('import "cc-test-setup";\nimport "cc-ma-setup";\n' + RENDER + body + "\n(globalThis as any).done();\n", stubs=STUBS, lang=lang)

    def test_stages_show_state_and_short_counts(self):
        self.run_page(r"""
const page = html(month);
const stages = [...page.matchAll(/data-slot="cc-month-stage" data-stage="(\w+)" data-state="(\w+)"/g)].map((m) => [m[1], m[2]]);
assert.deepEqual(stages, [["intake", "attention"], ["entry", "attention"], ["verify", "attention"], ["tax", "attention"]]);
// 收单 8 张有日期 + 1 张没日期；录账 已推 2 / 共 7；核对 最近完成的回读；报税 ภ.พ.30 电子申报截止
assert.equal(stageSummary("intake", month), "9 documents");
// 录账写出另有 2 张只识别（收单 9 = 录账 7 + 只识别 2）；核对写谁、何时读的（能用这条连接的人都看得到）
assert.equal(stageSummary("entry", month), "2/7 entered · 2 recognized only");
assert.equal(stageSummary("verify", month), `Read back ${dayTime("2026-10-02T03:01:00Z")} · Bee`);
const due = taxDue(month.stages.tax);
assert.equal(stageSummary("tax", { stages: { ...month.stages, tax: { ...month.stages.tax } } }), due ? `Due ${dayShort(due)}` : "");
// 都过了：不写截止日，行上写状态词（不写「逾期」）
const late = { stages: { ...month.stages, tax: { ...month.stages.tax, deadlines: { pp30: { efiling: "2020-01-23" }, pnd: { efiling: "2020-01-15" } } } } };
assert.equal(stageSummary("tax", late), "");
assert.equal(dayShort("2026-10-23"), "Oct 23");
assert.ok(text(page).slice(0, 12).join("|").startsWith("Intake|9 documents|Entry|2/7 entered · 2 recognized only|Verify|Read back"));
// 截止日：只有 ภ.พ.30 → 它；有 ภ.ง.ด. 草稿 → 更早的 ภ.ง.ด.；过了的跳过；都过了不写截止日（也不说逾期，测试窗口第 3 轮）
const tax = month.stages.tax;
assert.equal(taxDue(tax, "2026-10-10"), "2026-10-23");
const withWht = { ...tax, wht: [{ draft: "W1" }] };
assert.equal(taxDue(withWht, "2026-10-10"), "2026-10-15");
assert.equal(taxDue(withWht, "2026-10-16"), "2026-10-23");
assert.equal(taxDue(withWht, "2026-11-01"), "");
assert.equal(taxDue(tax, "2026-10-24"), "");
// 核对一段的回读时刻写到时分（这台设备的时区），只有日期的照旧
process.env.TZ = "Asia/Bangkok";
assert.equal(dayTime("2026-10-02T03:01:00Z"), "Oct 2, 10:01");
assert.equal(dayTime("2026-10-02T15:30:00Z"), "Oct 2, 22:30");
assert.equal(dayTime("2026-10-23"), "Oct 23");
assert.equal(dayTime(""), "");
assert.equal(dayTime("soon"), "soon");
// 多本账套：主行写读了几本，每本一行缩进子行（谁、何时；还在读；还没读）
const set0 = month.stages.verify.sets[0];
const two = { ...month, stages: { ...month.stages, verify: { ...month.stages.verify, sets: [set0, { ...set0, key: "s:\\b", label: "B", status: "none", readback: null, requested_by: "", requested_by_name: "", ready_at: "", counts: null }] } } };
assert.equal(stageSummary("verify", two), "1/2 read back");
const subs = [...html(two).matchAll(/data-slot="cc-month-set">([\s\S]*?)<\/div>/g)].map((m) => text(m[1]).join(" · "));
assert.deepEqual(subs, [`ABC 69 · Read back ${dayTime(set0.ready_at)} · Bee`, "B · Not read back"]);
assert.equal(readingNow(two), false);
// 状态点有可读的名字
assert.match(page, /<span class="cc-month-dot" data-state="attention" role="img" aria-label="Needs attention" title="Needs attention"><\/span>/);
// 回读中：写「Reading…」；没有可写的数：写状态词
const reading = { ...month, stages: { ...month.stages, verify: { ...month.stages.verify, sets: [{ ...month.stages.verify.sets[0], newer: "reading" }] } } };
assert.equal(stageSummary("verify", reading), "Reading…");
const quiet = { ...month, stages: { ...month.stages, intake: { ...month.stages.intake, received: 0, no_date: 0, state: "empty" } }, exceptions: { items: [], total: 0, counts: {} } };
assert.ok(text(html(quiet)).slice(0, 2).join("|") === "Intake|Nothing yet");
// 核对、报税给 null（不是 Express / 没有权限）：不列
const limited = { ...month, stages: { ...month.stages, verify: null, tax: null } };
assert.deepEqual(shownStages(limited), ["intake", "entry"]);
assert.deepEqual([...html(limited).matchAll(/data-stage="(\w+)"/g)].map((m) => m[1]), ["intake", "entry"]);
""")

    def test_exceptions_are_grouped_by_severity_with_the_right_actions(self):
        self.run_page(r"""
const page = html(month);
// 分组：要处理 → 看一眼 → 知悉，组内服务端顺序
assert.deepEqual(groupExceptions(month.exceptions.items).map(([s, list]) => [s, list.map((i: any) => i.code)]), [
  ["action", ["task_folder_missing", "intake_file_failed", "choice_needed", "account_tax_mismatch", "not_pushed", "push_failed", "tax_issue"]],
  ["warn", ["express_missing", "tax_draft_stale"]],
  ["info", ["duplicate_number", "not_invoice", "express_changed"]],
]);
assert.deepEqual([...page.matchAll(/data-slot="cc-month-group" data-severity="(\w+)"/g)].map((m) => m[1]), ["action", "warn", "info"]);
assert.ok(text(page).join("|").includes("To do|7"));
assert.equal(attentionCount(month), 9);   // 卡头的数：知悉的不算
// 组标题与卡头用服务端按全部算的数（清单截断了也对）；截断时卡底写还有几条；单据只算了一部分时写一行
const cut = { ...month, truncated: true, exceptions: { ...month.exceptions, items: month.exceptions.items.slice(0, 3), total: 13 } };
assert.equal(attentionCount(cut), 9);
assert.equal(unlisted(cut), 10);
assert.ok(text(html(cut)).join("|").includes("To do|7"));
assert.deepEqual([...html(cut).matchAll(/data-slot="cc-month-note">([\s\S]*?)<\/div>/g)].map((m) => text(m[1]).join("")),
  ["10 more not listed", "Only part of this month's documents was counted"]);
assert.equal(unlisted(month), 0);
assert.ok(!page.includes("cc-month-note"));
// 组标题是语义标题
assert.match(page, /<div class="cc-subheading flex items-center gap-1.5" role="heading" aria-level="3"><span>To do<\/span>/);
// choice_needed：当场选的那一行（待处理的 ChooseRow 行变体），码名 · 单据 · 金额，展开前不取可选项
assert.match(page, /data-slot="cc-pending-choose" data-variant="row"/);
const choose = page.split('data-variant="row"')[1].split("</button>")[0];
assert.match(choose, /class="cc-pending-toggle cc-panel-row" aria-expanded="false"/);
assert.deepEqual(text(choose).slice(1), ["Needs a choice", "INV-h1 · Makro", "1,070.50"]);
assert.equal(g.calls.length, 0);
// 其余单据：交给 CC；任务：打开任务（汇总的写件数）；知悉的整行淡色
const items = [...page.matchAll(/<button type="button" class="cc-panel-row" data-slot="cc-month-item" data-code="(\w+)" data-severity="(\w+)" title="([^"]+)">([\s\S]*?)<\/button>/g)]
  .map((m) => [m[1], m[2], m[3], text(m[4]).join(" · ")]);
// 行尾去处读屏也读得到（sr-only，同 title）；汇总的件数写「N 张」，不和金额混成一列裸数字
assert.deepEqual(items[0], ["task_folder_missing", "action", "Open task", "Folder not found · Buy · Open task"]);
assert.deepEqual(items[1], ["intake_file_failed", "action", "Open task", "File failed · Buy · 2 documents · Open task"]);
assert.deepEqual(items[2], ["account_tax_mismatch", "action", "Ask CC", "Tax ID mismatch · INV-h2 · Makro · 1,070.50 · Ask CC"]);
assert.deepEqual(items.find((i) => i[0] === "express_missing"), ["express_missing", "warn", "Ask CC", "Not found in Express · ABC 69 · 1 document · Ask CC"]);
assert.deepEqual(items.find((i) => i[0] === "tax_draft_stale"), ["tax_draft_stale", "warn", "Ask CC", "Draft out of date · Head office · Ask CC"]);
assert.deepEqual(items.find((i) => i[0] === "tax_issue"), ["tax_issue", "action", "Ask CC", "Draft issue · Past the 6-month claim period · Head office · 1 issue · Ask CC"]);
assert.deepEqual(items.filter((i) => i[1] === "info").map((i) => i[0]), ["duplicate_number", "not_invoice", "express_changed"]);  // 知悉先列 3 条
// 同号的一组只占一行：「单号 · N 张」（测试窗口第 3 轮）
assert.deepEqual(items.find((i) => i[0] === "duplicate_number"), ["duplicate_number", "info", "Ask CC", "Duplicate number · INV-7 · 2 documents · Ask CC"]);
// 每个码都有短名（不显示码本身）
for (const item of month.exceptions.items) assert.notEqual(codeName(item), item.code, item.code);
// 每组先列 8 条，其余「显示更多」
const many = { ...month, exceptions: { ...month.exceptions, items: Array.from({ length: GROUP_SHOWN + 3 }, (_, i) => ({ ...month.exceptions.items[3], document: `d${i}` })) } };
const long = html(many);
assert.equal((long.match(/data-slot="cc-month-item"/g) ?? []).length, GROUP_SHOWN);
assert.ok(text(long).includes("Show more"));
// 知悉的先列 3 条（不把下面的卡挤下去）
assert.equal(GROUP_SHOWN_BY.info, 3);
const fyi = { ...month, exceptions: { ...month.exceptions, items: Array.from({ length: 5 }, (_, i) => ({ ...month.exceptions.items.find((x: any) => x.code === "not_invoice"), task: `t${i}` })) } };
assert.equal((html(fyi).match(/data-slot="cc-month-item"/g) ?? []).length, 3);
""")

    def test_helpers_follow_the_pending_contract(self):
        self.run_page(r"""
const [choice] = month.exceptions.items.filter((i: any) => i.code === "choice_needed");
const item = pendingOf(choice, "COMPANY");
assert.equal(choosable(item), true);
assert.equal(item.code, "choice_needed");
assert.equal(item.reason_code, "choice_needed");
assert.deepEqual(item.choice, { connection: "C1", account_set_key: "S:\\69EXP\\ABC", kinds: ["party"] });
const failed = pendingOf(month.exceptions.items.find((i: any) => i.code === "push_failed"), "COMPANY");
assert.equal(failed.action, "open");
assert.equal(failed.reason_code, "failed");   // 原待处理粗分
// 次要信息：任务名、账套名、草稿分店、单据
const by = (code: string) => month.exceptions.items.find((i: any) => i.code === code);
assert.equal(exceptionSubject(by("task_folder_missing")), "Buy");
assert.equal(exceptionSubject(by("express_changed")), "ABC 69");
assert.equal(exceptionSubject(by("tax_issue"), (b: string) => `#${b}`), "Past the 6-month claim period · #00000");
assert.equal(exceptionSubject(by("duplicate_number")), "INV-7 · Makro");
// 空月：每段 empty、没有异常 → 会话栏不显示；有异常或有一段不空 → 显示
const blank = { ...month, stages: { intake: { ...month.stages.intake, state: "empty" }, entry: { ...month.stages.entry, state: "empty" }, verify: null, tax: { ...month.stages.tax, state: "empty" } },
  exceptions: { items: [], total: 0, counts: {} } };
assert.equal(emptyMonth(blank), true);
assert.equal(emptyMonth(null), true);
assert.equal(emptyMonth(month), false);
assert.equal(emptyMonth({ ...blank, stages: { ...blank.stages, entry: { ...blank.stages.entry, state: "open" } } }), false);
// 读：GET companies.api.month，带公司与月份
g.respond = async () => ({ ok: true, status: 200, json: async () => ({ message: month }) });
const got = await loadMonth("COMPANY", "2026-09");
const asked = g.calls.at(-1);
assert.equal(asked.method, "/api/method/pearnly_cowork.companies.api.month");
assert.deepEqual(asked.args, { company: "COMPANY", period: "2026-09" });
assert.equal(asked.body, null);
assert.equal(got.exceptions.total, 12);
""")

    def test_hand_to_cc_names_documents_by_number_never_by_record(self):
        # 测试窗口第 3 轮：「交给 CC」的预填写了 CWH-…；改成单号 · 对方 · 金额 · 日期，一张 / 几张各一种说法；同号的一组列每一张
        self.run_page(r"""
import { duplicatePrefill } from "@/components/month-ledger";
import { handText } from "@/components/pending";
const dup = month.exceptions.items.find((i: any) => i.code === "duplicate_number");
assert.equal(duplicatePrefill(dup), "Check these documents with the same number: \nINV-7 · Makro · 1,070.50 · 2026-09-20\nINV-7 · Makro · 1,070.50 · 2026-09-20");
assert.ok(!/\b(h3|h7)\b/.test(duplicatePrefill(dup)));
const one = pendingOf(month.exceptions.items.find((i: any) => i.code === "push_failed"), "COMPANY");
assert.equal(handText([one]), `Handle this one: ${[one.invoice_no, one.counterparty, "1,070.50", one.date].join(" · ")}`);
const two = handText([one, { ...one, document: "CWH-0002", invoice_no: "INV-9", amount: "5", date: "" }]);
assert.equal(two, `Handle these: \n${[one.invoice_no, one.counterparty, "1,070.50", one.date].join(" · ")}\nINV-9 · Makro · 5.00`);
assert.ok(!two.includes("CWH-") && !two.includes(`[${one.document}]`));
// 单号、对方、金额、日期都没有的单（推送失败、没识别出来的）写文件名，不静默丢掉（复审第 3 轮）：选 3 张就写 3 张
const bare = { ...one, document: "CWH-0003", invoice_no: "", counterparty: "", amount: null, date: "", filename: "scan-0915.pdf" };
assert.equal(handText([one, bare, { ...bare, document: "CWH-0004", filename: "IMG_2231.jpg" }]).split("\n").length, 4);
assert.equal(handText([bare]), "Handle this one: scan-0915.pdf");
assert.equal(handText([pendingOf({ ...month.exceptions.items.find((i: any) => i.code === "push_failed"), ...bare } as any, "COMPANY")]),
             "Handle this one: scan-0915.pdf");
const blank = { ...dup, invoice_no: "", documents: [{ document: "h3", invoice_no: "INV-7", counterparty: "Makro", amount: "1070.5", date: "2026-09-20" },
  { document: "h9", invoice_no: "", counterparty: "", amount: null, date: "", filename: "dup.pdf" }] };
assert.equal(duplicatePrefill(blank), "Check these documents with the same number: \nINV-7 · Makro · 1,070.50 · 2026-09-20\ndup.pdf");
""")
        self.run_page(r"""
import { duplicatePrefill } from "@/components/month-ledger";
import { handText } from "@/components/pending";
const one = pendingOf(month.exceptions.items.find((i: any) => i.code === "push_failed"), "COMPANY");
assert.ok(handText([one]).startsWith("处理这张："));
assert.ok(handText([one, one]).startsWith("处理这几张：\n"));
assert.ok(duplicatePrefill(month.exceptions.items.find((i: any) => i.code === "duplicate_number")).startsWith("核对这几张同号单据：\n"));
""", lang="zh")

    def test_card_waits_hidden_and_the_company_section_shows_its_month(self):
        self.run_page(r"""
import { MonthCard, MonthSection } from "@/components/month-ledger";
import { SidebarProvider } from "@/components/ui/sidebar";
import { lastMonth, monthLabel } from "@/lib/companies";
const render = (type: any, props: any) => renderToStaticMarkup(createElement(SidebarProvider, null, createElement(type, props)));
// 会话栏：还没取到不占位；对话没定月份不取
assert.ok(!render(MonthCard, { company: "COMPANY", period: "2026-09" }).includes("cc-panel-month"));
assert.ok(!render(MonthCard, { company: "COMPANY", period: "" }).includes("cc-panel-month"));
// 公司页：一直显示，卡头月份胶囊（幽灵按钮）默认上个月，取的时候转圈
const section = render(MonthSection, { company: "COMPANY", period: "", onPeriod: () => {} });
assert.match(section, /data-slot="cc-company-month"/);
assert.ok(text(section).slice(0, 2).join("|") === `Month-end|${monthLabel(lastMonth())}`);
assert.match(section, /class="cc-chin-btn" data-slot="cc-period-chip"/);
assert.match(section, /animate-spin/);
""")

    def test_thai_and_chinese_words(self):
        self.run_page(r"""
const zh = text(html(month));
assert.deepEqual([zh[0], zh[2], zh[4], zh[6]], ["收单", "录账", "核对", "报税"]);
assert.equal(zh[1], "9 张");
assert.equal(zh[3], "已录 2/7 · 仅识别 2");
assert.equal(zh[5], `Bee ${dayTime("2026-10-02T03:01:00Z")} 回读`);
assert.ok(zh.includes("INV-7 · 2 张"));
assert.ok(zh.includes("需处理") && zh.includes("需留意") && zh.includes("知悉"));
assert.ok(zh.includes("要人选") && zh.includes("Express 中找不到"));
""", lang="zh")
        self.run_page(r"""
const th = text(html(month));
assert.deepEqual([th[0], th[2], th[4], th[6]], ["รับเอกสาร", "บันทึกบัญชี", "ตรวจสอบ", "ภาษี"]);
assert.ok(th.includes("ต้องเลือก"));
assert.ok(th.includes("1 ฉบับ"));
""", lang="th")


if __name__ == "__main__":
    unittest.main()
