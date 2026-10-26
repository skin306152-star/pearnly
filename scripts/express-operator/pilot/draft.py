"""草稿单：理解层的产物，到这一步「账怎么记」已定，执行层只管照着录。

试点里草稿单由语料的 Express 标准答案换到试点账套（69EXP\\test）的主档上得到（见 corpus.py），
所以「理解层」视为全对，测的只是「录进去」这一步。
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Optional

DOC_TYPES = {
    "RR": {"name": "赊购", "side": "purchase", "head": "APTRN", "party": "SUPCOD", "ref": "REFNUM"},
    "HP": {"name": "现购", "side": "purchase", "head": "APTRN", "party": "SUPCOD", "ref": "REFNUM"},
    "IV": {"name": "赊销", "side": "sales", "head": "ARTRN", "party": "CUSCOD", "ref": "YOUREF"},
    "HS": {"name": "现销", "side": "sales", "head": "ARTRN", "party": "CUSCOD", "ref": "YOUREF"},
}
CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


@dataclass
class Line:
    item_code: str
    qty: str
    unit_price: str
    loc: str = ""  # 空 = 用 Express 带出的默认仓

    @property
    def amount(self) -> Decimal:
        return money(Decimal(self.qty) * Decimal(self.unit_price))


@dataclass
class Draft:
    doc_id: str
    doc_type: str
    date: str  # 公历 ISO，录入时换成 Express 的 日/月/佛历两位年
    party_code: str
    lines: List[Line]
    ref_no: str = ""
    remark: str = ""  # 泰文备注（走剪贴板粘贴）
    vat_rate: str = "7"
    vat_included: bool = False  # 单价是否含税（现销 HS 默认「1-รวม VAT」）
    expected: Dict[str, str] = field(default_factory=dict)  # 标准答案给的 net / vat / total；空则按行算
    source: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.doc_type not in DOC_TYPES:
            raise ValueError(f"不支持的单种 {self.doc_type}")
        self.lines = [line if isinstance(line, Line) else Line(**line) for line in self.lines]
        if not self.lines:
            raise ValueError(f"{self.doc_id} 没有明细行")
        dt.date.fromisoformat(self.date)

    @property
    def spec(self) -> Dict[str, str]:
        return DOC_TYPES[self.doc_type]

    @property
    def date_be(self) -> str:
        day = dt.date.fromisoformat(self.date)
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

    def to_json(self) -> Dict:
        return asdict(self)

    def describe(self) -> str:
        """给模型看的一段说明（中文 + 原值），不含任何凭据。"""
        t = self.totals()
        rows = "\n".join(f"  {i + 1}. 品码 {l.item_code}  仓 {l.loc or '默认'}  数量 {l.qty}  单价 {l.unit_price}"
                         for i, l in enumerate(self.lines))
        return (f"单种 {self.doc_type}（{self.spec['name']}），日期 {self.date_be}，对方码 {self.party_code}"
                + (f"，对方单号 {self.ref_no}" if self.ref_no else "") + (f"，备注 {self.remark}" if self.remark else "")
                + f"\n明细：\n{rows}\n合计：税前 {t['net']}、税 {t['vat']}、含税 {t['total']}")


def load(path: Path) -> List[Draft]:
    """一行一张草稿单（JSONL）。"""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Draft(**json.loads(line)))
    return out


def dump(drafts: List[Draft], path: Path) -> None:
    Path(path).write_text("".join(json.dumps(d.to_json(), ensure_ascii=False) + "\n" for d in drafts),
                          encoding="utf-8")


def samples() -> List[Draft]:
    """离线自检用的四种单各一张（码是虚构的，只在模拟器里用）。"""
    return [
        Draft("S-RR-1", "RR", "2026-10-20", "P001", [Line("501780", "5", "120.00"), Line("500310", "2", "35.50", "02")],
              ref_no="INV-7781", remark="ซื้อสินค้าทดสอบ"),
        Draft("S-HP-1", "HP", "2026-10-21", "P002", [Line("501871", "1", "999.00")], ref_no="RC-1102"),
        Draft("S-IV-1", "IV", "2026-10-22", "5001", [Line("501780", "3", "180.00")]),
        Draft("S-HS-1", "HS", "2026-10-23", "CASH", [Line("500310", "4", "53.50")], vat_included=True),
    ]
