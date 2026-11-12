"""流程用例读工具结果，等的是「值已取得到」，不是「某个字段名出现了」（11-12 主会话：重跑才过的不算过）。

假智能体回的「[lab-agent] <工具名> <工具结果原文>」是流式画出来的。11-11 先改成「等回复里出现 "status" 再按正则取字段」，11-12 全套
又偶发两格（cc.operator、cowork.cc.record-reply）：`"status"` 先出现在嵌套的跳过清单里，顶层的值还没画出来；正则取第一处同名字段，
也可能取到嵌套的那个。统一改用 `cc_lib` 的 `toolResult(工具名)`：整段 JSON 完整了才回对象。这里拦两种写法再出现：
- 等某个带引号的字段名出现在文字里（`innerText.includes('"status"')` 一类）；
- 按正则从回复文字里取 `"字段": 值`。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FLOWS = ROOT / "scripts/flows"

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


if __name__ == "__main__":
    unittest.main()
