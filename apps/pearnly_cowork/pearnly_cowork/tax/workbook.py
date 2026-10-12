"""报税草稿工作簿（openpyxl）：ภ.พ.30 + รายงานภาษีขาย / ซื้อ + 核查；ใบแนบ ภ.ง.ด.53 / ภ.ง.ด.3 + 待核。

外观用对账工作簿的统一常量（`reconciliation.export.theme`）：Arial、深色表头、浅灰强调行、三色状态、#,##0.00。
表名、栏名全走词条（泰文是税务局表样原词）；金额写数值，日期写佛历「日/月/年」。只从草稿摘要排版，不再算数。
"""
from decimal import Decimal
from io import BytesIO

from frappe import _
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from pearnly_cowork.reconciliation.export import theme
from pearnly_cowork.tax import periods

LEVEL_STATUS = {"error": "miss", "warn": "warn"}
LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}
DETAIL_KEYS = ("invoice_month", "last_month", "claimed_month", "expected_vat", "rate", "expected", "buyer_tax_id",
               "company_tax_id", "document_type", "first_row", "of", "counterparty")
_LINE = Side(style="thin", color=theme.LINE)
_BORDER = Border(left=_LINE, right=_LINE, top=_LINE, bottom=_LINE)


class _Sheet:
    def __init__(self, book, title, lang, first=False):
        self.lang = lang
        self.ws = book.active if first else book.create_sheet()
        self.ws.title = self.t(title)[:31]
        self.ws.sheet_view.showGridLines = False
        self.row = 0

    def t(self, key) -> str:
        return _(key, lang=self.lang)

    def _cell(self, column, value, *, money=False, bold=False, fill=None, color=None, align=None):
        cell = self.ws.cell(self.row, column)
        if money and value not in (None, ""):
            cell.value = float(Decimal(str(value)))
            cell.number_format = theme.MONEY
        else:
            cell.value = value
        cell.font = Font(name=theme.FONT, size=10, bold=bold, color=color or theme.TEXT)
        if fill:
            cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal=align or ("right" if money else "left"), vertical="center")
        cell.border = _BORDER
        return cell

    def title(self, text):
        self.row += 1
        cell = self.ws.cell(self.row, 1, text)
        cell.font = Font(name=theme.FONT, size=12, bold=True, color=theme.TEXT)
        self.ws.row_dimensions[self.row].height = theme.TITLE_HEIGHT

    def blank(self):
        self.row += 1

    def pair(self, key, value):
        self.row += 1
        self._cell(1, self.t(key), bold=True, fill=theme.EMPHASIS_BG)
        self._cell(2, value)
        self.ws.row_dimensions[self.row].height = theme.ROW_HEIGHT

    def header(self, keys):
        self.row += 1
        for column, key in enumerate(keys, 1):
            cell = self._cell(column, self.t(key) if key else "", bold=True, fill=theme.HEADER_BG, color=theme.WHITE,
                              align="center")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        self.ws.row_dimensions[self.row].height = theme.HEADER_HEIGHT * 2

    def section(self, key, width):
        self.row += 1
        for column in range(1, width + 1):
            self._cell(column, self.t(key) if column == 1 else None, bold=True, fill=theme.EMPHASIS_BG)

    def line(self, values, money=(), emphasis=False, status=None, status_column=None):
        """一行：values 按列；money 是金额列（1 起）；status 是 error / warn 时给 status_column 染色。"""
        self.row += 1
        for column, value in enumerate(values, 1):
            fill, color = (theme.EMPHASIS_BG, None) if emphasis else (None, None)
            if status in LEVEL_STATUS and column == status_column:
                fill, color = theme.STATUS[LEVEL_STATUS[status]]
            self._cell(column, value, money=column in money, bold=emphasis, fill=fill, color=color)
        self.ws.row_dimensions[self.row].height = theme.ROW_HEIGHT

    def widths(self, values):
        for column, width in enumerate(values, 1):
            self.ws.column_dimensions[get_column_letter(column)].width = min(width, theme.MAX_WIDTH)


def _save(book) -> bytes:
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _issue_text(sheet, issue) -> str:
    """问题名；配不上的票与凭证后面带上可能是同一笔的记录名。"""
    text = sheet.t("cowork.tax.issue." + issue["code"])
    return f"{text} ({', '.join(issue['candidates'])})" if issue.get("candidates") else text


def _month(value) -> str:
    """「2026-09」写成佛历「09/2569」；不是月份原样。"""
    try:
        return periods.be_month(*periods.parse_period(value)) if len(str(value or "")) == 7 else str(value or "")
    except ValueError:
        return str(value or "")


def _detail(sheet, detail) -> str:
    parts = []
    for key in DETAIL_KEYS:
        if detail and detail.get(key) not in (None, ""):
            parts.append(f"{sheet.t('cowork.tax.detail.' + key)}: {_month(detail[key])}")
    return "; ".join(parts)


def _worst(issues):
    levels = [item["level"] for item in issues or []]
    return min(levels, key=lambda level: LEVEL_ORDER.get(level, 3)) if levels else ""


# ---------------- ภ.พ.30 ----------------

def _vat_report(book, lang, rows, issues_by_row, party_key, title_key):
    sheet = _Sheet(book, title_key, lang)
    sheet.header(["cowork.tax.col.seq", "cowork.tax.col.date", "cowork.tax.col.invoice", "cowork.tax.col.document",
                  party_key, "cowork.tax.head.tax_id", "cowork.tax.col.place", "cowork.tax.col.value",
                  "cowork.tax.col.vat", "cowork.tax.col.check"])
    amount, vat = Decimal(0), Decimal(0)
    for seq, row in enumerate(rows, 1):
        found = issues_by_row.get(row["index"]) or []
        worst = _worst(found)
        checks = ", ".join(dict.fromkeys(_issue_text(sheet, item) for item in found))
        sheet.line([seq, periods.be(row["tax_date"] or row["doc_date"]), row["refnum"], row["docnum"],
                    " ".join(part for part in (row.get("prefix"), row["name"]) if part), row["tax_id"], row["branch"],
                    row["amount"], row["vat"], checks], money=(8, 9), status=worst, status_column=10)
        amount += Decimal(row["amount"])
        vat += Decimal(row["vat"])
    sheet.line([sheet.t("cowork.tax.total"), "", "", "", "", "", "", str(amount), str(vat), ""], money=(8, 9),
               emphasis=True)
    sheet.widths([8, 12, 18, 14, 36, 16, 10, 16, 16, 36])
    return {row["index"]: seq for seq, row in enumerate(rows, 1)}


def vat_book(summary, lang) -> bytes:
    book = Workbook()
    sheet = _Sheet(book, "cowork.tax.sheet.pp30", lang, first=True)
    company = summary.get("company") or {}
    year, month = periods.parse_period(summary["period"])
    sheet.title(f"{sheet.t('cowork.tax.sheet.pp30')} {company.get('name') or ''}".strip())
    sheet.pair("cowork.tax.head.operator", company.get("name") or "")
    sheet.pair("cowork.tax.head.tax_id", company.get("tax_id") or "")
    sheet.pair("cowork.tax.head.branch", company.get("branch") or "")
    sheet.pair("cowork.tax.head.tax_month", periods.be_month(year, month))
    sheet.pair("cowork.tax.head.read_at", (summary.get("readback") or {}).get("read_at") or "")
    sheet.pair("cowork.tax.head.due_paper", periods.be((summary.get("deadlines") or {}).get("paper")))
    sheet.pair("cowork.tax.head.due_online", periods.be((summary.get("deadlines") or {}).get("efiling")))
    sheet.blank()
    sheet.header(["cowork.tax.col.item", "cowork.tax.col.description", "cowork.tax.col.amount"])
    boxes = summary.get("boxes") or {}
    for number in range(1, 17):
        key = str(number)
        sheet.line([number, sheet.t(f"cowork.tax.pp30.box.{key}"), boxes.get(key) or None], money=(3,),
                   emphasis=key in ("11", "12") and bool(boxes.get(key)) and Decimal(boxes[key]) > 0)
    sheet.blank()
    sheet.line(["", sheet.t("cowork.tax.pp30.claimable"), (summary.get("claimable_if_fixed") or {}).get("vat")],
               money=(3,))
    if summary.get("previous_excess"):
        sheet.line(["", sheet.t("cowork.tax.pp30.previous_excess"), summary["previous_excess"]], money=(3,))
    sheet.widths([12, 64, 18])

    issues = summary.get("issues") or []
    by_row = {}
    for item in issues:
        if item.get("index"):
            by_row.setdefault(item["index"], []).append(item)
    sales_seq = _vat_report(book, lang, summary.get("sales_rows") or [], by_row, "cowork.tax.col.buyer",
                            "cowork.tax.sheet.sales")
    purchase_seq = _vat_report(book, lang, summary.get("purchase_rows") or [], by_row, "cowork.tax.col.seller",
                               "cowork.tax.sheet.purchases")

    check = _Sheet(book, "cowork.tax.sheet.check", lang)
    check.header(["cowork.tax.col.level", "cowork.tax.col.issue", "cowork.tax.col.side", "cowork.tax.col.seq",
                  "cowork.tax.col.date", "cowork.tax.col.invoice", "cowork.tax.col.document", "cowork.tax.col.name",
                  "cowork.tax.head.tax_id", "cowork.tax.col.value", "cowork.tax.col.vat", "cowork.tax.head.tax_month",
                  "cowork.tax.col.detail", "cowork.tax.col.source"])
    for item in sorted(issues, key=lambda entry: (LEVEL_ORDER.get(entry["level"], 3), entry.get("side", ""),
                                                  entry.get("index", 0))):
        side = item.get("side") or ""
        seq = (sales_seq if side == "S" else purchase_seq).get(item.get("index"), "")
        check.line([check.t("cowork.tax.level." + item["level"]), _issue_text(check, item),
                    check.t("cowork.tax.sheet.sales" if side == "S" else "cowork.tax.sheet.purchases") if side else "",
                    seq, periods.be(item.get("date")), item.get("refnum", ""), item.get("docnum", ""),
                    item.get("name", ""), item.get("tax_id", ""), item.get("amount") or None, item.get("vat") or None,
                    _month(item.get("period")), _detail(check, item.get("detail")), item.get("history", "")],
                   money=(10, 11), status=item["level"], status_column=1)
    check.widths([12, 30, 16, 8, 12, 18, 14, 30, 16, 14, 14, 10, 40, 20])
    return _save(book)


# ---------------- ภ.ง.ด.3 / ภ.ง.ด.53 ----------------

def _income(sheet, line) -> str:
    if line.get("income_type") == "other" and line.get("income_text"):
        return line["income_text"]
    return sheet.t("cowork.tax.income." + line["income_type"]) if line.get("income_type") else ""


def _attachment(book, lang, form, data):
    sheet = _Sheet(book, f"cowork.tax.sheet.{form}", lang)
    names = (["cowork.tax.col.title", "cowork.tax.col.first_name", "cowork.tax.col.last_name"] if form == "pnd3"
             else ["cowork.tax.col.payee_name"])
    sheet.header(["cowork.tax.col.sheet", "cowork.tax.col.seq", "cowork.tax.col.payee_tax_id", "cowork.tax.head.branch",
                  *names, "cowork.tax.col.address", "cowork.tax.col.paid_on", "cowork.tax.col.income_type",
                  "cowork.tax.col.rate", "cowork.tax.col.paid", "cowork.tax.col.withheld", "cowork.tax.col.condition",
                  "cowork.tax.col.check", "cowork.tax.col.source"])
    width = 13 + len(names)
    paid, withheld = 9 + len(names), 10 + len(names)
    totals = {entry["sheet"]: entry for entry in data.get("sheet_totals") or []}
    lines, shown_seq = data.get("lines") or [], set()
    for position, line in enumerate(lines):
        first = line["seq"] not in shown_seq
        shown_seq.add(line["seq"])
        name_cells = ([line.get("name_title", ""), line.get("name_first", ""), line.get("name_last", "")]
                      if form == "pnd3" else [line["payee_name"]])
        worst = _worst([item for item in line.get("issues") or [] if item["level"] != "info"])
        checks = ", ".join(dict.fromkeys(_issue_text(sheet, item) for item in line.get("issues") or []))
        sheet.line([line["sheet"], line["seq"] if first else "", line["payee_tax_id"] if first else "",
                    (line["payee_branch"] or "00000") if first else "", *(name_cells if first else [""] * len(names)),
                    line["payee_address"] if first else "", periods.be(line["date"]), _income(sheet, line),
                    line["rate"], line["base"], line["tax"], line["condition"], checks, ", ".join(line["refs"])],
                   money=(paid, withheld), status=worst, status_column=width - 1)
        last_on_sheet = position + 1 == len(lines) or lines[position + 1]["sheet"] != line["sheet"]
        if last_on_sheet and line["sheet"] in totals:
            values = [""] * width
            values[0], values[1] = line["sheet"], sheet.t("cowork.tax.sheet_total")
            values[paid - 1], values[withheld - 1] = totals[line["sheet"]]["base"], totals[line["sheet"]]["tax"]
            sheet.line(values, money=(paid, withheld), emphasis=True)
    values = [""] * width
    values[1], values[paid - 1], values[withheld - 1] = sheet.t("cowork.tax.total"), data.get("base"), data.get("tax")
    sheet.line(values, money=(paid, withheld), emphasis=True)
    sheet.widths([6, 6, 16, 8, *([10, 18, 18] if form == "pnd3" else [36]), 40, 12, 24, 8, 16, 16, 8, 30, 24])


def wht_book(summary, lang) -> bytes:
    book = Workbook()
    sheet = _Sheet(book, "cowork.tax.sheet.summary", lang, first=True)
    company = summary.get("company") or {}
    year, month = periods.parse_period(summary["period"])
    sheet.title(f"{sheet.t('cowork.tax.form.pnd53')} / {sheet.t('cowork.tax.form.pnd3')} {company.get('name') or ''}".strip())
    sheet.pair("cowork.tax.head.payer", company.get("name") or "")
    sheet.pair("cowork.tax.head.tax_id", company.get("tax_id") or "")
    sheet.pair("cowork.tax.head.branch", company.get("branch") or "")
    sheet.pair("cowork.tax.head.paid_month", periods.be_month(year, month))
    sheet.pair("cowork.tax.head.due_paper", periods.be((summary.get("deadlines") or {}).get("paper")))
    sheet.pair("cowork.tax.head.due_online", periods.be((summary.get("deadlines") or {}).get("efiling")))
    sheet.blank()
    sheet.header(["cowork.tax.col.form", "cowork.tax.col.payees", "cowork.tax.col.sheets", "cowork.tax.col.total_paid",
                  "cowork.tax.col.total_tax", "cowork.tax.col.surcharge", "cowork.tax.col.total_due"])
    forms = summary.get("forms") or {}
    for form in ("pnd53", "pnd3"):
        data = forms.get(form) or {}
        sheet.line([sheet.t(f"cowork.tax.form.{form}"), data.get("payees", 0), data.get("sheets", 0), data.get("base"),
                    data.get("tax"), "0.00", data.get("tax")], money=(4, 5, 6, 7))
    sheet.widths([36, 12, 12, 20, 20, 14, 22])
    for form in ("pnd53", "pnd3"):
        _attachment(book, lang, form, forms.get(form) or {})

    review = _Sheet(book, "cowork.tax.sheet.review", lang)
    width = 9
    review.section("cowork.tax.review.questions", width)
    review.header(["cowork.tax.col.payee_tax_id", "cowork.tax.col.payee_name", "cowork.tax.col.category",
                   "cowork.tax.col.rows", "cowork.tax.col.rate", "cowork.tax.col.paid", "cowork.tax.col.withheld",
                   "cowork.tax.col.suggested", ""])
    for question in summary.get("questions") or []:
        review.line([question["payee_tax_id"], question["payee_name"], question.get("category", ""), question["rows"],
                     ", ".join(question.get("rates") or []), question["base"], question["tax"],
                     review.t("cowork.tax.income." + question["suggested"]) if question.get("suggested") else "", ""],
                    money=(6, 7))
    for key, rows in (("cowork.tax.review.excluded", summary.get("review") or []),
                      ("cowork.tax.review.suggestions", summary.get("suggestions") or [])):
        review.blank()
        review.section(key, width)
        review.header(["cowork.tax.col.issue", "cowork.tax.col.paid_on", "cowork.tax.col.payee_tax_id",
                       "cowork.tax.col.payee_name", "cowork.tax.col.income_type", "cowork.tax.col.rate",
                       "cowork.tax.col.paid", "cowork.tax.col.withheld", "cowork.tax.col.source"])
        for row in rows:
            issues = [item for item in row.get("issues") or [] if item["level"] != "info"] or row.get("issues") or []
            review.line([", ".join(dict.fromkeys(_issue_text(review, item) for item in issues)),
                         periods.be(row["date"]), row["payee_tax_id"], row["payee_name"], _income(review, row),
                         row["rate"], row["base"] or None, row["tax"] or None, ", ".join(row["refs"])],
                        money=(7, 8), status=_worst(issues), status_column=1)
    if summary.get("notes"):
        review.blank()
        review.section("cowork.tax.review.notes", width)
        review.header(["cowork.tax.col.issue", "cowork.tax.col.detail", "cowork.tax.col.source", "", "", "", "", "",
                       ""])
        for note in summary["notes"]:
            review.line([review.t("cowork.tax.issue." + note["code"]), _detail(review, note), note.get("ref", ""),
                         "", "", "", "", "", ""], status=note["level"], status_column=1)
    review.widths([30, 16, 16, 36, 24, 10, 16, 16, 30])
    return _save(book)
