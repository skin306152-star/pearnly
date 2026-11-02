# -*- coding: utf-8 -*-
"""一张要代录进 MR.ERP 的单（载荷 18 的 ``mrerp`` 块；第 3 步站点按这个形状出，评估 §4、§14.1）。

到这一步「账怎么记」已在站点定好（与 MR.ERP 导入直写同一份准备：菜单、默认值、对方 / 商品码），代录只照着录。载荷不带任何密码。

载荷形状（``payload["mrerp"]``）::

    {"url": "https://mrerp4sme.com", "connection": "<连接号>",
     "account_set": {"comidyear": "12", "seldb": "1", "name": "ABC2569", "company_name": "ABC Co., Ltd."},
     "doc_type": "purchase" | "purchase_expense" | "sales_credit" | "sales_cash",
     "menu": "363",                      # 这家公司这类单用的菜单号（idmenu），列表、新增、回读都在这个菜单里
     "docnum": "INV-001",                # 单号（菜单没开自动编号时才填进去；开了以页面给的为准）
     "date": "2026-10-31", "party_code": "S001",
     "ref_no": "IV6901-22", "ref_date": "2026-10-30",   # 对方单号（赊购 = 供应商发票号，填「เลขที่บิล」）
     "credit_days": "30", "vat_rate": "7%", "discount": "0",
     "defaults": {"dept": "", "job": "", "employee": "", "branch": "", "store": ""},  # 空 = 用页面带出的默认
     "lines": [{"code": "P001", "qty": "2", "price": "100", "unit": "", "store": "", "desc": ""}],
     "totals": {"before_vat": "200.00", "vat": "14.00", "net": "214.00"}}
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional

CENT = Decimal("0.01")
DOC_TYPES = ("purchase", "purchase_expense", "sales_credit", "sales_cash")


def money(value) -> Decimal:
    return Decimal(str(value).replace(",", "").strip() or "0").quantize(CENT, ROUND_HALF_UP)


def dec(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def thai_date(iso: str) -> str:
    """MR.ERP 页面上的日期写法：日/月/佛历年（``formatdates.php::dateeng2thai``，两位日月补零）。"""
    if not iso:
        return ""
    day = dt.date.fromisoformat(str(iso)[:10])
    return f"{day.day:02d}/{day.month:02d}/{day.year + 543}"


def iso_date(thai: str) -> str:
    """页面日期 → ISO（读回核对用）；认不出回空。"""
    try:
        d, m, y = (int(part) for part in str(thai or "").strip().split("/"))
        return dt.date(y - 543, m, d).isoformat()
    except (ValueError, TypeError):
        return ""


@dataclass
class Line:
    code: str
    qty: str
    price: str
    unit: str = ""
    store: str = ""
    desc: str = ""

    @property
    def amount(self) -> Decimal:
        return money(Decimal(str(self.qty)) * Decimal(str(self.price)))


@dataclass
class Draft:
    doc_id: str
    doc_type: str
    menu: str
    date: str  # 公历 ISO
    party_code: str
    lines: List[Line]
    docnum: str = ""
    ref_no: str = ""
    ref_date: str = ""
    credit_days: str = ""
    vat_rate: str = ""
    vat_included: bool = False
    discount: str = "0"
    defaults: Dict[str, str] = field(default_factory=dict)
    expected: Dict[str, str] = field(default_factory=dict)  # before_vat / vat / net；空则按行算

    def __post_init__(self):
        self.doc_type = str(self.doc_type or "").strip().lower()
        self.lines = [line if isinstance(line, Line) else Line(**line) for line in self.lines]
        dt.date.fromisoformat(self.date)

    @property
    def date_th(self) -> str:
        return thai_date(self.date)

    @property
    def ref_date_th(self) -> str:
        return thai_date(self.ref_date or self.date)

    @property
    def rate(self) -> Decimal:
        found = dec(str(self.vat_rate or "").replace("%", ""))
        return found if found is not None else Decimal(0)

    def totals(self) -> Dict[str, Decimal]:
        """与页面同算法（``allform.js::calcgrandtotal``）：明细合计 − 折扣 = 税前；不含税价按税前乘税率，含税价逐行拆税。"""
        if self.expected:
            return {k: money(v) for k, v in self.expected.items()}
        rate = self.rate
        discount = money(self.discount or "0")
        if self.vat_included:
            removed = [money(line.amount * rate / (100 + rate)) for line in self.lines]
            gross = sum((line.amount - vat for line, vat in zip(self.lines, removed)), Decimal(0))
            vat = sum(removed, Decimal(0)) - (money(discount * rate / 100) if discount else Decimal(0))
            before = gross - discount
        else:
            before = sum((line.amount for line in self.lines), Decimal(0)) - discount
            vat = money(before * rate / 100)
        return {"before_vat": money(before), "vat": money(vat), "net": money(before + vat)}

    def describe(self) -> str:
        """给模型看的一段说明（只有单据内容，不含任何凭据）。"""
        t = self.totals()
        rows = "\n".join(f"  {i + 1}. 品码 {l.code}  数量 {l.qty}  单价 {l.price}" for i, l in enumerate(self.lines))
        return (f"MR.ERP 单种 {self.doc_type}（菜单 {self.menu}），日期 {self.date_th}，对方码 {self.party_code}"
                + (f"，对方单号 {self.ref_no}" if self.ref_no else "") + f"\n明细：\n{rows}\n"
                + f"合计：税前 {t['before_vat']}、税 {t['vat']}、含税 {t['net']}")


def block(payload: Dict) -> Dict:
    found = payload.get("mrerp") if isinstance(payload, dict) else None
    return found if isinstance(found, dict) else {}


def from_payload(doc_id: str, payload: Dict) -> Draft:
    """载荷 → 草稿单。缺的项不猜：缺品码的行留空码，引擎录到那一行时按对不上转人工。"""
    data = block(payload)
    totals = data.get("totals") or {}
    expected = {k: str(money(totals[k])) for k in ("before_vat", "vat", "net") if dec(totals.get(k)) is not None}
    if set(expected) != {"before_vat", "vat", "net"}:
        expected = {}
    lines = [Line(code=str(it.get("code") or "").strip(), qty=str(dec(it.get("qty")) or 1),
                  price=str(dec(it.get("price")) or 0), unit=str(it.get("unit") or "").strip(),
                  store=str(it.get("store") or "").strip(), desc=str(it.get("desc") or "").strip()[:200])
             for it in data.get("lines") or [] if isinstance(it, dict)]
    defaults = {k: str(v or "").strip() for k, v in (data.get("defaults") or {}).items() if isinstance(k, str)}
    return Draft(doc_id=str(doc_id), doc_type=str(data.get("doc_type") or ""), menu=str(data.get("menu") or "").strip(),
                 date=str(data.get("date") or "")[:10], party_code=str(data.get("party_code") or "").strip(),
                 lines=lines, docnum=str(data.get("docnum") or "").strip()[:18],
                 ref_no=str(data.get("ref_no") or "").strip()[:20], ref_date=str(data.get("ref_date") or "")[:10],
                 credit_days=str(data.get("credit_days") or "").strip(), vat_rate=str(data.get("vat_rate") or "").strip(),
                 vat_included=data.get("vat_included") is True, discount=str(dec(data.get("discount")) or 0),
                 defaults=defaults, expected=expected)


def connection_of(payload: Dict) -> Dict[str, str]:
    """这一批连哪个 MR.ERP、哪个账套（宿主装配会话用）。"""
    data = block(payload)
    account = data.get("account_set") or {}
    return {"url": str(data.get("url") or "").strip().rstrip("/"), "connection": str(data.get("connection") or ""),
            "comidyear": str(account.get("comidyear") or ""), "seldb": str(account.get("seldb") or "1"),
            "name": str(account.get("name") or ""), "company_name": str(account.get("company_name") or "")}
