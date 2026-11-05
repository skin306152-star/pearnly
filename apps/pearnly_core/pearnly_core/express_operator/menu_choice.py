"""代录子菜单多个候选时的站点一侧（多产品共用，纯函数，无框架、无 IO；评估 `docs/work/2026-11-05-cc-operator-menu-fallback-评估.md` 修法 D）。

各家公司定制过 Express 菜单，同一种单分几套单号（例：「ซื้อเงินสด-ทั่วไป」「ซื้อโดยโอนผ่าน TMB 2-01792-3」都是现购，区别是付款走现金
还是走哪家银行转账）。CC 桌面版落到子菜单、里面几个命令时依次：载荷 ``operator.form_choice``（这家这本账套这种单记住的菜单文字）
对上就用；否则按 ``operator.pay_route`` 在候选里找唯一一项；还定不下来停这一张，原因 ``menu_choice``，候选随回执带回。

- :func:`pay_route`：单据的付款文字（可多段）→ ``cash`` / ``transfer`` / ``""``。现金类、转账类（含 พร้อมเพย์ / QR、泰国各家银行
  泰文名与缩写）各认一类；两类都有或都没有回空（不猜，让 CC 停下问人）。
- :func:`parse_reason`：单据上记的回执原因 ``[OPERATOR_MENU_CHOICE] HP: 甲 | 乙`` → ``("HP", ["甲", "乙"])``。写法由 CC 桌面版
  ``engine.MenuChoice`` 定（原因原文 = ``单种: 候选 | 候选``），产品照抄到推送记录与单据上；回执 ``meta.operator.menu_options``
  过不了共用回执净化（``express_rules.push.common.sanitize_push_meta`` 只留白名单），所以站点从原因原文读。
- :func:`option_code`：候选文字 → 作答与记住用的值（选择记忆的值上限 :data:`CHOICE_MAX` 字；更长的截断，CC 按「互相包含且唯一」
  仍认得出是哪一项）。
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Sequence, Tuple

KIND = "operator_form"     # 选择记忆的类：按「连接 + 账套」记，归类键 = 单据种类（HP / RR / …），值 = 选中的菜单文字
REASON = "menu_choice"     # CC 桌面版回执的原因码（``contracts.REASONS``）
CASH, TRANSFER = "cash", "transfer"
PAY_ROUTES = (CASH, TRANSFER)
CHOICE_MAX = 60            # 同 Cowork 选择记忆的值上限（`push/choices.record`）
OPTIONS_MAX = 20           # 一个子菜单最多认几项（CC 回执原文本身限 300 字）
DOC_TYPE_MAX = 10

# 现金类：泰文按包含比（「ชำระเงินสด」「จ่ายเงินสด」都含 เงินสด），英文按整词比
CASH_WORDS = ("เงินสด",)
CASH_CODES = ("cash",)
# 转账类：转账 / 银行 / PromptPay 字样与各家银行的泰文名（按包含比）
TRANSFER_WORDS = (
    "โอน", "ธนาคาร", "พร้อมเพย์", "คิวอาร์",
    "กสิกร", "ไทยพาณิชย์", "กรุงเทพ", "กรุงไทย", "กรุงศรี", "ทหารไทย", "ธนชาต", "ทีเอ็มบี", "ทีทีบี", "ออมสิน",
    "ยูโอบี", "ซีไอเอ็มบี", "เกียรตินาคิน", "ทิสโก้", "แลนด์แอนด์เฮ้าส์", "อาคารสงเคราะห์", "ธ.ก.ส", "ไอซีบีซี",
    "ไทยเครดิต",
)
# 英文与缩写按整词比（免得撞上别的词：KBANK 里的 bank 不单算，bay 不撞 ebay）
TRANSFER_CODES = (
    "transfer", "transferred", "bank", "banking", "promptpay", "prompt pay", "qr", "qrcode",
    "tmb", "ttb", "kbank", "kasikorn", "kasikornbank", "scb", "bbl", "ktb", "krungthai", "bay", "krungsri", "gsb", "uob",
    "cimb", "lhb", "lhbank", "kkp", "tisco", "ghb", "baac", "icbc", "tcrb",
)


def _norm(text) -> str:
    """比对用：NFC、小写、去首尾空白（泰文名的空格在 :func:`_is_transfer` 里去掉再比）。"""
    return unicodedata.normalize("NFC", str(text or "")).casefold().strip()


def _has_code(text: str, codes: Sequence[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])", text) for code in codes)


def _is_cash(text: str) -> bool:
    return any(word in text for word in CASH_WORDS) or _has_code(text, CASH_CODES)


def _is_transfer(text: str) -> bool:
    joined = text.replace(" ", "")
    return any(word in joined for word in TRANSFER_WORDS) or _has_code(text, TRANSFER_CODES)


def pay_route(*texts) -> str:
    """单据的付款文字（识别出的付款方式、ERP 单据的「付款方式」等，可多段，空的不算）→ ``cash`` / ``transfer`` / ``""``。
    几段合起来看：只认出现金类 → cash；只认出转账类 → transfer；两类都有、都没有 → 空。"""
    found = set()
    for raw in texts:
        text = _norm(raw)
        if not text:
            continue
        if _is_cash(text):
            found.add(CASH)
        if _is_transfer(text):
            found.add(TRANSFER)
    return found.pop() if len(found) == 1 else ""


_REASON = re.compile(r"^\s*\[OPERATOR_MENU_CHOICE\]\s*(?P<doc_type>[A-Za-z0-9]{1,%d})\s*:\s*(?P<options>.*)$" % DOC_TYPE_MAX,
                     re.S)


def parse_reason(raw) -> Tuple[str, List[str]]:
    """单据上记的回执原因 → (单据种类（大写）, 候选菜单文字)；不是 ``menu_choice`` 或读不出候选回 ``("", [])``。
    候选去空、去重（保持原顺序），最多 :data:`OPTIONS_MAX` 项。"""
    found = _REASON.match(str(raw or ""))
    if not found:
        return "", []
    options: List[str] = []
    for part in found.group("options").split(" | "):
        part = " ".join(part.split())
        if part and part not in options:
            options.append(part)
    options = options[:OPTIONS_MAX]
    return (found.group("doc_type").upper(), options) if options else ("", [])


def option_code(label) -> str:
    """候选文字 → 作答 / 记住用的值（空白归一，截到 :data:`CHOICE_MAX` 字）。"""
    return " ".join(str(label or "").split())[:CHOICE_MAX].strip()


__all__ = ["CASH", "CHOICE_MAX", "KIND", "OPTIONS_MAX", "PAY_ROUTES", "REASON", "TRANSFER", "option_code",
           "parse_reason", "pay_route"]
