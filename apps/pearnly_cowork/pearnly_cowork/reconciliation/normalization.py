"""Pure normalization from legacy ec2e5c957e6b field_comparator.py.

Function bodies retain deployed behavior, including Buddhist two-digit years,
invoice prefixes and leading zeroes. Similarity is evidence, never an automatic
reconciliation match. No amount comparison is migrated in this module.
"""


import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Optional


_HQ_ALIASES = {"00000", "สำนักงานใหญ่", "สนญ", "head office", "hq", "หลัก", "สำนักงานหลัก"}


def normalize_str(s) -> str:
    """去首尾空格 · 全角→半角 · 大小写折叠"""
    if not s:
        return ""
    return unicodedata.normalize("NFKC", str(s)).strip().lower()


def normalize_name(s) -> str:
    """v118.32.2.5 · 姓名/公司名标准化:去所有空格(适合泰文/中文/日文 OCR 排版差) + 全角半角 + 大小写"""
    if not s:
        return ""
    v = unicodedata.normalize("NFKC", str(s)).lower()
    return re.sub(r"\s+", "", v)


def normalize_invoice_no(s) -> str:
    """v118.32.4.9 · 改"逐字段对照"模式 · 只做纯视觉归一化(空格/连字符/斜杠/大小写)
    不再剥离 INV/IV/TAX 前缀 —— 那是替用户判定"INV 和 IV 是同一笔"违反新铁律
    前导零也保留(用户视觉看到的写法)"""
    if not s:
        return ""
    return normalize_str(s).replace(" ", "").replace("-", "").replace("/", "")


def normalize_tax_id(s) -> str:
    """去连字符和空格,只留数字"""
    return re.sub(r"[^0-9]", "", str(s or ""))


def normalize_branch(s) -> str:
    """总部多种写法归一为 '00000'"""
    if not s:
        return "00000"
    n = normalize_str(s).replace(" ", "")
    # 别名集合走同款归一化(NFKC + lower + 去空格)再比 ·
    # 否则带空格的 "head office" / 泰文 "สำนักงานใหญ่"(NFKC 拆 ำ)永远匹配不上
    hq_norm = {normalize_str(v).replace(" ", "") for v in _HQ_ALIASES}
    if n in hq_norm or n == "":
        return "00000"
    if n.isdigit():
        return n.zfill(5)
    return n


def _thai_to_gregorian(year: int) -> int:
    """佛历年→西历年。
    · 2 位年(<100):泰国财税文档一律佛历缩写(25xx),2500+yy 佛历 → 西历(=1957+yy);
      如 69→พ.ศ.2569→2026。Python %y 会把这类年扩成 19xx/20xx 西历,须先取回 2 位再折算。
    · 4 位佛历(>2400):-543。
    · 4 位西历:原样返回。
    """
    if year < 100:
        return 1957 + year  # 2500 + yy − 543
    return year - 543 if year > 2400 else year


_EXCEL_EPOCH = date(1899, 12, 30)
_WITH_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?$")
_SERIAL = re.compile(r"^\d{5}(\.0+)?$")


def _excel_serial(number) -> Optional[date]:
    """Excel 日期格子的序列号（.xls 读出来是数）：只认 1954–2119 这一段，别的数不当日期。"""
    return _EXCEL_EPOCH + timedelta(days=int(number)) if 20000 <= number < 80000 else None


def parse_date(s) -> Optional[date]:
    """解析日期字符串,支持多种格式 + 佛历自动转换。
    2 位年份按泰国财税惯例视作佛历缩写(25xx),不套用 Python %y 的西历假设。

    表格里真正的日期格子也认（2026-09-28 录制发现税表日期显示成「2026-01-05 00:00:00」）：
    日期对象、带时间的 年-月-日、.xls 的日期序列号。
    """
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return _excel_serial(s)
    if not s:
        return None
    s = str(s).strip()
    # 「01-02-69」「01.02.2569」也认（2026-09-29 压测 I04：Express 打印的 95 页税报日期是 日-月-2 位年，原样留着
    # 「01-02-69」，按日期排序、配对都落空）。
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y",
                "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y"):
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        # %y 已被 strptime 扩成 4 位西历(69→1969),取回 2 位交佛历折算
        year = d.year % 100 if "%y" in fmt else d.year
        return d.replace(year=_thai_to_gregorian(year))
    with_time = _WITH_TIME.match(s)
    if with_time:
        return parse_date(with_time.group(1))
    if _SERIAL.match(s):
        return _excel_serial(float(s))
    return None


def fuzzy_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def levenshtein(a: str, b: str) -> int:
    """v118.32.4.9.6 · 编辑距离 · 用于税号 fuzzy 疑似匹配(Bug 4)
    a/b 都是数字串(已 normalize_tax_id 过)· 不考虑 unicode 重码"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def tax_id_fuzzy_distance(t1: str, t2: str) -> int:
    """v118.32.4.9.6 · 税号 fuzzy 距离(Bug 4)
    两边都先 normalize_tax_id · 返回编辑距离
    距离 ≤ 2 → 调用方标"疑似匹配 · 请确认" · 不直接判定"""
    n1 = normalize_tax_id(t1)
    n2 = normalize_tax_id(t2)
    if not n1 or not n2:
        return 99
    return levenshtein(n1, n2)


def mod11_check(tax_id: str) -> bool:
    """泰国 13 位税号 Mod-11 校验"""
    digits = normalize_tax_id(tax_id)
    if len(digits) != 13:
        return False
    try:
        total = sum(int(d) * w for d, w in zip(digits, range(13, 1, -1)))
        return (11 - total % 11) % 10 == int(digits[-1])
    except Exception:
        return False
