"""同一网页进程两份 PDF 同时生成不再偶发 500（11-07 清欠测试基线发现，评估 docs/work/2026-11-08-pdf-concurrent-500-评估.md）。

根因：锁定版 Frappe 16.33.0 出 PDF 前用 cssutils 解析整页样式（`frappe.utils.pdf.get_print_format_styles`），框架默认打印字体
（`frappe.www.printview.get_font` 的默认值）少一个引号，每份 PDF 都带这句坏样式。cssutils「出错抛不抛」是进程级开关：每个解析器
建时记下当时的值、解析中改成「不抛」、解析完改回记下的值。默认是「抛」，所以别的线程的解析器先结束把开关改回「抛」时，
正在解析的这一份撞上坏样式就抛 `xml.dom.SyntaxErr` → 500。

修法：共用层 `pearnly_core.desk.print_css.lenient_css_parsing` 在每个请求 / 后台任务开始时把进程级默认改成「不抛」，
之后的解析器记下的、改回的都是「不抛」，开关不再来回翻；生成的 HTML、版面不变。

- 登记：两个钩子都挂上（开发检查也跑）；
- 用锁定版 Frappe 自己的 `get_print_format_styles`、自己的默认字体和 `standard.css` 复现交错（确定性）：不修必抛、修了不抛；
- 真线程并发解析：修了一次都不抛。要 Frappe：生产镜像里跑（候选检查），开发检查跳过。
"""
import ast
import logging
import os
import pathlib
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_core")]
# `frappe.utils.pdf` 导入时给 cssutils 建日志（默认写 ../logs/cssutils.log）；离线检查没有那个目录，改成只打到 stderr
os.environ.setdefault("FRAPPE_STREAM_LOGGING", "1")
HOOK = "pearnly_core.desk.print_css.lenient_css_parsing"


def hook_lists():
    tree = ast.parse((ROOT / "apps/pearnly_core/pearnly_core/hooks.py").read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ("before_request", "before_job"):
                found[node.targets[0].id] = ast.literal_eval(node.value)
    return found


class Registered(unittest.TestCase):
    def test_every_request_and_job_starts_lenient(self):
        lists = hook_lists()
        # 放第一个：后面的 before_request 可能直接跳转 / 抛出，这一步也要先做
        self.assertEqual(lists.get("before_request", [None])[0], HOOK)
        self.assertIn(HOOK, lists.get("before_job", []))


def frappe_pieces():
    try:
        import cssutils
        import jinja2
        from bs4 import BeautifulSoup
        from frappe.utils.pdf import get_print_format_styles
        from frappe.www.printview import get_font
    except ImportError:
        return None
    import frappe

    src = (pathlib.Path(frappe.__file__).parent / "templates/styles/standard.css").read_text(encoding="utf-8")
    env = jinja2.Environment()
    env.filters["flt"] = float
    # 与打印设置字体为 Default 时一样：standard.css 里的 font-family 就是框架默认值
    css = env.from_string(src).render(print_settings={"font_size": 9}, font=get_font(None, None, for_legacy=True))
    page = f"<html><head><style>{css}</style></head><body><div class='print-format'>x</div></body></html>"
    return cssutils, BeautifulSoup, get_print_format_styles, page


class Interleave(logging.Handler):
    """正解析的这份第一次记日志时，让「别的线程」那个更早建好的解析器跑完一遍，把开关改回它记下的值。"""

    def __init__(self, other):
        super().__init__()
        self.other, self.fired = other, False

    def emit(self, record):
        if not self.fired:
            self.fired = True
            self.other.parseString("a { color: red }")


@unittest.skipIf(frappe_pieces() is None, "Frappe not installed")
class ConcurrentPdfStyles(unittest.TestCase):
    def setUp(self):
        self.cssutils, self.soup, self.styles, self.page = frappe_pieces()
        self.saved = self.cssutils.log.raiseExceptions
        self.level = self.cssutils.log._log.level

    def tearDown(self):
        self.cssutils.log.raiseExceptions = self.saved
        self.cssutils.log.setLevel(self.level)

    def interleaved(self):
        cssutils = self.cssutils
        other = cssutils.CSSParser()  # 别的线程：这份开始解析之前就建好了
        handler = Interleave(other)
        cssutils.log._log.addHandler(handler)
        cssutils.log.setLevel(logging.DEBUG)
        try:
            return self.styles(self.soup(self.page, "html5lib"))
        finally:
            cssutils.log._log.removeHandler(handler)
            self.assertTrue(handler.fired, "the stylesheet logged nothing, the interleave never happened")

    def test_the_framework_default_font_is_still_broken(self):
        # 根因还在：默认字体串坏、开关是进程级。哪天升级后这条不再抛，复核还要不要这个钩子
        self.cssutils.log.raiseExceptions = True
        import xml.dom
        with self.assertRaises(xml.dom.SyntaxErr):
            self.interleaved()

    def test_after_the_hook_another_parser_finishing_does_not_make_this_one_raise(self):
        from pearnly_core.desk import print_css

        self.cssutils.log.raiseExceptions = True  # 新进程的默认
        print_css.lenient_css_parsing()
        styles = self.interleaved()
        self.assertIn("font-size", [prop.name for prop in styles])  # .print-format 其余样式照常取到
        self.assertFalse(self.cssutils.log.raiseExceptions)

    def test_real_threads_parsing_together_never_raise(self):
        from pearnly_core.desk import print_css

        self.cssutils.log.raiseExceptions = True
        print_css.lenient_css_parsing()
        self.cssutils.log.setLevel(logging.CRITICAL)
        errors, interval = [], sys.getswitchinterval()
        page = self.page.replace("<style>", "<style>" + "".join(f".c{i} {{ padding: {i}px; }}\n" for i in range(150)), 1)

        def work():
            for _ in range(25):
                try:
                    self.styles(self.soup(page, "html5lib"))
                except Exception as exc:  # noqa: BLE001 - 数下来断言
                    errors.append(repr(exc)[:120])

        sys.setswitchinterval(1e-5)
        try:
            threads = [threading.Thread(target=work) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(interval)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
