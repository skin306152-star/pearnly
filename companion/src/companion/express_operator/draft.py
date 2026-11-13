# -*- coding: utf-8 -*-
"""一张要代录的单：由直写同一份载荷换来（上游准备、记住的选择都不变，只换执行方式）。

到这一步「账怎么记」已定，代录只管照着录。和直写的口径差别：
- 代录按明细录，要品码（``items[].code``）；没有品码的行在预检里报「商品主档没有」，不猜；
- 价格类型默认「不含税」（单价按税前录，和直写存税前价一致）；载荷的 ``operator.vat_included`` 为真时按含税价录
  （收据只印含税单价时用），这时明细单价存含税价，读回按价格类型换算后比。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional

CENT = Decimal("0.01")

# 单种 → 表头表、对方栏、对方单号栏（与 dbf_fields 同一口径：NETVAL 税前、VATAMT 税、NETAMT 含税）
DOC_TYPES = {
    "RR": {"side": "purchase", "head": "APTRN", "party": "SUPCOD", "ref": "REFNUM"},
    "HP": {"side": "purchase", "head": "APTRN", "party": "SUPCOD", "ref": "REFNUM"},
    "IV": {"side": "sales", "head": "ARTRN", "party": "CUSCOD", "ref": "YOUREF"},
    "HS": {"side": "sales", "head": "ARTRN", "party": "CUSCOD", "ref": "YOUREF"},
}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


def _dec(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _plain(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass
class Line:
    item_code: str
    qty: str
    unit_price: str
    loc: str = ""  # 空 = 用 Express 带出的默认仓
    unit: str = ""
    exempt: bool = False  # 免税行（载荷 vat_exempt）：Express 原生记 VATCOD '0'、单头与进项税记录记零税额 AMTRAT0

    @property
    def amount(self) -> Decimal:
        return money(Decimal(self.qty) * Decimal(self.unit_price))


@dataclass
class Draft:
    doc_id: str
    doc_type: str
    date: str  # 公历 ISO
    party_code: str
    lines: List[Line]
    ref_no: str = ""
    vat_rate: str = "7"
    vat_included: bool = False
    expected: Dict[str, str] = field(default_factory=dict)  # 载荷的 net / vat / total；空则按行算
    # 菜单落到多个命令的子菜单时选哪个（``menu.pick``）：站点记住的菜单文字、单据的付款方式（cash / transfer / 空）。
    # 载荷 ``operator.form_choice`` / ``operator.pay_route``，旧服务端不带就是空（定不下来停这一张、原因 menu_choice）
    form_choice: str = ""
    pay_route: str = ""
    # 载荷表头 ``srv_vattyp``（服务品税口径 1 权责 / 2 收付实现，别的值当没带）：存档途中 Express 问销项税口径时按它答（配方
    # ``button_by_srv_vattyp``），不带就按配方默认钮——行为同没有这个键时（11-13）
    srv_vattyp: str = ""

    def __post_init__(self):
        self.doc_type = self.doc_type.upper()
        self.lines = [line if isinstance(line, Line) else Line(**line) for line in self.lines]
        dt.date.fromisoformat(self.date)

    @property
    def spec(self) -> Dict[str, str]:
        return DOC_TYPES.get(self.doc_type, {})

    @property
    def side(self) -> str:
        return self.spec.get("side", "")

    @property
    def day(self) -> dt.date:
        return dt.date.fromisoformat(self.date)

    @property
    def date_be(self) -> str:
        """Express 的打法：日/月/佛历两位年。"""
        day = self.day
        return f"{day.day:02d}/{day.month:02d}/{(day.year + 543) % 100:02d}"

    def totals(self) -> Dict[str, Decimal]:
        if self.expected:
            return {k: money(v) for k, v in self.expected.items()}
        gross = sum((line.amount for line in self.lines), Decimal(0))
        rate = Decimal(self.vat_rate) / 100
        if self.vat_included:
            vat = money(gross * rate / (1 + rate))
            return {"net": gross - vat, "vat": vat, "total": gross}
        vat = money(gross * rate)
        return {"net": gross, "vat": vat, "total": gross + vat}

    @property
    def vat_mode(self) -> str:
        """表头「ประเภทราคา」：0 无增值税 / 1 含税 / 2 分开计税（10-29 实机读出的三项）。税额为 0、又没有免税行的才记 0
        （不收增值税的供应商：10-29 rec-laptop 原来按 2 录，Express 照 7% 算出税）。有免税行的（整单免税也一样）照 1 / 2 录：
        语料 158 本账套里免税单原生一律是 FLGVAT 1 / 2 + 明细 VATCOD '0' + 零税额 AMTRAT0，「按商品记增值税」开关开不开都如此。"""
        if self.totals()["vat"] == 0 and not any(line.exempt for line in self.lines):
            return "0"
        return "1" if self.vat_included else "2"

    def describe(self) -> str:
        """给模型看的一段说明（只有单据内容，不含任何凭据）。"""
        t = self.totals()
        rows = "\n".join(f"  {i + 1}. 品码 {l.item_code}  仓 {l.loc or '默认'}  数量 {l.qty}  单价 {l.unit_price}"
                         for i, l in enumerate(self.lines))
        return (f"单种 {self.doc_type}，日期 {self.date_be}，对方码 {self.party_code}"
                + (f"，对方单号 {self.ref_no}" if self.ref_no else "")
                + f"，价格{'含税' if self.vat_included else '不含税'}\n明细：\n{rows}\n"
                + f"合计：税前 {t['net']}、税 {t['vat']}、含税 {t['total']}")


def _be_to_iso(docdate_be: str) -> str:
    s = str(docdate_be or "").strip()
    if len(s) != 6 or not s.isdigit():
        raise ValueError(f"bad docdate_be: {docdate_be!r}")
    year = 2500 + int(s[0:2]) - 543
    return dt.date(year, int(s[2:4]), int(s[4:6])).isoformat()


def from_payload(doc_id: str, payload: Dict) -> Draft:
    """直写载荷 → 草稿单。缺品码的行留空码（预检报 item_missing），不在这里猜。"""
    doc_type = str(payload.get("doctype") or "").strip().upper()
    party = (payload.get("supplier") if DOC_TYPES.get(doc_type, {}).get("side") == "purchase"
             else payload.get("customer")) or {}
    head_loc = str(payload.get("loccod") or "").strip()
    options = payload.get("operator") or {}
    lines = []
    for it in payload.get("items") or []:
        qty = _dec(it.get("qty")) or Decimal(1)
        price = _dec(it.get("unit_price"))
        if price is None:
            amount = _dec(it.get("amount")) or Decimal(0)
            price = (amount / qty).quantize(Decimal("0.0001"), ROUND_HALF_UP) if qty else amount
        lines.append(Line(item_code=str(it.get("code") or "").strip(), qty=_plain(qty), unit_price=_plain(price),
                          loc=str(it.get("loccod") or head_loc).strip(), unit=str(it.get("unit") or "").strip(),
                          exempt=it.get("vat_exempt") is True))
    expected = {}
    for key, name in (("net", "base_amount"), ("vat", "vat_amount"), ("total", "total_amount")):
        value = _dec(payload.get(name))
        if value is not None:
            expected[key] = str(money(value))
    if set(expected) != {"net", "vat", "total"}:
        expected = {}
    ref_no = str(payload.get("ref_no") or "").strip()
    if DOC_TYPES.get(doc_type, {}).get("side") == "purchase":
        # 供应商票号进 REFNUM C(15)：照直写的缩法（去分隔符、再不够留后 15 字节），两种入账方式存进去的一样；
        # 不缩的话 Express 栏位只收前 15 字节，填完核对就对不上（10-27 实机 PDM-INV-0926-018-OP）
        from ..purchase_adapter import fit_ref_no

        ref_no = fit_ref_no(ref_no)
    return Draft(doc_id=str(doc_id), doc_type=doc_type, date=_be_to_iso(payload.get("docdate_be")),
                 party_code=str(party.get("code") or "").strip(), lines=lines,
                 ref_no=ref_no, vat_rate=str(payload["vat_rate"]) if payload.get("vat_rate") not in (None, "") else "7",
                 vat_included=options.get("vat_included") is True, expected=expected,
                 form_choice=str(options.get("form_choice") or "").strip()[:200],
                 pay_route=_pay_route(options.get("pay_route")),
                 srv_vattyp=_srv_vattyp(payload.get("srv_vattyp")))


def _srv_vattyp(value) -> str:
    choice = str(value if value is not None else "").strip()
    return choice if choice in ("1", "2") else ""


def _pay_route(value) -> str:
    route = str(value or "").strip().lower()
    return route if route in ("cash", "transfer") else ""
