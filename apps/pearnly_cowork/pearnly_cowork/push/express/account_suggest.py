"""要人选费用科目时先给的建议（科目建议 B + C，用户 2026-10-13 批准）：确定性匹配，不调用模型。纯计算，不连库。

只在这套账科目表里可记账的费用类科目里挑（CC 桌面端报上来的科目表本来只有可记账的；费用类按 Express 科目类型
GROUP = 5 核，没报类型的按编号大类 5 核，``1111-00`` 与 ``11-01-00-00`` 两种编号形状一样），最多 ``LIMIT`` 个，按来源排：

1. ``hint``：会计员在对话里明说的费用类别（如「ค่าเครื่องเขียน」），命中的科目排第一（第 4、6 条，2026-10-13 录制）；
2. ``history``：这本账套近 12 个月这家供应商的单据常记的科目（`readback.history` 经钩子交来的次数）；
3. ``name``：票面的类别、品名、卖方名与科目名对得上——同属一个常见费用类（下面的泰英关键词表），或名称里有同一个词。

建议只是排在前面、网页预选第一项：仍要会计员确认一次（可以一句话确认），确认后照旧按「公司 + 供应商或类别」记住
（`push/choices.py`）。没有一个对得上就不给，照现在问一次。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

LIMIT = 2
HINT_MAX = 120
EXPENSE_GROUP = "5"
HINT, HISTORY, NAME = "hint", "history", "name"

# 常见费用类：(类名, 词, 先从文字里去掉再找的词)。词同时用来认票面 / 会计员的话与科目名；英文词按整词找。
# 「先去掉」防误认：ค่าน้ำมัน（油费）里有 ค่าน้ำ（水费），ค่าส่งเสริมการขาย（促销）里有 ค่าส่ง（运费）。
CATEGORIES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("electricity", ("ไฟฟ้า", "ค่าไฟ", "กฟภ", "กฟน", "electricity", "electric"), ("ไฟแนนซ์",)),
    ("water", ("น้ำประปา", "ประปา", "ค่าน้ำ", "water supply", "waterworks"),
     ("น้ำมัน", "น้ำแข็ง", "น้ำดื่ม", "น้ำยา")),
    ("rent", ("ค่าเช่า", "เช่าพื้นที่", "เช่าอาคาร", "เช่าสำนักงาน", "rent", "rental", "lease"), ("เช่าซื้อ",)),
    ("telecom", ("โทรศัพท์", "อินเทอร์เน็ต", "อินเตอร์เน็ต", "อินเทอร์เนต", "internet", "telephone", "phone",
                 "mobile", "broadband", "ทรูมูฟ", "ดีแทค", "dtac", "3bb", "ais"), ()),
    ("fuel", ("น้ำมัน", "เชื้อเพลิง", "ดีเซล", "เบนซิน", "แก๊สโซฮอล", "gasohol", "diesel", "fuel", "petrol",
              "ปตท", "บางจาก", "bangchak", "shell", "caltex", "esso", "susco", "ซัสโก้"),
     ("น้ำมันพืช", "น้ำมันเครื่องสำอาง")),
    ("freight", ("ขนส่ง", "ค่าส่ง", "จัดส่ง", "ไปรษณีย์", "freight", "shipping", "delivery", "courier", "logistics",
                 "postage", "kerry", "เคอรี่", "แฟลช"), ("ส่งเสริม",)),
    ("stationery", ("เครื่องเขียน", "แบบพิมพ์", "วัสดุสำนักงาน", "เครื่องใช้สำนักงาน", "วัสดุสิ้นเปลือง", "ปากกา",
                    "ดินสอ", "กระดาษ", "แฟ้ม", "หมึก", "stationery", "office supplies", "office supply", "toner",
                    "paper"), ()),
    ("advertising", ("โฆษณา", "ประชาสัมพันธ์", "การตลาด", "advertising", "advertisement", "marketing",
                     "facebook"), ()),
    ("professional", ("วิชาชีพ", "ทำบัญชี", "สอบบัญชี", "ที่ปรึกษา", "ทนายความ", "กฎหมาย", "professional",
                      "accounting", "audit", "consulting", "consultant", "legal"), ()),
    ("repair", ("ซ่อม", "บำรุงรักษา", "repair", "maintenance"), ()),
    ("cleaning", ("ทำความสะอาด", "รักษาความสะอาด", "แม่บ้าน", "cleaning"), ()),
    ("software", ("ซอฟต์แวร์", "ซอฟท์แวร์", "ซอฟแวร์", "โปรแกรม", "software", "subscription", "license", "licence",
                  "cloud"), ()),
    ("travel", ("เดินทาง", "ทางด่วน", "แท็กซี่", "ค่าโดยสาร", "ตั๋วเครื่องบิน", "travel", "taxi", "toll",
                "airline"), ()),
    ("insurance", ("ประกันภัย", "เบี้ยประกัน", "insurance"), ("ประกันสังคม",)),
)
# 名称词对得上时不算的泛词（费用、其他、服务、公司名的法人字样……）
STOP = frozenset((
    "ค่าใช้จ่าย", "ใช้จ่าย", "อื่นๆ", "อื่น", "ทั่วไป", "บริการ", "ค่าบริการ", "บริษัท", "จำกัด", "มหาชน", "สาขา",
    "สำนักงานใหญ่", "ห้างหุ้นส่วน", "ร้าน", "สินค้า", "ซื้อ", "ค่าซื้อ", "รายจ่าย", "เบ็ดเตล็ด", "ต่างๆ",
    "expense", "expenses", "other", "others", "general", "service", "services", "company", "limited", "ltd",
    "fee", "fees", "charge", "charges", "cost", "costs", "and", "the", "for", "misc", "sundry", "co"))
# 名称词至少这么长才比（泰文按不含声调 / 上下元音的字数）：太短的词（น้ำ、ไฟ）只经关键词表认
WORD_MIN = 4
_SPLIT = re.compile(r"[\s\d.,;:()\[\]{}/\\|+*&#@!?\"'“”‘’<>=_\-–—~%$฿]+|และ")
_ASCII = re.compile(r"^[a-z0-9 ]+$")


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).lower().replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _size(word: str) -> int:
    return sum(1 for ch in word if unicodedata.category(ch) != "Mn")


def _found(term: str, text: str) -> bool:
    """``term`` 在 ``text`` 里：英文词按整词，泰文按子串。"""
    if _ASCII.fullmatch(term):
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None
    return term in text


def _categories(text: str) -> Dict[str, str]:
    """文字里认得出的常见费用类：{类名: 认出它的那个词}。"""
    out = {}
    for name, terms, blocked in CATEGORIES:
        cleaned = text
        for word in blocked:
            cleaned = cleaned.replace(word, " ")
        hit = next((term for term in terms if _found(term, cleaned)), None)
        if hit:
            out[name] = hit
    return out


def _words(text: str) -> List[str]:
    out = []
    for word in _SPLIT.split(text):
        word = word[3:] if word.startswith("ค่า") else word  # 「ค่า…」（…费）只看后面的
        if word and word not in STOP and _size(word) >= WORD_MIN and word not in out:
            out.append(word)
    return out


def expense(row: Mapping[str, Any]) -> bool:
    """可记账科目里的费用类：有 Express 科目类型（GROUP）按类型，没有按编号大类。"""
    code = str(row.get("code") or "").strip()
    kind = str(row.get("type") or row.get("group") or "").strip()
    if not code:
        return False
    return kind == EXPENSE_GROUP if kind else code[:1] == EXPENSE_GROUP


def _chart(accounts: Iterable) -> Dict[str, str]:
    return {str(row["code"]).strip(): str(row.get("name") or "").strip()
            for row in accounts or () if isinstance(row, Mapping) and expense(row)}


def _scored(text: str, chart: Mapping[str, str]) -> List[Tuple[int, str, str]]:
    """文字与每个费用科目对得上的程度：[(分, 科目, 对上的词)]，分高的在前；对不上的不列。"""
    text = _norm(text)
    if not text:
        return []
    wanted, words = _categories(text), _words(text)
    out = []
    for code, name in chart.items():
        label = _norm(name)
        score, term = 0, ""
        shared = [category for category in _categories(label) if category in wanted]
        if shared:
            score, term = 3, wanted[shared[0]]
        overlap = [word for word in words if _found(word, label)]
        overlap += [word for word in _words(label) if word not in overlap and _found(word, text)]
        if overlap:
            score += min(len(overlap), 2)
            term = term or overlap[0]
        if score:
            out.append((score, code, term))
    out.sort(key=lambda item: (-item[0], item[1]))
    return out


def _document_text(fields: Mapping[str, Any]) -> str:
    items = [str(item.get("name") or "") for item in fields.get("items") or [] if isinstance(item, Mapping)]
    return " ".join([str(fields.get("category") or ""), *items[:30], str(fields.get("seller_name") or "")])


def suggest(fields: Optional[Mapping[str, Any]], *, accounts: Sequence, history: Sequence = (), hint: Any = "",
            limit: int = LIMIT) -> List[Dict[str, Any]]:
    """这张票的费用科目建议 ``[{code, name, source, reason}]``（最多 ``limit`` 个，最该选的在前）。

    ``accounts``：这套账的科目表（`reported_accounts`，``{code, name, type}``）；``history``：这家供应商近 12 个月常用的
    科目 ``[{code, docs, last}]``（次数多的在前）；``hint``：会计员在对话里说的费用类别。"""
    fields = fields if isinstance(fields, Mapping) else {}
    chart = _chart(accounts)
    if not chart:
        return []
    used = {str(row.get("code") or ""): row for row in history or () if isinstance(row, Mapping)}
    out: List[Dict[str, Any]] = []

    def add(code, source, reason):
        if code in chart and all(item["code"] != code for item in out):
            out.append({"code": code, "name": chart[code], "source": source, "reason": reason})

    note = str(hint or "").strip()[:HINT_MAX]
    if note:
        matched = _scored(note, chart)
        # 会计员说的类别对上几个科目：这家用过的在前
        matched.sort(key=lambda item: (-int(item[1] in used), -item[0], item[1]))
        for _score, code, _term in matched:
            add(code, HINT, f'Matches what the user said: "{note}"')
    for code, row in used.items():
        docs = int(row.get("docs") or 0)
        if docs > 0:
            add(code, HISTORY, f"Used on {docs} of this supplier's documents in the last 12 months")
    for _score, code, term in _scored(_document_text(fields), chart):
        add(code, NAME, f'Account name matches "{term}" on the document')
    return out[:max(int(limit), 0)]


__all__ = ["CATEGORIES", "HINT", "HISTORY", "LIMIT", "NAME", "expense", "suggest"]
