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
        self.assertTrue({"title", "docs", "entered", "readBack", "reading", "due", "openTask", "readbackText"} <= sets["en"])
        i18n = read("i18n.ts")
        self.assertIn('stage: { intake: "收单", entry: "录账", verify: "核对", tax: "报税" }', i18n)
        # 已有文案不改：卡片标题仍是「本月」，交给 CC、显示更多沿用
        self.assertIn('thisMonth: "本月"', i18n)
        self.assertIn('handToCC: "交给 CC"', i18n)

    def test_panel_and_company_page_use_the_same_ledger(self):
        panel = read("components/session-panel.tsx")
        self.assertIn("month: <MonthCard company={company} period={period} />,", panel)
        self.assertNotIn("const MonthCard: FC = () => null;", panel)
        page = read("components/company-page.tsx")
        overview = page.split('<div className="cc-company-overview">', 1)[1].split("</div>", 1)[0]
        lines = [line.strip() for line in overview.strip().splitlines()]
        self.assertTrue(lines[0].startswith("<CompanyComposer "), lines)
        self.assertEqual(lines[1], "<MonthSection company={id} />")  # 概览第一节
        ledger = read("components/month-ledger.tsx")
        self.assertIn('action={<PeriodChip value={period} onChange={setPeriod} allowNone={false} look="cc-chin-btn" />}', ledger)
        self.assertIn("useState(() => lastMonth())", ledger)
        # 会话栏：没取到、空月不显示；出错显示原因
        card = ledger.split("export const MonthCard: FC", 1)[1].split("\n};", 1)[0]
        self.assertIn("if (!month && !error) return null;", card)
        self.assertIn("if (month && emptyMonth(month) && !error) return null;", card)
        self.assertIn('<p role="alert"', card)
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
        self.assertIn("useLiveRefresh(reload, { events: ok ? PENDING_EVENTS : [] });", month)
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
import { LedgerBody, GROUP_SHOWN, stageSummary, codeName } from "@/components/month-ledger";
import { choosable } from "@/components/pending";
import { attentionCount, dayShort, emptyMonth, exceptionSubject, groupExceptions, loadMonth, pendingOf, shownStages } from "@/lib/month";

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
assert.equal(stageSummary("entry", month), "2/7 entered");
assert.equal(stageSummary("verify", month), `Read back ${dayShort("2026-10-02T03:01:00Z")}`);
assert.equal(stageSummary("tax", month), "Due Oct 23");
assert.equal(dayShort("2026-10-23"), "Oct 23");
assert.ok(text(page).slice(0, 12).join("|").startsWith("Intake|9 documents|Entry|2/7 entered|Verify|Read back"));
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
  ["info", ["duplicate_number", "duplicate_number", "not_invoice", "express_changed"]],
]);
assert.deepEqual([...page.matchAll(/data-slot="cc-month-group" data-severity="(\w+)"/g)].map((m) => m[1]), ["action", "warn", "info"]);
assert.ok(text(page).join("|").includes("To do|7"));
assert.equal(attentionCount(month.exceptions.items), 9);   // 卡头的数：知悉的不算
// choice_needed：当场选的那一行（待处理的 ChooseRow 行变体），码名 · 单据 · 金额，展开前不取可选项
assert.match(page, /data-slot="cc-pending-choose" data-variant="row"/);
const choose = page.split('data-variant="row"')[1].split("</button>")[0];
assert.match(choose, /class="cc-pending-toggle cc-panel-row" aria-expanded="false"/);
assert.deepEqual(text(choose).slice(1), ["Needs a choice", "INV-h1 · Makro", "1,070.50"]);
assert.equal(g.calls.length, 0);
// 其余单据：交给 CC；任务：打开任务（汇总的写件数）；知悉的整行淡色
const items = [...page.matchAll(/<button type="button" class="cc-panel-row" data-slot="cc-month-item" data-code="(\w+)" data-severity="(\w+)" title="([^"]+)">([\s\S]*?)<\/button>/g)]
  .map((m) => [m[1], m[2], m[3], text(m[4]).join(" · ")]);
assert.deepEqual(items[0], ["task_folder_missing", "action", "Open task", "Folder not found · Buy"]);
assert.deepEqual(items[1], ["intake_file_failed", "action", "Open task", "File failed · Buy · 2"]);
assert.deepEqual(items[2], ["account_tax_mismatch", "action", "Ask CC", "Tax ID mismatch · INV-h2 · Makro · 1,070.50"]);
assert.deepEqual(items.find((i) => i[0] === "express_missing"), ["express_missing", "warn", "Ask CC", "Not found in Express · ABC 69 · 1"]);
assert.deepEqual(items.find((i) => i[0] === "tax_draft_stale"), ["tax_draft_stale", "warn", "Ask CC", "Draft out of date · Head office"]);
assert.deepEqual(items.filter((i) => i[1] === "info").map((i) => i[0]), ["duplicate_number", "duplicate_number", "not_invoice", "express_changed"]);
// 每个码都有短名（不显示码本身）
for (const item of month.exceptions.items) assert.notEqual(codeName(item), item.code, item.code);
// 每组先列 8 条，其余「显示更多」
const many = { ...month, exceptions: { ...month.exceptions, items: Array.from({ length: GROUP_SHOWN + 3 }, (_, i) => ({ ...month.exceptions.items[3], document: `d${i}` })) } };
const long = html(many);
assert.equal((long.match(/data-slot="cc-month-item"/g) ?? []).length, GROUP_SHOWN);
assert.ok(text(long).includes("Show more"));
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
assert.equal(exceptionSubject(by("tax_issue"), (b: string) => `#${b}`), "#00000");
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
assert.equal(got.exceptions.total, 13);
""")

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
const section = render(MonthSection, { company: "COMPANY" });
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
assert.equal(zh[3], "已录 2/7");
assert.ok(zh.includes("要处理") && zh.includes("看一眼") && zh.includes("知悉"));
assert.ok(zh.includes("要人选") && zh.includes("Express 里找不到"));
""", lang="zh")
        self.run_page(r"""
const th = text(html(month));
assert.deepEqual([th[0], th[2], th[4], th[6]], ["รับเอกสาร", "บันทึกบัญชี", "ตรวจสอบ", "ภาษี"]);
assert.ok(th.includes("ต้องเลือก"));
""", lang="th")


if __name__ == "__main__":
    unittest.main()
