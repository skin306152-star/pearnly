# -*- coding: utf-8 -*-
"""DMS LINE 文本分发：全局命令 → 状态分支 → 手机号捕获 → 缺料提示。

**分发顺序是这条对话流的安全属性**：全局命令**先于任何状态判定**。
老产品在这里踩过坑并写进注释：编辑态一度对文本有优先占有权，结果用户改姓名时打
「เมนู」被当成新姓名写进 id_card 并重跑查重。编辑态只是众多状态之一，
**不该独占文本**。

会话在入口只读一次，沿分支透传 —— 同一条消息不重复读库。

### 两处**未搬**（老产品有，属于别的层）

· **菜单层**：`เมนู` / 问候语本该弹菜单卡（建档 / 订车 / 查询 / 改密）。现在统一回
  「发身份证开始建档」—— 这是泰方已定过的原文，且**说的就是现在唯一能做的事**：
  不发明新的泰文话术，也不给一个点了没反应的按钮。
· **查询 / 逐字段修正 / 订车逐问**：各自的分支还没搬。文本落到这里时按"缺料提示"处理，
  不会静默吞掉。
"""

from __future__ import annotations

import logging
from typing import Optional

from pearnly_core import line
from pearnly_core import line_session as store
from pearnly_dms import dms_cards as cards
from pearnly_dms import dms_commands as commands

logger = logging.getLogger(__name__)


def route(binding: dict, line_user_id: str, reply_token: str, text: str) -> None:
    cmd = commands.classify(text)  # 纯函数、零 IO：重置路径压根不必读会话

    if cmd == commands.CMD_RESET:
        store.clear_session(line_user_id)
        line.reply(reply_token, [{"type": "text", "text": cards.TXT_RESET}])
        return

    sess = store.get_session(line_user_id)

    if cmd in (commands.CMD_MENU, commands.CMD_GREETING):
        # 菜单层未搬：回"发身份证开始建档"（泰方原文，且正是现在唯一能做的事）。
        # 记一条 warning，免得以后有人以为是漏了分支。
        logger.warning("[dms router] 菜单层未搬，เมนู 命令暂回建档引导")
        line.reply(reply_token, [{"type": "text", "text": cards.TXT_INTRO}])
        return

    if any(ch.isdigit() for ch in text):
        capture_phone(binding, line_user_id, reply_token, sess, text)
        return

    if not sess:
        # 菜单卡未搬：直接给建档引导（老产品这里弹菜单卡）
        line.reply(reply_token, [{"type": "text", "text": cards.TXT_INTRO}])
    else:
        line.reply(reply_token, [{"type": "text", "text": nudge(sess)}])


def capture_phone(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], text: str
) -> None:
    """手机号**透传**：DMS 是权威，它吃什么就送什么，不在我们这边写死格式。

    "含数字即视为号码"是**纯路由判据**（用来区分号码和闲聊）；格式对不对由 DMS
    保存时裁决 —— 我们自己加格式校验只会把 DMS 认得的号码挡在外面。
    """
    from pearnly_dms import dms_flow as flow

    payload = flow.merge_session(
        binding,
        line_user_id,
        {"phone": text},
        keep=("id_card", "id_card_mid", "endpoint_id", "mode"),
        sess=sess,
    )
    if not payload.get("id_card"):
        line.reply(reply_token, [{"type": "text", "text": cards.TXT_ASK_CARD}])
        return
    flow._enqueue(
        "dedup",
        flow.run_dedup,
        binding,
        line_user_id,
        payload["id_card"],
        text,
        payload.get("endpoint_id"),
    )


def nudge(sess: dict) -> str:
    """按当前收料进度给下一步提示。

    只管"还缺材料"的态：已经建档的用户再听到"请发身份证和手机号"，
    就是要他**重付一次 OCR**，所以话术必须跟着进度走。
    """
    if sess.get("state") == "reviewing":
        return cards.TXT_PICK_ABOVE
    payload = sess.get("payload") or {}
    if payload.get("id_card") and not payload.get("phone"):
        return cards.TXT_ASK_PHONE
    if payload.get("phone") and not payload.get("id_card"):
        return cards.TXT_ASK_CARD
    return cards.TXT_NEED_BOTH
