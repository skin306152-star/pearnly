# -*- coding: utf-8 -*-
"""审批流的三张卡（纯函数，零副作用）。

照搬老产品 `services/line_dms/approval_cards.py`（线上基准 `501589f0`，布局未改）：

1. **选审批人卡**（销售提审后）：首项固定"发给全部"，下面逐个管理员；
2. **审批申请卡**（推给管理员）：谁提的、哪个客户、逐条 旧 → 新，批 / 拒；
3. **等待卡**（销售侧）：发给谁 + "改派审批人"（指定的人不在也不卡死）。

## 文案在语言包里

键 = `dms.card.approval.*`，一个泰文字符都不留在这里（同 `dms_query_cards`：
这份的 24 条里有 6 条是函数体里的内联字面量，只搬顶层常量会漏）。

⚠️ **申请的一次性不靠卡片**：靠 `dms_approval_store.claim` 的原子状态迁移。
所以 postback 只带申请号（`req`）与审批人（`aid`），**不带 nonce** ——
翻聊天记录点旧卡也不会把已处理的申请再做一遍。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pearnly_dms import i18n
from pearnly_dms.line.cards import (
    ACT_APPROVAL_APPROVE,
    ACT_APPROVAL_REJECT,
    ACT_APPROVAL_RETARGET,
    ACT_APPROVAL_TARGET,
    _btn,
    _bubble,
    _data,
    _kv_row,
    diff_rows,
)

_MAX_APPROVER_BTNS = 6  # LINE 气泡体积约束：6~7 个管理员的场景够用，更多就走"发给全部"


def __getattr__(name: str) -> Any:
    """让外部用 `approval_cards.TXT_XXX` 取词（流程层要那几条回话）。"""
    if name.startswith(("TXT_", "BTN_", "LBL_", "TITLE_")):
        return _t(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _t(key: str, **kw) -> str:
    return i18n.t(f"dms.card.approval.{key}", lang="th", **kw)


def picker_card(request_id: str, approvers: List[Dict[str, str]]) -> Dict[str, Any]:
    """选审批人卡：首项固定"发给全部"，下列逐个管理员（最多 6 个）。"""
    sub = {
        "type": "text",
        "text": _t("TXT_PICK_APPROVER_SUB"),
        "size": "xs",
        "color": "#8a8a8a",
        "wrap": True,
    }
    footer = [_btn(_t("BTN_ALL_APPROVERS"), _data(ACT_APPROVAL_TARGET, req=request_id, aid="all"))]
    for approver in approvers[:_MAX_APPROVER_BTNS]:
        footer.append(
            _btn(
                approver.get("display_name") or _t("LBL_APPROVER"),
                _data(ACT_APPROVAL_TARGET, req=request_id, aid=str(approver["user_id"])),
                "secondary",
            )
        )
    title = _t("TXT_PICK_APPROVER")
    return _bubble(title, [sub], footer, title)


def request_card(
    request_id: str,
    operator_name: str,
    customer_name: str,
    customer_id: str,
    display_diffs: List[Dict[str, str]],
) -> Dict[str, Any]:
    """推给管理员的审批卡：谁提的、哪个客户、逐条 旧→新，批 / 拒两个键。"""
    rows: List[Dict[str, Any]] = [
        _kv_row(_t("LBL_REQUESTER"), operator_name),
        _kv_row(_t("LBL_CUSTOMER"), f"{customer_name or '—'} ({customer_id})"),
        {"type": "separator", "margin": "sm"},
        *diff_rows(display_diffs),
    ]
    footer = [
        _btn(_t("BTN_APPROVE"), _data(ACT_APPROVAL_APPROVE, req=request_id), "primary"),
        _btn(_t("BTN_REJECT"), _data(ACT_APPROVAL_REJECT, req=request_id), "secondary"),
    ]
    title = _t("TITLE_REQUEST")
    return _bubble(title, rows, footer, title)


def waiting_card(request_id: str, target_label: Optional[str]) -> Dict[str, Any]:
    """销售侧等待回执：发给谁 + "改派审批人"（指定的人不在时不卡死）。"""
    rows = [_kv_row(_t("LBL_SENT_TO"), target_label or _t("BTN_ALL_APPROVERS"))]
    footer = [
        _btn(
            _t("BTN_CHANGE_APPROVER"),
            _data(ACT_APPROVAL_RETARGET, req=request_id),
            "secondary",
        )
    ]
    text = _t("TXT_SENT_WAIT")
    return _bubble(text, rows, footer, text)
