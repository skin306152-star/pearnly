# -*- coding: utf-8 -*-
"""订车对话里**付款字段**的原生问法（与 `dms_qa_cards` 互相引用，见下）。

渠道银行目录有两副面孔：

· 目录**有行** → 给选择按钮（quick reply）；
· 目录**权威为空** → 回到可填写文本的流程（银行名称 + 原生必要资料），
  **不再报"读取失败"**。

读取失败另有路径（主档快照抛错 → `master_problem` 卡），**不许在这里冒充空目录**。

⚠️ 与线上同样的**循环引用**：`dms_qa_cards` 要 `ask_pay_ref` / `ask_payment_bank`，
而这里要用 `dms_qa_cards` 的 `_msg` / `_option_text` / `_pick_rows`。
线上的办法是——本模块只在**函数体内** import 对方；照做。

照搬老产品 `services/line_dms/qa_payment_cards.py`（线上基准 `501589f0`），逻辑未改。
"""

from __future__ import annotations

from pearnly_dms.booking.conversation.qa_util import qat

# 一行资料里的字段分隔符提示：与 `dms_text_fields` 支持的规则同一套
# （半/全角竖线、英文/中文逗号、顿号、斜杠、居中点、留白点号）。
# 文案只列最常用的四种，**不再声称"只能用 |"**——用户怎么写都能收，
# 只在真的拆不出来时才重问。
TXT_SEP_HINT = qat("TXT_SEP_HINT")

# 该渠道银行目录权威为空时的兼容问法：多问一项银行名称（隐藏 bank id 允许为空）。
# ⚠️ 与上面 `TXT_ASK_CHEQUE_REF` 同一个坑：线上尾句前有个**换行**，
# 而语言包读取器对每条值做 `strip()`，行尾换行存不进 CSV —— 换行必须在这里补。
# （两条线上是 `"…KBank\n" + TXT_SEP_HINT` / `"…VISA\n" + TXT_SEP_HINT`。）
TXT_ASK_CHEQUE_REF_MANUAL = qat("TXT_ASK_CHEQUE_REF_MANUAL") + "\n" + TXT_SEP_HINT
TXT_ASK_CARD_REF_MANUAL = qat("TXT_ASK_CARD_REF_MANUAL") + "\n" + TXT_SEP_HINT


def ask_pay_ref(channel: str, manual_bank: bool = False) -> dict:
    """渠道补充信息的问法：分类查 `CHANNEL_EXTRA_SHAPE` 单表，不在各处硬编码渠道子集。

    `manual_bank` = 该渠道银行目录权威为空（手工填名称）时的问法。
    """
    from pearnly_dms.booking.conversation.qa_cards import (
        TXT_ASK_CARD_REF,
        TXT_ASK_CHEQUE_REF,
        TXT_ASK_OTHER_REF,
        _msg,
    )
    from pearnly_dms.booking.conversation.qa_util import CHANNEL_EXTRA_SHAPE

    if CHANNEL_EXTRA_SHAPE.get(channel) != "ref":
        return _msg(TXT_ASK_OTHER_REF)
    if manual_bank:
        return _msg(TXT_ASK_CARD_REF_MANUAL if channel == "card" else TXT_ASK_CHEQUE_REF_MANUAL)
    return _msg(TXT_ASK_CARD_REF if channel == "card" else TXT_ASK_CHEQUE_REF)


def ask_payment_bank(banks: list, page: int = 0, channel: str = "") -> dict:
    """选收款银行；目录权威为空 → 退回手工填问法（不报"读取失败"）。"""
    from pearnly_dms.masters.company_banks import company_bank_label, sort_bank_rows
    from pearnly_dms.booking.conversation.qa_cards import _msg, _option_text, _pick_rows

    banks = sort_bank_rows(banks)
    if not banks:
        return ask_pay_ref(channel, manual_bank=True)
    return _msg(
        # ⚠️ 这句是**内联字面量**（不是 TXT_ASK_PAY_DST 那句），照线上原样进包。
        _option_text(qat("TXT_PICK_BANK"), banks, company_bank_label, page),
        _pick_rows(banks, "paybank", company_bank_label, page),
    )
