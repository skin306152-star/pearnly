"""ภ.พ.30 草稿：各栏由 Express 回读的 ISVAT 行算出。纯函数：只用标准库，金额一律 Decimal。

取行口径同 Express 自己的 รายงานภาษีซื้อ / รายงานภาษีขาย：税期（VATPRD）是本月的行；软删的、作废（DOCSTAT C）的不计，
票据日在本月但税期在别的月的单独列出说明。草稿数字 = Express 记录本身，核查（`input_vat`）只标问题、不悄悄改数。

栏位按税务局 2568 年版表样（2569-03-01 起用）：按期正常申报只填到第 11 或第 12 栏，第 13–16 栏（เงินเพิ่ม、
เบี้ยปรับ 及合计）只在逾期或补报时填，这里留空。第 10 栏「ภาษีที่ชำระเกินยกมา」与免税销售额由会计给。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pearnly_cowork.tax import periods

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
SIDES = ("P", "S")
PURCHASE, SALE = "P", "S"
# Express 的作废标记（DOCSTAT）；正常为 N。作废行在 Express 税报里怎么显示要在测试机核对。
VOID_STATUS = ("C",)
ZERO_VAT_AS = ("zero_rated", "exempt")
BOXES = tuple(str(number) for number in range(1, 17))
LATE_ONLY = ("13", "14", "15", "16")


def money(value):
    """金额 → 两位小数 Decimal（DBF 的 B 型是二进制浮点：先转字符串再量化）；空或读不出回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class VatRecord:
    index: int
    side: str
    rectyp: str
    period: tuple | None
    tax_date: date | None
    doc_date: date | None
    docnum: str
    refnum: str
    name: str
    prefix: str
    tax_id: str
    branch: str
    amount: Decimal
    vat: Decimal
    status: str
    deleted: bool
    history: str

    @property
    def invoice_date(self):
        return self.tax_date or self.doc_date

    def as_dict(self) -> dict:
        return {"index": self.index, "side": self.side, "rectyp": self.rectyp,
                "period": periods.text(*self.period) if self.period else "",
                "tax_date": self.tax_date.isoformat() if self.tax_date else "",
                "doc_date": self.doc_date.isoformat() if self.doc_date else "",
                "docnum": self.docnum, "refnum": self.refnum, "name": self.name, "prefix": self.prefix,
                "tax_id": self.tax_id, "branch": self.branch, "amount": str(self.amount), "vat": str(self.vat),
                "status": self.status, "deleted": self.deleted, "history": self.history}


def _text(value, limit=200) -> str:
    return str(value if value is not None else "").strip()[:limit]


def records(raw) -> tuple[list, list]:
    """回读行（`readback.service.vat_records` 的 records）→ (VatRecord 列表, 读不了的行 [{index, code, docnum}])。"""
    out, errors = [], []
    for index, row in enumerate(raw or [], 1):
        if not isinstance(row, Mapping):
            errors.append({"index": index, "code": "record_invalid", "docnum": ""})
            continue
        side = _text(row.get("side"), 1).upper()
        amount, vat = money(row.get("amount")), money(row.get("vat"))
        if side not in SIDES or amount is None or vat is None:
            errors.append({"index": index, "code": "record_invalid", "docnum": _text(row.get("docnum"), 30)})
            continue
        try:
            period = periods.parse_period(row.get("period"))
        except ValueError:
            period = None
        out.append(VatRecord(
            index=index, side=side, rectyp=_text(row.get("rectyp"), 2), period=period,
            tax_date=periods.parse_date(row.get("tax_date")), doc_date=periods.parse_date(row.get("doc_date")),
            docnum=_text(row.get("docnum"), 30), refnum=_text(row.get("refnum"), 30), name=_text(row.get("name")),
            prefix=_text(row.get("prefix"), 30), tax_id=_text(row.get("tax_id"), 20), branch=_text(row.get("branch"), 10),
            amount=amount, vat=vat, status=_text(row.get("status"), 2).upper(), deleted=bool(row.get("deleted")),
            history=_text(row.get("history"), 140)))
    return out, errors


def _order(record):
    return (record.invoice_date or date.max, record.refnum or record.docnum, record.index)


def select(rows, year: int, month: int) -> dict:
    """本月税期的销项 / 进项行（按日期、单号排），及不计入的行与原因：
    row_deleted（软删）、row_voided（作废）、row_other_period（票据日在本月、税期在别的月）、row_period_missing。"""
    sales, purchases, excluded = [], [], []
    for record in rows:
        if record.period == (year, month):
            if record.deleted:
                excluded.append((record, "row_deleted"))
            elif record.status in VOID_STATUS:
                excluded.append((record, "row_voided"))
            else:
                (sales if record.side == SALE else purchases).append(record)
        elif not record.deleted and periods.month_of(record.invoice_date) == (year, month):
            excluded.append((record, "row_other_period" if record.period else "row_period_missing"))
    return {"sales": sorted(sales, key=_order), "purchases": sorted(purchases, key=_order),
            "excluded": sorted(excluded, key=lambda item: _order(item[0]))}


def total(rows) -> tuple[Decimal, Decimal]:
    return sum((r.amount for r in rows), ZERO), sum((r.vat for r in rows), ZERO)


def boxes(sales, purchases, *, exempt_sales=ZERO, zero_vat_as="zero_rated", excess_brought_forward=ZERO) -> dict:
    """第 1–16 栏（键 "1"…"16"，值 Decimal；第 13–16 栏为 None）。

    - 1 ยอดขายในเดือนนี้ = 本月销项行的金额 + 会计给的免税销售额（免税销售不开税票，不在 ISVAT 里）；
    - 2 / 3：税额为 0 的销项行默认算零税率（第 2 栏），会计说是免税就算进第 3 栏；
    - 6 / 7 = 本月进项行的金额 / 税额（与 Express 记录一致，不剔除核查标出的行）；
    - 8 / 9 = 5 与 7 之差；10 = 上月结转的多缴税额；11 / 12 照表样：8 > 10 时 11 = 8 − 10，
      否则 12 = 10 − 8（有第 8 栏时）或 9 + 10。"""
    exempt = money(exempt_sales) or ZERO
    carried = money(excess_brought_forward) or ZERO
    if zero_vat_as not in ZERO_VAT_AS:
        raise ValueError("tax.option_invalid")
    sales_amount, sales_vat = total(sales)
    zero_vat = sum((r.amount for r in sales if r.vat == 0 and r.amount != 0), ZERO)
    purchase_amount, purchase_vat = total(purchases)
    out = dict.fromkeys(BOXES)
    out["1"] = sales_amount + exempt
    out["2"] = zero_vat if zero_vat_as == "zero_rated" else ZERO
    out["3"] = exempt + (zero_vat if zero_vat_as == "exempt" else ZERO)
    out["4"] = out["1"] - out["2"] - out["3"]
    out["5"] = sales_vat
    out["6"], out["7"] = purchase_amount, purchase_vat
    out["8"] = max(out["5"] - out["7"], ZERO)
    out["9"] = max(out["7"] - out["5"], ZERO)
    out["10"] = carried
    if out["8"] > carried:
        out["11"], out["12"] = out["8"] - carried, ZERO
    elif out["8"] > 0:
        out["11"], out["12"] = ZERO, carried - out["8"]
    else:
        out["11"], out["12"] = ZERO, out["9"] + carried
    return out


def outcome(values: Mapping) -> str:
    """payable（第 11 栏 > 0）/ overpaid（第 12 栏 > 0）/ nil。"""
    if (values.get("11") or ZERO) > 0:
        return "payable"
    if (values.get("12") or ZERO) > 0:
        return "overpaid"
    return "nil"
