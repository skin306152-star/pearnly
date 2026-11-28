"""CC 引导卡的几种输入：金额多选、分摊、月份 / 日期 / 数字 / 文字（``frontend/assistant/src/lib/guide-inputs.ts``；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2，P0 第 9 个提交的逻辑部分）。

断言的是应有状态：
- 金额按「分」的整数算：0.10 + 0.20 就是 0.30；认千分位逗号；负数、三位小数、字母不是金额；
- 金额多选：勾上一行填它的默认数（发票的未收余额），取消就去掉，行的顺序照选项；一行都没勾、有一行不是正数、超过这一行
  最多能填的数，各说得出是哪一种、哪一行；
- 分摊：各项合计等于总额才算对，空着的项不算；还差多少算得出；「余下的都算这种」把差额整笔填上，分多了不动；
- 交回服务端的行只带填了的、金额两位小数（与服务端 ``steps.value_problem`` 收的形状一致）；
- 一格输入：月份 YYYY-MM、日期要真有那一天、数字、文字不空且不超长。

    python3 tests/test_cc_guide_inputs.py
"""
import sys
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, ROOT + "/tests")
sys.path.insert(0, ROOT + "/apps/pearnly_core")

from test_assistant_ui import run_ts  # noqa: E402

from pearnly_core.assistant.guide import steps  # noqa: E402

LOGIC = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { amountsProblem, defaultAmount, fillRest, fromMinor, lockedRows, rowsToSend, setAmount, splitProblem, splitRemainder, sumMinor, toMinor,
  toggleRow, valueOk, TEXT_MAX } from "@/lib/guide-inputs";

// 金额按分的整数算
assert.equal(toMinor("1,250.50"), 125050n);
assert.equal(toMinor(" 7 "), 700n);
assert.equal(toMinor("0.5"), 50n);
for (const bad of ["", "-5", "1.234", "abc", "1e3", "1,2,3.4.5", ".5", null, undefined]) assert.equal(toMinor(bad as any), null, String(bad));
assert.equal(fromMinor(125050n), "1250.50");
assert.equal(fromMinor(5n), "0.05");
assert.equal(fromMinor(-150n), "-1.50");
assert.equal(fromMinor(sumMinor([{ code: "a", amount: "0.10" }, { code: "b", amount: "0.20" }])), "0.30");

// 金额多选
const invoices = [{ code: "IV001", amount: "1000", max: "1000" }, { code: "IV002", amount: "250.5", max: "250.50" }, { code: "IV003" }];
assert.equal(defaultAmount(invoices[1]), "250.50");
assert.equal(defaultAmount(invoices[2]), "");
let rows = toggleRow(invoices, [], "IV002", true);
rows = toggleRow(invoices, rows, "IV001", true);
assert.deepEqual(rows, [{ code: "IV001", amount: "1000.00" }, { code: "IV002", amount: "250.50" }]);   // 顺序照选项，不照勾的先后
assert.deepEqual(toggleRow(invoices, rows, "IV001", false), [{ code: "IV002", amount: "250.50" }]);
assert.deepEqual(toggleRow(invoices, rows, "NOPE", true), rows);
assert.deepEqual(amountsProblem(invoices, []), { problem: "empty" });
assert.deepEqual(amountsProblem(invoices, rows), { problem: "" });
assert.deepEqual(amountsProblem(invoices, setAmount(rows, "IV002", "0")), { problem: "amount", code: "IV002" });
assert.deepEqual(amountsProblem(invoices, setAmount(rows, "IV002", "x")), { problem: "amount", code: "IV002" });
assert.deepEqual(amountsProblem(invoices, setAmount(rows, "IV001", "1000.01")), { problem: "over", code: "IV001" });
assert.deepEqual(amountsProblem(invoices, setAmount(rows, "IV001", "999.99")), { problem: "" });
assert.deepEqual(setAmount(rows, "IV003", "5"), rows);                                                // 没勾的行不变

// 分摊
const blank = [{ code: "cash", amount: "" }, { code: "transfer", amount: "" }, { code: "discount", amount: "" }];
assert.equal(splitProblem(blank, "1250.50"), "empty");
// 应分不是正数（收款抵减单抵平 0、抵过头为负：差额退现金）：一项不填就能交；没给合计照旧算空
assert.equal(splitProblem(blank, "0.00"), "");
assert.equal(splitProblem(blank, "-37.00"), "");
assert.equal(splitProblem(blank, ""), "empty");
let split = setAmount(blank, "cash", "250.50");
assert.equal(splitProblem(split, "1250.50"), "mismatch");
assert.equal(splitRemainder(split, "1250.50"), 100000n);
split = fillRest(split, "1250.50", "transfer");
assert.deepEqual(split[1], { code: "transfer", amount: "1000.00" });
assert.equal(splitProblem(split, "1250.50"), "");
assert.equal(splitProblem(setAmount(split, "discount", "0"), "1250.50"), "amount");
assert.equal(splitProblem(setAmount(split, "cash", "300"), "1250.50"), "mismatch");
assert.equal(splitRemainder(setAmount(split, "cash", "300"), "1250.50"), -4950n);
assert.deepEqual(fillRest(setAmount(split, "cash", "2000"), "1250.50", "discount")[2], { code: "discount", amount: "" });   // 分多了不动
assert.deepEqual(fillRest(setAmount(blank, "cash", "abc"), "1250.50", "transfer")[1], { code: "transfer", amount: "" });
assert.deepEqual(rowsToSend(split), [{ code: "cash", amount: "250.50" }, { code: "transfer", amount: "1000.00" }]);
assert.deepEqual(rowsToSend([{ code: "cash", amount: "1,250.5" }]), [{ code: "cash", amount: "1250.50" }]);

// max_rows（wire 50ba9dfb9，MR.ERP 账本一笔只收一种方式）：不带 free 的非零行满了，其余不带 free 的行锁上；free（折扣）不占名额、不锁；
// 0 与没填的不算名额；清空那一行就放开；没给 max_rows 不锁
const ways = [{ code: "cash" }, { code: "transfer" }, { code: "cheque" }, { code: "discount", free: true }];
assert.deepEqual([...lockedRows(ways, [{ code: "cash", amount: "600" }], 1)].sort(), ["cheque", "transfer"]);
assert.deepEqual([...lockedRows(ways, [{ code: "cash", amount: "600" }, { code: "discount", amount: "10" }], 1)].sort(), ["cheque", "transfer"]);
assert.equal(lockedRows(ways, [{ code: "discount", amount: "10" }, { code: "cash", amount: "0" }, { code: "transfer", amount: "" }], 1).size, 0);
assert.equal(lockedRows(ways, [{ code: "cash", amount: "" }], 1).size, 0);
assert.equal(lockedRows(ways, [{ code: "cash", amount: "600" }], 2).size, 0);
assert.deepEqual([...lockedRows(ways, [{ code: "cash", amount: "600" }, { code: "transfer", amount: "5" }], 2)], ["cheque"]);
assert.equal(lockedRows(ways, [{ code: "cash", amount: "600" }]).size, 0);

// 一格输入
assert.ok(valueOk("month", "2026-11") && !valueOk("month", "2026-13") && !valueOk("month", "2026-1"));
assert.ok(valueOk("date", "2026-11-14") && valueOk("date", "2028-02-29"));
assert.ok(!valueOk("date", "2026-02-30") && !valueOk("date", "14/11/2026") && !valueOk("date", ""));
assert.ok(valueOk("number", "12.50") && valueOk("number", "-3") && !valueOk("number", "twelve") && !valueOk("number", ""));
assert.ok(valueOk("text", "ABC Co., Ltd.") && !valueOk("text", "   ") && !valueOk("text", "x".repeat(TEXT_MAX + 1)));
console.log(JSON.stringify({ sent: rowsToSend(split), amounts: rows }));
(globalThis as any).done();
"""


class GuideInputTests(unittest.TestCase):
    def test_money_rows_split_and_single_values(self):
        out = run_ts(LOGIC)
        self.assertIn('"sent"', out)

    def test_what_the_page_sends_is_what_the_server_accepts(self):
        """页面交回的行（上面那段脚本打印的）过服务端同一步的形状核对；页面判不对的值服务端也不收。"""
        import json

        found = json.loads([line for line in run_ts(LOGIC).splitlines() if line.startswith("{")][-1])
        self.assertEqual(steps.value_problem({"key": "method", "input": "split"}, found["sent"]), "")
        self.assertEqual(steps.value_problem({"key": "invoices", "input": "amounts"}, found["amounts"]), "")
        for bad in ([], [{"code": "cash", "amount": "0"}], [{"code": "cash", "amount": "-1"}]):
            self.assertNotEqual(steps.value_problem({"key": "method", "input": "split"}, bad), "")
        self.assertEqual(steps.value_problem({"key": "m", "input": "month"}, "2026-11"), "")
        self.assertNotEqual(steps.value_problem({"key": "m", "input": "month"}, "2026-13"), "")
        self.assertNotEqual(steps.value_problem({"key": "d", "input": "date"}, "2026-02-30"), "")

    def test_the_components_use_the_standard_kit_and_the_four_languages(self):
        src = Path(ROOT) / "frontend/assistant/src"
        code = (src / "components/tool-uis/guide-inputs.tsx").read_text(encoding="utf-8")
        for part in ('from "@/components/ui/item"', 'from "@/components/ui/checkbox"', 'from "@/components/ui/input"',
                     'from "@/components/ui/button"', "aria-invalid", "toggleRow", "fillRest"):
            self.assertIn(part, code)
        # max_rows：多选金额锁没勾的行（已勾的还能取消），分摊锁还空着的格并收起「填入差额」；三处入口都把题上的 max_rows 交进来
        self.assertIn("const off = disabled || (!checked && locked.has(option.code));", code)
        self.assertIn('const off = disabled || (value.trim() === "" && locked.has(option.code));', code)
        self.assertIn('{value.trim() === "" && rest > 0n && !off ? (', code)
        self.assertEqual((src / "components/tool-uis/guide-run-card.tsx").read_text(encoding="utf-8").count("maxRows={q.max_rows}"), 3)
        # query（Cowork 引导第二线 11-19）：带搜索的单选题，搜索框初值；服务端已按它搜好第一页，页面不再搜一遍；只是初值，不当答案、不勾选
        self.assertIn('const [txt, setTxtRaw] = useState(query);', code)
        self.assertIn("if (!txt || (query && txt === query)) return setPage({ options: [...first], more: firstMore });", code)
        self.assertIn("const found = useOptions(options, more, search, picked, query);", code)
        card = (src / "components/tool-uis/guide-run-card.tsx").read_text(encoding="utf-8")
        self.assertIn("query={q.query} onChange={onChange} />", card)
        self.assertNotIn("value={q.query", card)
        # 约定第 3 条（docs/work/2026-11-19-cc-guide-page-endpoints.md）：带 more 的选项题「显示更多」去服务端取下一页（guide_search 任何选项步骤都给）；
        # 搜索框只在声明了 search 的题上出
        self.assertIn("const search = q.search || q.more ? (txt: string, start: number) => searchGuide(run, q.key, txt, start) : undefined;", card)
        self.assertIn("const box = !!q.search;", card)
        self.assertEqual(code.count("{search && searchBox && <SearchBox value={found.txt}"), 2)
        for raw in ("<input", "<button", "<table", "radix-ui"):          # 不手写通用控件、不直接引底层件
            self.assertNotIn(raw, code)
        i18n = (src / "i18n.ts").read_text(encoding="utf-8")
        block = i18n[i18n.index("const GUIDE = {"):i18n.index("export const guide =")]
        for key in ("total:", "remaining:", "over:", "fillRest:", " amount:"):   # 「 amount:」带空格：不数进 (amount: string) 的参数
            self.assertEqual(block.count(key), 4, key)                    # 四语各一条
        self.assertIn('"guide-inputs"', (src / "kit/main.tsx").read_text(encoding="utf-8"))
        self.assertIn("--cc-guide-amount-w:", (src / "globals.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
