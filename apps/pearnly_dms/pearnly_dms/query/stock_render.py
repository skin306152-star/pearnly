# -*- coding: utf-8 -*-
"""库存停放报表的转换件：汇总 Excel（openpyxl）、汇总 PDF 与 LINE 图片（同一份 HTML，DMS 已有的 Playwright Chromium 出）。

不引新依赖、不带字体文件：生产镜像随 Playwright 装了泰文 Loma、文泉驿正黑、IPA 哥特（`frappe.Dockerfile` 的
`playwright install --with-deps chromium`），按语言排在字体栈里。Pillow 画泰文会叠错上下标，所以图片也交给浏览器排版。
"""

from __future__ import annotations

import html
import io
from typing import Dict, List

from pearnly_dms import i18n
from pearnly_dms.query.stock_report import locations_text

ROWS_PER_IMAGE = 40  # 一张图最多 40 行（宽 1080，手机上还看得清）
MAX_IMAGES = 3  # 一次回复最多 5 条：3 张图 + 1 张卡片，留一条余量
IMAGE_WIDTH = 1080
FONTS = '"Loma", "Sarabun", "WenQuanYi Zen Hei", "IPAPGothic", "Noto Sans", sans-serif'


def _t(key: str, **kw) -> str:
    return i18n.t(f"dms.card.report.{key}", lang=i18n.line_lang(), **kw)


def as_of_label(moment) -> str:
    """更新时间：泰文用佛历（与 MR.ERP 导出一致），其它语言用公历。"""
    year = moment.year + (543 if i18n.line_lang() == "th" else 0)
    return f"{moment.day:02d}/{moment.month:02d}/{year} {moment.hour:02d}:{moment.minute:02d}"


def section_title(scope: str, year: str) -> str:
    my = f"{year} MY" if year else ""
    return " ".join(_t("SECTION_" + scope.upper(), my=my).split())


def _lines(summary: Dict, scope: str) -> List[Dict]:
    """把分段汇总摊成行（段头行 + 数据行），出图分页与 Excel 共用同一顺序。"""
    out = []
    for section in summary["sections"]:
        out.append({"kind": "head", "title": section_title(scope, section["year"])})
        out.extend({"kind": "row", **row} for row in section["rows"])
    return out


# 配色照岳父的样表（用户 11-26 看样张后定）：表头与段头亮绿底黑字加粗、数据行隔行浅灰蓝、台数红色加粗居中、
# 底部更新时间与合计蓝色加粗（合计数字红色加粗）。图片、PDF、Excel 三份同一套；手工青色格与蓝字备注不做。
HEAD_FILL = "00FF00"
STRIPE_FILL = "E8EDF3"
COUNT_COLOR = "E00000"
FOOT_COLOR = "0033CC"
_MARK = "\u0000"


def _total_parts(count) -> tuple:
    """「รวม {count} คัน」拆成数字前、数字、数字后三段（数字单独上红色）。"""
    before, _, after = _t("TOTAL", count=_MARK).partition(_MARK)
    return before, str(count), after


def workbook(summary: Dict, scope: str, as_of: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Alignment, Font, PatternFill

    book = Workbook()
    sheet = book.active
    sheet.title = "Stock"
    head_fill = PatternFill("solid", fgColor=HEAD_FILL)
    stripe = PatternFill("solid", fgColor=STRIPE_FILL)
    center = Alignment(horizontal="center")
    sheet.append([_t("TITLE")])
    sheet["A1"].font = Font(bold=True, size=14)
    shade = False
    for line in _lines(summary, scope):
        if line["kind"] == "head":
            sheet.append([line["title"], _t("COL_CODE"), _t("COL_COUNT"), _t("COL_PLACE")])
            for cell in sheet[sheet.max_row]:
                cell.font, cell.fill = Font(bold=True, color="000000"), head_fill
            sheet[sheet.max_row][2].alignment = center
            shade = False
            continue
        sheet.append([line["name"], line["code"], line["count"], locations_text(line["locations"])])
        row = sheet[sheet.max_row]
        if shade:
            for cell in row:
                cell.fill = stripe
        shade = not shade
        row[2].font, row[2].alignment = Font(bold=True, color=COUNT_COLOR), center
    sheet.append([])
    before, number, after = _total_parts(summary["total"])
    blue, red = InlineFont(b=True, color=FOOT_COLOR), InlineFont(b=True, color=COUNT_COLOR)
    sheet.append([CellRichText([TextBlock(blue, before), TextBlock(red, number), TextBlock(blue, after)])])
    sheet.append([_t("AS_OF", time=as_of)])
    sheet[sheet.max_row][0].font = Font(bold=True, color=FOOT_COLOR)
    for column, width in zip("ABCD", (28, 22, 10, 48)):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


_CSS = """
*{box-sizing:border-box}body{margin:0;background:#fff;color:#1f1f1f;font-family:%s;font-size:26px}
.wrap{padding:28px 32px}h1{font-size:32px;margin:0 0 18px}
table{border-collapse:collapse;width:100%%}td,th{border:1px solid #9a9a9a;padding:8px 12px;vertical-align:top}
th{background:#%s;color:#000;text-align:left;font-weight:700}tr.s td{background:#%s}
td.n,th.n{text-align:center;white-space:nowrap}td.n{color:#%s;font-weight:700}
td.c{white-space:nowrap}.foot{margin-top:16px;display:flex;justify-content:space-between;font-weight:700;color:#%s}
.foot b{color:#%s}.more{margin-top:10px;color:#555}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}
@page{size:A4}@media print{body{font-size:12px}.wrap{padding:0 1px}h1{font-size:16px}td,th{padding:4px 6px}
table{table-layout:fixed}col.a{width:26%%}col.b{width:22%%}col.n{width:10%%}td.c{white-space:normal}}
""" % (FONTS, HEAD_FILL, STRIPE_FILL, COUNT_COLOR, FOOT_COLOR, COUNT_COLOR)


def _table(lines: List[Dict]) -> str:
    parts, shade = ["<table><colgroup><col class=a><col class=b><col class=n><col></colgroup>"], False
    for line in lines:
        if line["kind"] == "head":
            parts.append("<tr><th>%s</th><th>%s</th><th class=n>%s</th><th>%s</th></tr>" % (
                html.escape(line["title"]), html.escape(_t("COL_CODE")), html.escape(_t("COL_COUNT")),
                html.escape(_t("COL_PLACE"))))
            shade = False
            continue
        parts.append("<tr%s><td>%s</td><td class=c>%s</td><td class=n>%d</td><td>%s</td></tr>" % (
            " class=s" if shade else "", html.escape(line["name"]), html.escape(line["code"]), line["count"],
            html.escape(locations_text(line["locations"]))))
        shade = not shade
    parts.append("</table>")
    return "".join(parts)


def document(lines: List[Dict], summary: Dict, as_of: str, *, footer: bool = True, more: int = 0) -> str:
    lang = i18n.line_lang()
    body = ["<div class=wrap><h1>%s</h1>" % html.escape(_t("TITLE")), _table(lines)]
    if more:
        body.append("<div class=more>%s</div>" % html.escape(_t("MORE_ROWS", count=more)))
    if footer:
        before, number, after = _total_parts(summary["total"])
        body.append("<div class=foot><span>%s<b>%s</b>%s</span><span>%s</span></div>" % (
            html.escape(before), html.escape(number), html.escape(after), html.escape(_t("AS_OF", time=as_of))))
    body.append("</div>")
    return ("<!doctype html><html lang=%s><head><meta charset=utf-8><style>%s</style></head><body>%s</body></html>"
            % (lang, _CSS, "".join(body)))


def image_pages(summary: Dict, scope: str) -> List[List[Dict]]:
    """出图分页：每页 ≤40 行；换页时把当前段头带到新页顶上（翻到第二张也知道是哪个年款）。最多 3 页。"""
    pages, current, head = [], [], None
    for line in _lines(summary, scope):
        if line["kind"] == "head":
            head = line
            if len(current) >= ROWS_PER_IMAGE - 1:
                pages.append(current)
                current = []
            current.append(line)
            continue
        if len(current) >= ROWS_PER_IMAGE:
            pages.append(current)
            current = [head] if head else []
        current.append(line)
    if current:
        pages.append(current)
    return pages


def render(context, summary: Dict, scope: str, as_of: str) -> Dict:
    """在一个 Playwright 浏览器上下文里出 PDF 与图片（JPEG：文字截图 85 质量约 0.2 MB，LINE 预览图上限 1 MB，原图与预览共用一份）。返回 ``{"pdf": bytes, "images": [bytes], "omitted": 行数}``。"""
    pages = image_pages(summary, scope)
    shown, omitted = pages[:MAX_IMAGES], pages[MAX_IMAGES:]
    omitted_rows = sum(1 for page in omitted for line in page if line["kind"] == "row")
    page = context.new_page()
    page.set_viewport_size({"width": IMAGE_WIDTH, "height": 200})  # 整页截图取内容高，视口小才不留白
    try:
        page.set_content(document(_lines(summary, scope), summary, as_of), wait_until="load")
        pdf = page.pdf(format="A4", print_background=True, margin={"top": "12mm", "bottom": "12mm",
                                                                  "left": "10mm", "right": "10mm"})
        images = []
        for index, lines in enumerate(shown):
            last = index == len(shown) - 1
            page.set_content(document(lines, summary, as_of, footer=last, more=omitted_rows if last else 0),
                             wait_until="load")
            images.append(page.screenshot(full_page=True, type="jpeg", quality=85))
        return {"pdf": pdf, "images": images, "omitted": omitted_rows}
    finally:
        page.close()


__all__ = ["IMAGE_WIDTH", "MAX_IMAGES", "ROWS_PER_IMAGE", "as_of_label", "document", "image_pages", "render",
           "section_title", "workbook"]
