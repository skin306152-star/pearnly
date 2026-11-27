# -*- coding: utf-8 -*-
"""库存停放报表的转换件：汇总 Excel（openpyxl）、汇总 PDF 与 LINE 图片（同一份 HTML，DMS 已有的 Playwright Chromium 出）。

版式照岳父的样表 1:1（用户 11-26），每一列表头与数据同一种对齐（车名 / 代码 / 位置靠左、台数居中）：没有表格上方的标题；最上面一行亮绿表头（看起来是样表那样的一整格「รถรุ่น 26 MY ว่าง Model/Color」，实际两格：前半句对齐车名列、
「Model/Color」对齐代码颜色列），后面「จำนวน」「สถานที่จอดรถ」；数据四列（车名 / 代码 颜色 / 台数 / 位置），没有网格线、隔行浅灰底；台数红色加粗；
最底一行蓝色加粗「อัพเดต 日期 เวลา 时间 Total」，合计数字在台数那一列正下方、红色加粗。不按年款分段（用户「做法一」），全表车型
主档年款一致时表头写年款。手工的青色格与蓝字备注不做。图片、PDF、Excel 同一版式。

不引新依赖、不带字体文件：生产镜像随 Playwright 装了泰文 Loma、文泉驿正黑、IPA 哥特（`frappe.Dockerfile` 的
`playwright install --with-deps chromium`），按语言排在字体栈里。Pillow 画泰文会叠错上下标，所以图片也交给浏览器排版。
所有文字按发起人的 LINE 语言（`i18n.line_lang()`；webhook 与后台任务都在 `i18n.line_language(user)` 块里）。
"""

from __future__ import annotations

import html
import io
from typing import Dict, List

from pearnly_dms import i18n
from pearnly_dms.query.stock_report import locations_text

ROWS_PER_IMAGE = 36  # 大字（手机气泡里不点开也看得清）、行距照样表（约 1.3 倍字高）下一张图最多 36 行
MAX_IMAGES = 3  # 一次回复最多 5 条：3 张图 + 1 张卡片，留一条余量
IMAGE_WIDTH = 1080
FONTS = '"Loma", "Sarabun", "WenQuanYi Zen Hei", "IPAPGothic", "Noto Sans", sans-serif'
HEAD_FILL = "00FF00"
STRIPE_FILL = "E9ECEF"
COUNT_COLOR = "E00000"
FOOT_COLOR = "0033CC"
NAME_COLOR = "333333"


def _t(key: str, **kw) -> str:
    return i18n.t(f"dms.card.report.{key}", lang=i18n.line_lang(), **kw)


def as_of_label(moment) -> Dict[str, str]:
    """更新日期与时间：四语都用公历（照样表「อัพเดต 20/11/2026 …」，用户 11-26 定）；泰文时间照样表用点（16.53），其它用冒号。"""
    thai = i18n.line_lang() == "th"
    return {"date": f"{moment.day:02d}/{moment.month:02d}/{moment.year}",
            "time": f"{moment.hour:02d}{'.' if thai else ':'}{moment.minute:02d}"}


def header_text(scope: str, year: str) -> tuple:
    """表头前两格（用户 11-26：表头两部分各自对齐各自的列）：前半句对齐车名列——空车档「รถรุ่น 26 MY ว่าง」，全部 / 未分配团队档
    不写「ว่าง」，年款不一致或没填时不写年款，四种组合都不为空；后半句「Model/Color」对齐代码颜色列。"""
    key = "HEAD_AVAILABLE" if scope == "available" else "HEAD_OTHER"
    return " ".join(_t(key, my=f"{year} MY" if year else "").split()), _t("HEAD_CODE")


def footer_text(as_of: Dict[str, str]) -> str:
    return " ".join(_t("FOOT", date=as_of["date"], time=as_of["time"]).split())


def workbook(summary: Dict, scope: str, as_of: Dict[str, str]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    book = Workbook()
    sheet = book.active
    sheet.title = "Stock"
    size = 12
    # 每一列表头与数据同一种对齐（用户 11-26「表头和内容要对齐」）：车名 / 代码 / 位置靠左，台数居中
    center = Alignment(horizontal="center", vertical="center")
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)
    sheet.append([*header_text(scope, summary["year"]), _t("COL_COUNT"), _t("COL_PLACE")])  # A1、B1 各一半，同绿底无边框
    for cell in sheet[1]:
        cell.font = Font(bold=True, size=size, color="000000")
        cell.fill = PatternFill("solid", fgColor=HEAD_FILL)
        cell.alignment = left
    sheet["C1"].alignment = center
    stripe = PatternFill("solid", fgColor=STRIPE_FILL)
    for index, line in enumerate(summary["rows"]):
        sheet.append([line["name"], line["code"], line["count"], locations_text(line["locations"])])
        row = sheet[sheet.max_row]
        for cell in row:
            cell.font = Font(size=size, color=NAME_COLOR)
            cell.alignment = left
            if index % 2:
                cell.fill = stripe
        row[2].font = Font(bold=True, size=size + 1, color=COUNT_COLOR)
        row[2].alignment = center
        row[3].font = Font(size=size, color="000000")
    sheet.append([footer_text(as_of), None, summary["total"], None])
    footer = sheet.max_row
    sheet.merge_cells(f"A{footer}:B{footer}")
    sheet.cell(footer, 1).font = Font(bold=True, size=size + 1, color=FOOT_COLOR)
    sheet.cell(footer, 1).alignment = left
    sheet.cell(footer, 3).font = Font(bold=True, size=size + 1, color=COUNT_COLOR)
    sheet.cell(footer, 3).alignment = center
    for column, width in zip("ABCD", (34, 18, 10, 44)):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


_CSS = """
*{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0;background:#fff;color:#%(name)s;font-family:%(fonts)s;font-size:34px}
table{border-collapse:collapse;width:100%%;table-layout:fixed}
col.a{width:30%%}col.b{width:19%%}col.n{width:11%%}
th,td{border:0;padding:4px 14px;line-height:1.25;vertical-align:middle}
th{background:#%(head)s;color:#000;font-weight:700;text-align:left}th.n{text-align:center}
td{overflow-wrap:anywhere}
tr.s td{background:#%(stripe)s}
td.n{text-align:center;color:#%(count)s;font-weight:700;font-size:38px;line-height:1.1}
td.c{white-space:nowrap}td.p{color:#000}td.p span{white-space:nowrap}
tr.f td{color:#%(foot)s;font-weight:700;background:#fff}tr.f td.n{color:#%(count)s}
.more{padding:8px 14px;color:#555;font-size:26px}
@page{size:A4;margin:12mm 10mm}
@media print{body{font-size:13px}th,td{padding:2px 8px}td.n{font-size:14px}.more{font-size:11px}}
""" % {"name": NAME_COLOR, "fonts": FONTS, "head": HEAD_FILL, "stripe": STRIPE_FILL, "count": COUNT_COLOR,
       "foot": FOOT_COLOR}


def document(rows: List[Dict], summary: Dict, scope: str, as_of: Dict[str, str], *, footer: bool = True,
             more: int = 0, first_index: int = 0) -> str:
    """一页（图片）或全表（PDF）：表头在 <thead>，PDF 跨页时每页顶上重复；``first_index``：本页第一行在全表里的序号（隔行底色接得上）。"""
    parts = ["<table><colgroup><col class=a><col class=b><col class=n><col></colgroup><thead><tr>"
             "<th>%s</th><th>%s</th><th class=n>%s</th><th>%s</th></tr></thead><tbody>" % (
                 *(html.escape(part) for part in header_text(scope, summary["year"])), html.escape(_t("COL_COUNT")),
                 html.escape(_t("COL_PLACE")))]
    for offset, line in enumerate(rows):
        parts.append("<tr%s><td>%s</td><td class=c>%s</td><td class=n>%d</td><td class=p>%s</td></tr>" % (
            " class=s" if (first_index + offset) % 2 else "", html.escape(line["name"]), html.escape(line["code"]),
            line["count"], _places_html(line["locations"])))
    if footer:
        parts.append("<tr class=f><td colspan=2>%s</td><td class=n>%d</td><td></td></tr>" % (
            html.escape(footer_text(as_of)), summary["total"]))
    parts.append("</tbody></table>")
    if more:
        parts.append("<div class=more>%s</div>" % html.escape(_t("MORE_ROWS", count=more)))
    return ("<!doctype html><html lang=%s><head><meta charset=utf-8><style>%s</style></head><body>%s</body></html>"
            % (i18n.line_lang(), _CSS, "".join(parts)))


def _places_html(locations) -> str:
    """地点列：一个地点（名 + 台数）不在中间断行，只在「/」处换行——真实地点名很长（「ระยอง-สำนักงานใหญ่」），
    任意断行会把一格挤成三行、名字和台数拆开（11-27 用线上原件出图发现）。"""
    return " / ".join("<span>%s</span>" % html.escape(f"{place} {count}") for place, count in locations)


def image_pages(summary: Dict) -> List[List[Dict]]:
    """出图分页：每页 ≤36 行，每页顶上各有同一行表头（分页不是分段），底部那行只在最后一张。"""
    rows = summary["rows"]
    return [rows[start:start + ROWS_PER_IMAGE] for start in range(0, len(rows), ROWS_PER_IMAGE)] or [[]]


def render(context, summary: Dict, scope: str, as_of: Dict[str, str]) -> Dict:
    """在一个 Playwright 浏览器上下文里出 PDF 与图片（JPEG 85：约 0.2–0.4 MB，LINE 预览图上限 1 MB，原图与预览共用一份）。
    返回 ``{"pdf": bytes, "images": [bytes], "omitted": 行数}``。"""
    pages = image_pages(summary)
    shown = pages[:MAX_IMAGES]
    omitted = sum(len(page) for page in pages[MAX_IMAGES:])
    page = context.new_page()
    page.set_viewport_size({"width": IMAGE_WIDTH, "height": 200})  # 整页截图取内容高，视口小才不留白
    try:
        page.set_content(document(summary["rows"], summary, scope, as_of), wait_until="load")
        pdf = page.pdf(format="A4", print_background=True)
        images = []
        for index, rows in enumerate(shown):
            last = index == len(shown) - 1
            page.set_content(document(rows, summary, scope, as_of, footer=last, more=omitted if last else 0,
                                      first_index=index * ROWS_PER_IMAGE), wait_until="load")
            images.append(page.screenshot(full_page=True, type="jpeg", quality=85))
        return {"pdf": pdf, "images": images, "omitted": omitted}
    finally:
        page.close()


__all__ = ["IMAGE_WIDTH", "MAX_IMAGES", "ROWS_PER_IMAGE", "as_of_label", "document", "footer_text", "header_text",
           "image_pages", "render", "workbook"]
