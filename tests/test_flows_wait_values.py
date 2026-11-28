"""流程用例读工具结果，等的是「值已取得到」，不是「某个字段名出现了」（11-12 主会话：重跑才过的不算过）。

假智能体回的「[lab-agent] <工具名> <工具结果原文>」是流式画出来的。11-11 先改成「等回复里出现 "status" 再按正则取字段」，11-12 全套
又偶发两格（cc.operator、cowork.cc.record-reply）：`"status"` 先出现在嵌套的跳过清单里，顶层的值还没画出来；正则取第一处同名字段，
也可能取到嵌套的那个。统一改用 `cc_lib` 的 `toolResult(工具名)`：整段 JSON 完整了才回对象。这里拦两种写法再出现：
- 等某个带引号的字段名出现在文字里（`innerText.includes('"status"')` 一类）；
- 按正则从回复文字里取 `"字段": 值`。

另一组（BusinessMenuFlowTests）：流程里点 CC 输入框上方业务菜单的一项，一律经 ``cc_lib`` 的 ``pickBusiness``（这一排直接摆着就点，没有就开「更多」点里面那一项），
读「菜单里有哪些」一律经 ``menuLabels``（直接摆出的 + 收进「更多」的）。

一排摆几项按真实宽度量（``thread.tsx`` useMenuFit），随字体、语言、视口变：11-28 本机 Mac 真实构建的手机格只摆「采购、销售、更多」，
只在这一排里找「收款」的两条流程（erp.cc.guide.collection / erp.cc.guide.stop）就点不到；GitHub 的 Linux Chrome 上同一格摆得下、照过。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "scripts/flows"
sys.path.insert(0, str(FLOWS))

# JS 里写成 includes('"name"')，在 Python 源码里是 includes('\"name\"') 或 includes('\\"name\\"')
WAIT_FOR_KEY = re.compile(r"""includes\(\s*'\\{1,2}"[A-Za-z_]+\\{1,2}"'\s*\)""")
REGEX_FIELD = re.compile(r"""RegExp\(\s*'\\{1,2}"'?\s*\+|RegExp\(\s*'\\{1,2}"\{""")


class FlowsWaitForValues(unittest.TestCase):
    def offenders(self, pattern):
        return [f"{path.name}:{n}" for path in sorted(FLOWS.glob("*.py"))
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]

    def test_no_flow_waits_for_a_field_name_to_appear(self):
        self.assertEqual(self.offenders(WAIT_FOR_KEY), [], "等 toolResult(工具名) 取到值，不等字段名出现")

    def test_no_flow_reads_fields_by_regex(self):
        self.assertEqual(self.offenders(REGEX_FIELD), [], "用 toolResult(工具名) 的对象取字段，不按正则取第一处同名字段")

    def test_the_patterns_catch_the_old_writing(self):
        old = [r"""await until(()=>!!found()&&found().innerText.includes('\"status\"'),240); """,
               r"""f"const found=()=>x; await until(()=>!!found()&&found().innerText.includes('\\"status\\"'),240); \"""",
               r"""const pick=(k)=>{const m=text.match(new RegExp('\"'+k+'\": ?(\"[^\"]*\"|[0-9]+)'));""",
               r"""return (f"(()=>{{const m=text.match(new RegExp('\"{name}\": ?(\"[^\"]*\"|[0-9]+)'));"""]
        self.assertTrue(WAIT_FOR_KEY.search(old[0]) and WAIT_FOR_KEY.search(old[1]))
        self.assertTrue(REGEX_FIELD.search(old[2]) and REGEX_FIELD.search(old[3]))

    def test_the_helper_is_shared(self):
        lib = (FLOWS / "cc_lib.py").read_text(encoding="utf-8")
        self.assertIn("JS += TOOL_RESULT", lib)
        for name in ("cc_operator.py", "cc_record_reply.py"):
            self.assertIn("toolResult(", (FLOWS / name).read_text(encoding="utf-8"), name)


# 各流程自己在菜单里找按钮的写法（只 cc_lib 里的共用步骤可以有）
OWN_LOOKUP = ("cc-business-more", "button[data-business", "cc-business-measure")

# 假页面：只认 pickBusiness / menuLabels 用到的几个选择器；「更多」收到 pointerdown 才「打开」下拉（同 Radix）
FAKE = r"""
const assert = require("node:assert/strict");
const make = (text, business) => ({ innerText: text, textContent: text, dataset: { business }, clicked: 0, click() { this.clicked++; },
  events: [], dispatchEvent(e) { this.events.push(e.type); if (this.onEvent) this.onEvent(e); return true; } });
function page(direct, hidden, withMore = true) {
  const buttons = Object.fromEntries(direct.map((k) => [k, make(k.toUpperCase(), k)]));
  const items = Object.fromEntries(hidden.map((k) => [k, make(k.toUpperCase(), k)]));
  let open = false;
  const more = withMore && hidden.length ? make("MORE", undefined) : null;
  if (more) more.onEvent = (e) => { if (e.type === "pointerdown") open = true; };
  const ruler = [...direct, ...hidden].map((k) => make(k.toUpperCase(), undefined));
  const row = {
    querySelector(s) {
      let m = /^:scope > button\[data-business="(.+)"\]$/.exec(s);
      if (m) return buttons[m[1]] || null;
      if (s === ':scope > button[data-slot="cc-business-more"]') return more;
      throw new Error("unexpected row selector " + s);
    },
    querySelectorAll(s) {
      if (s === '.cc-business-measure > button:not([data-slot="cc-business-more"])') return ruler;
      throw new Error("unexpected row selectorAll " + s);
    },
  };
  globalThis.document = {
    querySelector(s) {
      if (s === '[data-slot="cc-business-menu"]') return row;
      const m = /^\[data-slot="cc-business-more-menu"\] \[data-business="(.+)"\]$/.exec(s);
      if (m) return open ? items[m[1]] || null : null;
      return null;
    },
    querySelectorAll() { return []; },
  };
  return { buttons, items, more };
}
globalThis.PointerEvent = class { constructor(type) { this.type = type; } };
"""

CASES = r"""
(async () => {
  // 这一排直接摆着：点按钮，不碰「更多」
  let p = page(["purchase", "sales", "collection"], ["pending"]);
  assert.deepEqual(menuLabels(), ["PURCHASE", "SALES", "COLLECTION", "PENDING"]);
  assert.deepEqual(await pickBusiness("collection"), { label: "COLLECTION", via: "row" });
  assert.equal(p.buttons.collection.clicked, 1);
  assert.deepEqual(p.more.events, []);
  // 一排只摆两颗（「收款」收进「更多」）：按开「更多」再点菜单里那一项
  p = page(["purchase", "sales"], ["collection", "pending"]);
  assert.deepEqual(menuLabels(), ["PURCHASE", "SALES", "COLLECTION", "PENDING"]);
  assert.deepEqual(await pickBusiness("collection"), { label: "COLLECTION", via: "more" });
  assert.deepEqual(p.more.events, ["pointerdown"]);
  assert.equal(p.items.collection.clicked, 1);
  // 菜单里没有这一项：什么也不点，回 via ""（流程的判定据此判不通过）
  p = page(["purchase"], []);
  assert.deepEqual(await pickBusiness("collection"), { label: "", via: "" });
  assert.equal(p.buttons.purchase.clicked, 0);
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
"""


class BusinessMenuFlowTests(unittest.TestCase):
    def test_flows_pick_menu_items_through_the_shared_step(self):
        users = []
        for path in sorted(FLOWS.glob("*.py")):
            if path.name == "cc_lib.py":
                continue
            text = path.read_text(encoding="utf-8")
            for needle in OWN_LOOKUP:
                self.assertNotIn(needle, text, f"{path.name}：点业务菜单经 cc_lib 的 pickBusiness / menuLabels，不自己找按钮")
            if "pickBusiness(" in text:
                users.append(path.name)
        # 点菜单项的流程（ERP、Cowork 的 CC 引导类）都在用共用步骤
        for name in ("erp_cc_guide_collection.py", "erp_cc_guide_stop.py", "erp_cc_guide_pending.py",
                     "cowork_cc_guide_readback.py", "cowork_cc_guide_form_lib.py", "cc_guide.py"):
            self.assertIn(name, users)

    def test_judges_require_the_item_to_be_clicked(self):
        for name in ("erp_cc_guide_collection.py", "erp_cc_guide_stop.py"):
            text = (FLOWS / name).read_text(encoding="utf-8")
            self.assertIn("pickBusiness('collection')", text)
            self.assertIn('.get("via") in ("row", "more")', text)

    def test_pick_business_uses_the_row_or_opens_more(self):
        import cc_lib  # noqa: E402  （按需导入：本文件其余用例只读源码）
        start = cc_lib.JS.index("const menuRow=")
        end = cc_lib.JS.index("via:'more'};};", start) + len("via:'more'};};")
        helpers = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,5));} return false;};"
                   "const q=s=>document.querySelector(s);" + cc_lib.JS[start:end])
        out = subprocess.run(["node", "-e", FAKE + helpers + CASES], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ok", out.stdout)

if __name__ == "__main__":
    unittest.main()
