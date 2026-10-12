"""POS 导出（收银机的销售明细 Excel / CSV）→ 逐日销售汇总：纯规则，不依赖框架、不做 IO、不调模型、不计费。

各家 POS（Ocha、Loyverse 等）导出格式不同，这里做通用的列识别：日期、票号、含税金额（必填），增值税、付款方式、
单据类型 / 状态、商品、商品编码、数量（有就用）。认不准的（缺必填列、同一类有几列、日 / 月分不清、金额含不含税、
不认识的状态值）列成问题，按「公司 + 导出版式（表头指纹）」问一次、记住（`layouts`），以后同版式直接用。

金额一律 Decimal：每天含税合计与 POS 逐分相等；有增值税列时当天税额 = 逐票税额之和，没有时 = 当天含税合计 × 7/107
四舍五入到分；尾差全部落在税前额。作废单不算，退款单记负数；付款方式按当天汇总，之和恰好等于当天含税合计。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pearnly_cowork.contracts import pos as pos_contract

LAYOUT_VERSION = 1
FIELDS = ("date", "receipt", "amount", "vat", "payment", "kind", "status", "item", "sku", "qty")
REQUIRED = ("date", "receipt", "amount")
# 识别先后：先定下的列不再给后面的类（「ยอดชำระ」先当金额，就不会再被当成付款方式）
ORDER = ("date", "receipt", "vat", "amount", "payment", "kind", "status", "item", "sku", "qty")
ASKED = ("date", "receipt", "amount", "vat", "payment")  # 有几列都像时要问的类（其余认不准就不用）
DATE_ORDERS = ("dmy", "mdy", "ymd")
BASES = ("inclusive", "exclusive")
STATUS_VALUES = ("sale", "void", "refund")
HEADER_SCAN = 30
SAMPLE_ROWS = 3
SAMPLE_WIDTH = 40
MAX_DAYS = 100        # 一份导出最多这么多天（一天一张票，结果表格一次最多 100 张）
MAX_ITEM_LINES = 999  # Express 单据明细 NXTSEQ C(3)
MAX_UNKNOWN_VALUES = 20
_CENT = Decimal("0.01")
_VAT_RATE = Decimal("7")

# 表头关键词（泰 / 英 / 中 / 日），按层排：先在第一层找，找到就不看后面的层。
HINTS = {
    "date": (("วันที่", "date", "日期", "日付", "วันเวลา", "datetime"),),
    "receipt": (("receipt number", "receipt no", "เลขที่ใบเสร็จ", "เลขที่บิล", "bill no", "bill number", "เลขบิล",
                 "เลขที่ใบกำกับ", "invoice no", "invoice number", "ticket no", "order no", "order number", "单号",
                 "小票号", "票号", "伝票番号", "レシート番号"),
                ("receipt", "bill", "บิล", "ใบเสร็จ", "เลขที่", "ticket", "order", "no.")),
    "vat": (("vat", "ภาษีมูลค่าเพิ่ม", "ภาษีขาย", "taxes", "tax amount", "ภาษี", "增值税", "税额", "消費税", "tax"),),
    "amount": (("total collected", "grand total", "amount paid", "paid amount", "net total", "ยอดชำระ", "ยอดรับชำระ",
                "รวมทั้งสิ้น", "ยอดสุทธิ", "ยอดรวมสุทธิ", "实收", "实付", "合计金额", "合計"),
               ("net sales", "ยอดขายสุทธิ"),
               ("total", "amount", "ยอดขาย", "ยอดรวม", "จำนวนเงิน", "sales", "金额", "销售额", "金額")),
    "payment": (("payment type", "payment method", "ประเภทการชำระ", "ช่องทางชำระ", "ช่องทางการชำระ", "วิธีชำระ",
                 "ชำระโดย", "支付方式", "付款方式", "支払方法"),
                ("payment", "ชำระ", "支付", "付款", "tender")),
    "kind": (("receipt type", "ประเภทบิล", "ประเภทใบเสร็จ", "transaction type", "单据类型"),),
    "status": (("status", "state", "สถานะ", "状态", "ステータス"),),
    "item": (("item name", "ชื่อสินค้า", "ชื่อเมนู", "product name", "商品名", "品名"),
             ("item", "สินค้า", "รายการ", "menu", "เมนู", "product", "商品")),
    "sku": (("sku", "รหัสสินค้า", "item code", "product code", "barcode", "บาร์โค้ด", "商品编码", "商品コード"),),
    "qty": (("quantity", "qty", "จำนวนชิ้น", "数量"), ("จำนวน",)),
}
# 表头里带这些词就不是这一类（「ยอดก่อนภาษี」不是税额、「จำนวนเงิน」不是数量、「จำนวนลูกค้า」是客人数）
EXCLUDE = {
    "date": ("due", "ครบกำหนด", "update", "แก้ไข"),
    "receipt": ("type", "ประเภท", "tax id", "ผู้เสียภาษี", "เลขประจำตัว", "date", "วันที่", "amount", "ยอด"),
    "vat": ("ก่อน", "before", "excl", "incl", "รวม", "สุทธิ", "net", "total", "tax id", "ผู้เสียภาษี", "เลขประจำตัว",
            "rate", "อัตรา", "%", "type", "ประเภท"),
    "amount": ("before", "ก่อน", "excl", "ไม่รวม", "discount", "ส่วนลด", "cost", "ต้นทุน", "profit", "กำไร",
               "service", "ค่าบริการ", "qty", "จำนวนชิ้น", "change", "เงินทอน", "tip", "ทิป", "rate", "อัตรา", "%",
               "gross sales"),
    "payment": ("ยอด", "amount", "date", "วันที่", "จำนวน", "total"),
    "item": ("code", "รหัส", "sku", "category", "หมวด", "qty", "จำนวน", "price", "ราคา", "amount", "count", "type"),
    "qty": ("เงิน", "amount", "ลูกค้า", "guest", "customer", "คน"),
}
# 单据类型 / 状态一栏的值：作废的不算，退款的记负数，其余是销售
SALE_WORDS = frozenset({"", "sale", "sales", "closed", "completed", "complete", "paid", "success", "succeeded", "done",
                        "finished", "settled", "normal", "ok", "ปกติ", "สำเร็จ", "ชำระแล้ว", "ขาย", "เสร็จสิ้น",
                        "销售", "已完成", "完成", "正常", "販売", "完了"})
VOID_WORDS = frozenset({"void", "voided", "cancel", "cancelled", "canceled", "deleted", "ยกเลิก", "ยกเลิกบิล", "ลบ",
                        "作废", "取消", "已取消", "取消済", "キャンセル"})
REFUND_WORDS = frozenset({"refund", "refunded", "return", "returned", "คืนเงิน", "คืนสินค้า", "รับคืน", "退款", "退货",
                          "返品", "返金"})
# 票号一栏是这些字的行是合计行，不是票
TOTAL_WORDS = frozenset({"total", "grand total", "subtotal", "sum", "รวม", "รวมทั้งสิ้น", "ยอดรวม", "รวมทั้งหมด", "合计",
                         "总计", "合計"})
_THAI_MONTHS = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9,
                "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12, "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
                "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11,
                "ธันวาคม": 12}
_EN_MONTHS = {name: index for index, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_NUMERIC_DATE = re.compile(r"^(\d{1,4})[-/.](\d{1,2})[-/.](\d{1,4})$")
_WORD_DATE = re.compile(r"^(\d{1,2})[\s\-/]*([^\d\s\-/,]+)[\s\-/,]*(\d{2,4})$")
_EN_FIRST = re.compile(r"^([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{2,4})$")


class PosError(ValueError):
    """读不了 / 对不上：``code`` 是词条键（`pos.*`），``details`` 带行号、日期等。"""

    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


# ── 文本与金额 ─────────────────────────────────────────────────────────────────


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return unicodedata.normalize("NFC", str(value)).replace("\xa0", " ").strip()


def _norm(value) -> str:
    return re.sub(r"\s+", " ", cell_text(value).casefold())


def _hit(header: str, hints) -> bool:
    """表头是否命中一组关键词：短的英文词（≤3 字母，如 tax、qty、no.）按整词，其余按子串（泰文、中文不分词）。"""
    text = _norm(header)
    if not text:
        return False
    tokens = set(re.split(r"[\s/().,_\-:#]+", text))
    for hint in hints:
        hint = hint.casefold()
        if hint.isascii() and len(hint.strip(".")) <= 3 and hint.strip(".").isalpha():
            if hint.strip(".") in tokens:
                return True
        elif hint in text:
            return True
    return False


def money(value):
    """单元格 → 两位小数 Decimal（千分位、฿、THB、บาท、括号负数、尾随负号都认；Excel 浮点先转字符串）；空是 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("not a number")
    if isinstance(value, (int, float, Decimal)):
        amount = Decimal(str(value))
    else:
        text = cell_text(value)
        for token in ("฿", "THB", "thb", "บาท", ",", " ", "​"):
            text = text.replace(token, "")
        if not text or text in ("-", "--"):
            return None
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative, text = True, text[1:-1]
        if text.endswith("-"):
            negative, text = True, text[:-1]
        amount = Decimal(text)
        if negative:
            amount = -abs(amount)
    if not amount.is_finite():
        raise ValueError("not a number")
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def quantity(value):
    if value is None or value == "":
        return None
    amount = Decimal(str(value).replace(",", "").strip()) if not isinstance(value, (int, float, Decimal)) \
        else Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("not a number")
    return amount


# ── 日期 ─────────────────────────────────────────────────────────────────────


def _year(value: int, anchor_year: int) -> int:
    if value >= 2400:
        return value - 543  # 佛历
    if value < 100:
        # 两位年：公历 20yy 还是佛历 25yy，取离当前年份近的那个
        return min((2000 + value, 1957 + value), key=lambda year: abs(year - anchor_year))
    return value


def _date_text(value: str) -> str:
    """去掉日期后面的时刻（``14:32``、``2:30 PM``），留下日期本身。"""
    text = re.sub(r"(?<=\d)T(?=\d)", " ", value.strip())
    parts = [part for part in text.split() if ":" not in part and part.casefold() not in ("am", "pm", "น.")]
    return " ".join(parts)


def parse_date(value, order: str, anchor_year: int):
    """单元格 → date；读不出回 None。``order``：数字日期两段的先后（dmy / mdy / ymd）。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < value < 80000:  # Excel 序列日期
            return date(1899, 12, 30) + timedelta(days=int(value))
        return None
    text = _date_text(cell_text(value))
    if not text:
        return None
    try:
        match = _NUMERIC_DATE.match(text)
        if match:
            a, b, c = (int(part) for part in match.groups())
            if len(match.group(1)) >= 3:
                return date(_year(a, anchor_year), b, c)
            day, month = (a, b) if order != "mdy" else (b, a)
            return date(_year(c, anchor_year), month, day)
        match = _WORD_DATE.match(text)
        if match:
            word = match.group(2).casefold().strip(".")
            month = _THAI_MONTHS.get(match.group(2)) or _EN_MONTHS.get(word[:3])
            if month:
                return date(_year(int(match.group(3)), anchor_year), month, int(match.group(1)))
        match = _EN_FIRST.match(text.casefold())
        if match and match.group(1)[:3] in _EN_MONTHS:
            return date(_year(int(match.group(3)), anchor_year), _EN_MONTHS[match.group(1)[:3]], int(match.group(2)))
    except ValueError:
        return None
    return None


def date_order(values) -> str | None:
    """文本日期两段谁是日：有一段大过 12 就定了；都不大过 12（月初十二天内的导出）定不了回 None。ISO 写法回 ymd。"""
    first_big = second_big = False
    seen = False
    for value in values:
        if isinstance(value, (date, datetime, int, float)):
            continue
        match = _NUMERIC_DATE.match(_date_text(cell_text(value)))
        if not match:
            continue
        if len(match.group(1)) >= 3:
            return "ymd"
        seen = True
        first_big |= int(match.group(1)) > 12
        second_big |= int(match.group(2)) > 12
    if first_big and not second_big:
        return "dmy"
    if second_big and not first_big:
        return "mdy"
    return None if seen else "dmy"


# ── 表头与版式 ──────────────────────────────────────────────────────────────────


def header_hits(row) -> int:
    """这一行像表头的程度：命中了几类。"""
    headers = [cell_text(cell) for cell in row]
    return sum(1 for name in FIELDS if any(_hit(header, tier) for header in headers for tier in HINTS[name]))


def header_row(rows) -> int:
    """表头在第几行（0 起）：前 30 行里命中类别最多、且日期 / 票号 / 金额至少中两类的那一行（上面可能有报表标题）。"""
    best, best_hits = None, 0
    for index, row in enumerate(rows[:HEADER_SCAN]):
        headers = [cell_text(cell) for cell in row]
        required = sum(1 for name in REQUIRED if any(_hit(h, tier) for h in headers for tier in HINTS[name]))
        hits = header_hits(row)
        if required >= 2 and hits > best_hits:
            best, best_hits = index, hits
    if best is None:
        raise PosError("pos.no_header")
    return best


def fingerprint(headers) -> str:
    """导出版式的指纹：去掉空列后的表头（小写、合并空白）按顺序取摘要。同一台 POS 同一种导出每次都一样。"""
    names = [_norm(header) for header in headers if _norm(header)]
    return hashlib.sha256(json.dumps(names, ensure_ascii=False).encode()).hexdigest()


@dataclass
class Detection:
    layout: dict
    questions: list = field(default_factory=list)


def _status_word(value) -> str | None:
    text = _norm(value)
    if text in VOID_WORDS:
        return "void"
    if text in REFUND_WORDS:
        return "refund"
    if text in SALE_WORDS:
        return "sale"
    return None


def detect(headers, rows) -> Detection:
    """认列：每一类在表头里找关键词，第一层命中唯一一列就定下；缺必填列、或要问的类有几列都像，列成问题。
    再看日 / 月先后、金额含不含税（金额列不是「实收 / 合计」这类、又有税额列时问）、单据类型 / 状态里不认识的值。"""
    texts = [cell_text(header) for header in headers]
    columns, tiers, questions, used = {}, {}, [], set()
    for name in ORDER:
        found = None
        for level, tier in enumerate(HINTS[name]):
            candidates = [index for index, text in enumerate(texts) if text and index not in used
                          and _hit(text, tier) and not _hit(text, EXCLUDE.get(name, ()))]
            if candidates:
                found = (level, candidates)
                break
        if found is None:
            if name in REQUIRED:
                questions.append({"kind": "column", "field": name,
                                  "options": [text for index, text in enumerate(texts) if text and index not in used]})
            continue
        level, candidates = found
        if len(candidates) == 1:
            columns[name], tiers[name] = texts[candidates[0]], level
            used.add(candidates[0])
        elif name in ASKED:
            questions.append({"kind": "column", "field": name, "options": [texts[index] for index in candidates]})
    layout = {"version": LAYOUT_VERSION, "columns": columns, "date_order": "dmy", "amount_basis": "inclusive",
              "status_values": {}}
    if "date" in columns:
        index = texts.index(columns["date"])
        order = date_order(row[index] for row in rows if index < len(row))
        if order is None:
            questions.append({"kind": "date_order", "options": list(DATE_ORDERS[:2]),
                              "samples": _column_samples(rows, index)})
        else:
            layout["date_order"] = order
    if "amount" in columns and "vat" in columns and tiers.get("amount", 0) > 0:
        questions.append({"kind": "amount_basis", "column": columns["amount"], "options": list(BASES)})
    unknown = []
    for name in ("kind", "status"):
        if name in columns:
            index = texts.index(columns[name])
            for row in rows:
                value = cell_text(row[index]) if index < len(row) else ""
                if _status_word(value) is None and value not in unknown:
                    unknown.append(value)
    if unknown:
        questions.append({"kind": "status_values", "values": unknown[:MAX_UNKNOWN_VALUES],
                          "options": list(STATUS_VALUES)})
    return Detection(layout, questions)


def _column_samples(rows, index):
    found = []
    for row in rows:
        value = cell_text(row[index]) if index < len(row) else ""
        if value and value not in found:
            found.append(value[:SAMPLE_WIDTH])
        if len(found) >= SAMPLE_ROWS:
            break
    return found


def samples(headers, rows) -> list:
    """前几行数据（给人认列用，单元格截短）。"""
    texts = [cell_text(header) for header in headers]
    out = []
    for row in rows[:SAMPLE_ROWS]:
        out.append({text: cell_text(row[index])[:SAMPLE_WIDTH] for index, text in enumerate(texts)
                    if text and index < len(row) and cell_text(row[index])})
    return out


def apply_answers(headers, rows, answers) -> Detection:
    """人回答的问题（``columns`` {类: 表头}、``date_order``、``amount_basis``、``status_values`` {值: sale/void/refund}）
    合进认出来的版式；仍没答上的问题照样列出。表头写错（不在这份导出里）当作没答；可选类答空串 = 不用这一列。"""
    detection = detect(headers, rows)
    layout = detection.layout
    answers = answers if isinstance(answers, dict) else {}
    texts = {cell_text(header).casefold(): cell_text(header) for header in headers if cell_text(header)}
    for name, header in (answers.get("columns") or {}).items():
        if name not in FIELDS:
            continue
        chosen = texts.get(cell_text(header).casefold()) if header else None
        if chosen:
            for other, value in list(layout["columns"].items()):
                if value == chosen and other != name:
                    layout["columns"].pop(other)
            layout["columns"][name] = chosen
        elif header in ("", None) and name not in REQUIRED:
            layout["columns"].pop(name, None)
    if answers.get("date_order") in DATE_ORDERS:
        layout["date_order"] = answers["date_order"]
    if answers.get("amount_basis") in BASES:
        layout["amount_basis"] = answers["amount_basis"]
    for value, meaning in (answers.get("status_values") or {}).items():
        if meaning in STATUS_VALUES:
            layout["status_values"][_norm(value)] = meaning
    answered = set((answers.get("columns") or {}))
    remaining = []
    for question in detection.questions:
        kind = question["kind"]
        if kind == "column" and question["field"] in answered and (
                question["field"] in layout["columns"] or question["field"] not in REQUIRED):
            continue
        if kind == "date_order" and answers.get("date_order") in DATE_ORDERS:
            continue
        if kind == "amount_basis" and answers.get("amount_basis") in BASES:
            continue
        if kind == "status_values":
            left = [value for value in question["values"] if _norm(value) not in layout["status_values"]]
            if not left:
                continue
            question = {**question, "values": left}
        remaining.append(question)
    for name in REQUIRED:
        if name not in layout["columns"] and not any(q["kind"] == "column" and q["field"] == name for q in remaining):
            remaining.append({"kind": "column", "field": name, "options": sorted(texts.values())})
    return Detection(layout, remaining)


def valid_layout(layout) -> bool:
    return (isinstance(layout, dict) and layout.get("version") == LAYOUT_VERSION
            and isinstance(layout.get("columns"), dict) and all(name in layout["columns"] for name in REQUIRED)
            and set(layout["columns"]) <= set(FIELDS)
            and layout.get("date_order") in DATE_ORDERS and layout.get("amount_basis") in BASES
            and isinstance(layout.get("status_values"), dict)
            and (layout["amount_basis"] == "inclusive" or "vat" in layout["columns"]))


def _indices(headers, layout) -> dict:
    texts = [cell_text(header).casefold() for header in headers]
    found = {}
    for name, header in layout["columns"].items():
        wanted = cell_text(header).casefold()
        if wanted not in texts:
            raise PosError("pos.column_missing", column=header)
        found[name] = texts.index(wanted)
    return found


# ── 逐票、逐日 ─────────────────────────────────────────────────────────────────


@dataclass
class Receipt:
    number: str
    day: date
    amount: Decimal = Decimal("0")
    vat: Decimal = Decimal("0")
    payment: str = ""
    meaning: str = "sale"
    rows: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # [(name, sku, qty, amount)]


def _meaning(layout, *values) -> str:
    meanings = set()
    for value in values:
        word = layout["status_values"].get(_norm(value)) or _status_word(value)
        if word is None:
            raise PosError("pos.status_unknown", value=cell_text(value)[:SAMPLE_WIDTH])
        meanings.add(word)
    return "void" if "void" in meanings else "refund" if "refund" in meanings else "sale"


def receipts(headers, rows, layout, *, anchor_year: int, first_row: int = 1) -> tuple:
    """数据行 → 票（同一票号的几行合成一张：商品级导出一行一个商品）。回 ``(销售与退款票, 作废张数)``。

    合并单元格（票号、日期只写在第一行）往下沿用；票号与金额都空的行（小计、页脚）跳过。``first_row`` 是第一行数据在
    文件里的行号（报错时给人看）。"""
    index = _indices(headers, layout)
    order = layout["date_order"]
    found: dict = {}
    sequence: list = []
    last_number, last_day = "", None

    def cell(row, name):
        position = index.get(name)
        return row[position] if position is not None and position < len(row) else None

    for offset, row in enumerate(rows):
        line = first_row + offset
        if not any(cell_text(value) for value in row):
            continue
        number, raw_day = cell_text(cell(row, "receipt")), cell(row, "date")
        has_day = isinstance(raw_day, (date, datetime)) or bool(cell_text(raw_day))
        day = parse_date(raw_day, order, anchor_year) if has_day else None
        if _norm(number) in TOTAL_WORDS:
            continue  # 「合计 / รวม」行
        if not number:
            # 票号空：合并单元格的商品续行（同一张票的下一个商品）沿用上一行；其余（小计、页脚、标题）跳过
            if has_day and day is None or "item" not in index or not cell_text(cell(row, "item")) or not last_number:
                continue
            number = last_number
            day = day or last_day
        if day is None:
            if has_day or last_day is None:
                raise PosError("pos.date_unreadable", row=line)
            day = last_day  # 按日期分组的报表：日期只写在这一天的第一行
        try:
            amount = money(cell(row, "amount")) or Decimal("0")
        except (InvalidOperation, ValueError) as error:
            raise PosError("pos.amount_unreadable", row=line) from error
        try:
            vat = money(cell(row, "vat")) if "vat" in index else None
        except (InvalidOperation, ValueError) as error:
            raise PosError("pos.amount_unreadable", row=line) from error
        vat = vat or Decimal("0")
        if layout["amount_basis"] == "exclusive":
            amount += vat
        meaning = _meaning(layout, *(cell(row, name) for name in ("kind", "status") if name in index))
        key = (number, day)
        receipt = found.get(key)
        if receipt is None:
            receipt = found[key] = Receipt(number, day)
            sequence.append(receipt)
        receipt.amount += amount
        receipt.vat += vat
        receipt.rows.append(line)
        if meaning != "sale":
            receipt.meaning = "void" if "void" in (meaning, receipt.meaning) else meaning
        label = cell_text(cell(row, "payment"))
        if label and not receipt.payment:
            receipt.payment = label[:pos_contract.LABEL_MAX]
        if "item" in index:
            try:
                qty = quantity(cell(row, "qty")) if "qty" in index else None
            except (InvalidOperation, ValueError) as error:
                raise PosError("pos.amount_unreadable", row=line) from error
            receipt.lines.append((cell_text(cell(row, "item")), cell_text(cell(row, "sku")),
                                  Decimal("1") if qty is None else qty, amount))
        last_number, last_day = number, day
    kept, voids = [], 0
    for receipt in sequence:
        if receipt.meaning == "void":
            voids += 1
            continue
        if receipt.meaning == "refund":
            receipt.amount, receipt.vat = -abs(receipt.amount), -abs(receipt.vat)
            receipt.lines = [(name, sku, -abs(qty), -abs(amount)) for name, sku, qty, amount in receipt.lines]
        kept.append(receipt)
    if not kept:
        raise PosError("pos.no_rows")
    return kept, voids


def natural_key(number: str):
    return tuple(int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", number) if part)


@dataclass
class Day:
    day: date
    first: str
    last: str
    count: int
    refunds: int
    gross: Decimal
    vat: Decimal
    net: Decimal
    payments: list  # [(label, amount)]
    items: list     # [{"name", "sku", "qty", "amount"}]
    rows: list      # [(first_row, last_row)] 文件里的行号区间

    @property
    def ref(self) -> str:
        return pos_contract.day_ref(self.first, self.last, self.day)


def _row_ranges(numbers) -> list:
    ranges = []
    for number in sorted(set(numbers)):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return [tuple(pair) for pair in ranges]


def days(found, *, vat_column: bool, with_items: bool = False) -> list:
    """票 → 逐日汇总（按日期排）。每天：销售票号起止与张数、含税合计、税额、税前额、付款方式、（存货时）商品明细。"""
    by_day: dict = {}
    for receipt in found:
        by_day.setdefault(receipt.day, []).append(receipt)
    if len(by_day) > MAX_DAYS:
        raise PosError("pos.too_many_days", days=len(by_day))
    result = []
    for day in sorted(by_day):
        members = by_day[day]
        sales = sorted((r for r in members if r.meaning == "sale"), key=lambda r: natural_key(r.number))
        gross = sum((r.amount for r in members), Decimal("0"))
        if not sales or gross <= 0:
            raise PosError("pos.day_not_positive", day=day.isoformat())
        if vat_column:
            vat = sum((r.vat for r in members), Decimal("0"))
            ceiling = (gross * _VAT_RATE / (100 + _VAT_RATE)).quantize(_CENT, rounding=ROUND_HALF_UP)
            if vat < 0 or vat > ceiling + _CENT * len(members) + _CENT * 2:
                raise PosError("pos.vat_out_of_range", day=day.isoformat())
        else:
            vat = (gross * _VAT_RATE / (100 + _VAT_RATE)).quantize(_CENT, rounding=ROUND_HALF_UP)
        payments: dict = {}
        labels: dict = {}
        for receipt in members:
            # 现金的各种写法（含没写）合成一种；其余按写法归一后分开
            cash = pos_contract.is_cash(receipt.payment)
            key = "" if cash else pos_contract.norm_label(receipt.payment)
            if key not in labels or (cash and labels[key] == "Cash" and receipt.payment):
                labels[key] = receipt.payment or "Cash"
            payments[key] = payments.get(key, Decimal("0")) + receipt.amount
        paid = []
        for key, amount in payments.items():
            if amount == 0:
                continue
            if amount < 0 and not pos_contract.is_cash(labels[key]):
                raise PosError("pos.payment_negative", day=day.isoformat(), payment=labels[key])
            paid.append((labels[key], amount))
        if len(paid) > pos_contract.PAYMENTS_MAX:
            raise PosError("pos.too_many_payments", day=day.isoformat())
        items = _items(members, gross, day) if with_items else []
        result.append(Day(day, sales[0].number, sales[-1].number, len(sales),
                          sum(1 for r in members if r.meaning == "refund"), gross, vat, gross - vat, paid, items,
                          _row_ranges(line for r in members for line in r.rows)))
    return result


def _items(members, gross, day) -> list:
    """当天按商品（有编码按编码，没有按品名）汇总数量与含税金额；之和须等于当天含税合计。"""
    lines: dict = {}
    for receipt in members:
        if not receipt.lines:
            raise PosError("pos.items_mismatch", day=day.isoformat())
        for name, sku, qty, amount in receipt.lines:
            if not name and not sku:
                raise PosError("pos.items_mismatch", day=day.isoformat())
            key = sku.casefold() if sku else _norm(name)
            line = lines.setdefault(key, {"name": name or sku, "sku": sku, "qty": Decimal("0"), "amount": Decimal("0")})
            line["qty"] += qty
            line["amount"] += amount
    kept = [line for line in lines.values() if line["qty"] != 0 or line["amount"] != 0]
    if any(line["qty"] <= 0 or line["amount"] <= 0 for line in kept):
        raise PosError("pos.items_not_positive", day=day.isoformat())
    if sum((line["amount"] for line in kept), Decimal("0")) != gross:
        raise PosError("pos.items_mismatch", day=day.isoformat())
    if len(kept) > MAX_ITEM_LINES:
        raise PosError("pos.too_many_items", day=day.isoformat())
    return kept


def parse(headers, rows, layout, *, anchor_year: int, with_items: bool = False, first_row: int = 1) -> tuple:
    """整份导出 → ``(逐日汇总, 作废张数)``（版式已定下）。"""
    if not valid_layout(layout):
        raise PosError("pos.layout_invalid")
    if with_items and "item" not in layout["columns"]:
        with_items = False
    found, voids = receipts(headers, rows, layout, anchor_year=anchor_year, first_row=first_row)
    return days(found, vat_column="vat" in layout["columns"], with_items=with_items), voids


# ── 一天 → 一张票（与识别结果同一形状，免识别、不计费） ───────────────────────────────


def _s(value: Decimal) -> str:
    return format(value.quantize(_CENT), "f")


def invoice_fields(day: Day) -> dict:
    """这一天的票面字段：简易税票、现金客户（买方空）、已收（现销）、销项；有商品明细时逐行含税金额。"""
    fields = {"document_type": "simplified_tax_invoice", "invoice_number": day.ref, "date": day.day.isoformat(),
              "buyer_name": "", "buyer_tax": "", "seller_name": "", "seller_tax": "",
              "subtotal": _s(day.net), "vat": _s(day.vat), "total_amount": _s(day.gross),
              "notes": pos_contract.range_text(day.first, day.last), "category": "", "currency": "",
              "items": [], "tax_ids": [], "direction": "sales", "posting_payment_manual": "cash"}
    for line in day.items:
        qty = line["qty"]
        fields["items"].append({"name": str(line["name"])[:50], "qty": format(qty.normalize(), "f"),
                                "price": _s(line["amount"] / qty), "subtotal": _s(line["amount"])})
    return fields


def day_page(day: Day, number: int, *, layout_fingerprint: str = "") -> dict:
    """这一天作为一张票的页面（与 `ocr.pages.flatten_pipeline_pages` 同一形状；``_extraction_audit["pos"]`` 记付款方式）。"""
    audit = pos_contract.audit(day.first, day.last, day.count, day.payments, layout=layout_fingerprint,
                               rows=day.rows)
    return {"page_number": number, "page": number, "page_index": number, "text": "", "lines": [],
            "fields": invoice_fields(day), "is_copy": False, "is_duplicate": False, "_multi_invoice_sibling": False,
            "elapsed_ms": 0, "input_tokens": 0, "output_tokens": 0, "error": None, "_layer_chain": ["pos_import"],
            "_trigger_reasons": [], "_needs_manual_review": False, "_layer1_avg_confidence": 1.0,
            "_document_type": "invoice", "_confidence_band": "auto", "_final_confidence": 1.0,
            "_validation_warnings": [], "_extraction_audit": audit}


def summary(day: Day) -> dict:
    """给人 / CC 看的这一天（金额字符串）。"""
    return {"date": day.day.isoformat(), "ref": day.ref, "range": pos_contract.range_text(day.first, day.last),
            "receipts": day.count, "refunds": day.refunds, "gross": _s(day.gross), "vat": _s(day.vat),
            "net": _s(day.net), "payments": [{"label": label, "amount": _s(amount), "cash": pos_contract.is_cash(label)}
                                             for label, amount in day.payments],
            "items": len(day.items)}


__all__ = ["BASES", "DATE_ORDERS", "Day", "Detection", "FIELDS", "LAYOUT_VERSION", "MAX_DAYS", "PosError", "REQUIRED",
           "STATUS_VALUES", "apply_answers", "date_order", "day_page", "days", "detect", "fingerprint", "header_row",
           "header_hits", "invoice_fields", "money", "parse", "parse_date", "receipts", "samples", "summary",
           "valid_layout"]
