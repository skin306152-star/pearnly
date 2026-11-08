"""出 PDF 时解析样式不受别的线程影响（多产品共用：凡是在网页进程 / 后台任务里经框架出 PDF 的入口都靠它，
现在是 ERP 的采购凭证与原生打印页「存 PDF」；评估 docs/work/2026-11-08-pdf-concurrent-500-评估.md）。

锁定版 Frappe 16.33.0 出 PDF 前用 cssutils 解析整页样式（`frappe.utils.pdf.get_print_format_styles`），框架默认打印字体
（`frappe.www.printview.get_font` 的默认值）少一个引号，每份 PDF 都带这句坏样式。cssutils「解析出错是记日志还是抛」是
进程级开关 `cssutils.log.raiseExceptions`：每个解析器建时记下当时的值，解析中改成「不抛」，解析完改回记下的值；默认是「抛」。
所以同一进程两个线程同时解析时，先建的解析器先结束、把开关改回「抛」，后建的那份正撞上坏样式就抛 `xml.dom.SyntaxErr` → 500。

这里在每个请求、每个后台任务开始时把进程级默认改成「不抛」：之后建的解析器记下的、改回的都是「不抛」，开关不再来回翻。
单线程时解析结果与原来一字不差（`parseString` 本来就在「不抛」下解析）；生成的 HTML、版面都不变；不改 Frappe 源码。
"""
import cssutils


def lenient_css_parsing():
    """`before_request` / `before_job` 钩子：进程级默认改成「出错记日志、不抛」（已是就不动）。"""
    if cssutils.log.raiseExceptions:
        cssutils.log.raiseExceptions = False
