"""识别读出的值在原件 PDF 文字层里找不找得到原文（多产品共用）。

纯函数：只依赖标准库，不依赖 Frappe、HTTP 或任何产品 App。原在 Cowork ``push.express.automation``（自动推前的核对、
同一张票的副本页 ``same_document_page``），2026-10-20 原样搬到共用层，给 Claude 识别的硬性自检（``claude_ocr.rules``：
多页电子 PDF 后面几页是副本 / 收据联时不升 Opus）用同一个判据；Cowork 那边改为从这里导入，名字照旧导出。
甲骨文的 claude-ocr 服务也带这个文件（``scripts/claude-ocr/deploy.sh``）。
"""
import re
from decimal import Decimal, InvalidOperation

# 单号至少这么多个字母数字才拿来在文字层里找（太短，随便一页都撞得上）
NUMBER_MIN = 4
# 减项 / 增项通知单的标题（同 `pearnly_core.tax_documents.rules` 的标题表）：同号同额的减项单不是副本
ADJUSTMENT_THAI = ("ใบลดหนี้", "ใบเพิ่มหนี้")
ADJUSTMENT_ENGLISH = ("CREDIT NOTE", "DEBIT NOTE")

# 一个完整的数：前后不连着数字（逗号后跟数字也算连着），千分位逗号可有可无
_NUMBER = re.compile(r"(?<![\d.])(?<!\d,)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|,\d|\.\d)")


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _money(value):
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def amount_in_text(amount, text) -> bool:
    """金额在文字层里作为一个完整的数出现（千分位逗号可有可无，小数位数不拘：1,070.00 / 1070 / 1070.0 都算）。"""
    wanted = _money(amount)
    if wanted is None or not text:
        return False
    for token in _NUMBER.findall(str(text)):
        found = _money(token)
        if found is not None and found == wanted:
            return True
    return False


def tax_in_text(tax, text) -> bool:
    """13 位税号在文字层里能找到原文（数字之间可夹空格、连字号：0-1055-12345-67-8 也算）。"""
    number = _digits(tax)
    if len(number) != 13 or not text:
        return False
    pattern = r"(?<!\d)" + r"[\s\-]?".join(number) + r"(?!\d)"
    return re.search(pattern, str(text)) is not None


def number_in_text(number, text) -> bool:
    """单号在文字层里作为一个完整的号出现：逐字相同（不分大小写，字之间可夹一个空白），前后不连着字母或数字。字母数字不到
    `NUMBER_MIN` 个的号不认（太短，随便一页都撞得上）。"""
    key = re.sub(r"\s+", "", str(number or ""))
    if len(re.sub(r"[^0-9A-Za-z]", "", key)) < NUMBER_MIN or not text:
        return False
    pattern = r"(?<![0-9A-Za-z])" + r"\s?".join(re.escape(char) for char in key) + r"(?![0-9A-Za-z])"
    return re.search(pattern, str(text), re.IGNORECASE) is not None


def adjustment_note(text) -> bool:
    """文字层里有减项 / 增项通知单的标题（ใบลดหนี้ / ใบเพิ่มหนี้ / CREDIT NOTE / DEBIT NOTE）。"""
    text = str(text or "")
    thai = re.sub(r"\s+", "", text)
    english = " " + re.sub(r"[^A-Z]+", " ", text.upper()) + " "
    return any(name in thai for name in ADJUSTMENT_THAI) or any(f" {name} " in english for name in ADJUSTMENT_ENGLISH)


__all__ = ["ADJUSTMENT_ENGLISH", "ADJUSTMENT_THAI", "NUMBER_MIN", "adjustment_note", "amount_in_text", "number_in_text",
           "tax_in_text"]
