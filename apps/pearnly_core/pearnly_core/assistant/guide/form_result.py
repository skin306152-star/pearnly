"""表单引导（``source: form``）的结果步骤（多产品共用：Cowork、ERP 的表单引导都点名它，总表 C 堆，主会话 11-25 批）。纯整理，不碰库。

结果卡只照确定那一下的回包（``ctx["done"]``）画，不另查、不编造：回包没有的字段不显示。回包约定（各产品 ``confirm`` 照这个回）：

- ``status``：``entered``（办成，页面状态词「成功」）/ ``held``（没办，「已跳过」）/ ``failed``（「失败」）；
- ``label``：办的那一件给人看的名字（单号 · 对方、任务名、卡片标题）；没有时退回 ``docnum``（产品负责不放内部编号）；
- ``files``：``[{file, name}]`` 放进对话的文件；
- ``deleted``：删了几件（整数或被删的编号列表）；``skipped``：跳过几件，``skip_reason`` / ``skip_reason_text``：为什么跳过；
- ``reason`` / ``reason_text``：没办成的原因码 / 原因句（码按共用原因表 `guide.reasons` 出字，不在这里另写映射；不露 Error Log 编号）；
- ``state_text``：办成之后的事实状态（例：添加公司后「同步中」），显示在状态词下面；
- ``next``、``marks``、``questions``、``wait``、``total``：原样带过去（交给别的引导、月结标记、追问、还在算、合计）；
  回包里其余字段也原样留在结果里（页面不画），产品自己的测试与后续追问照读。

「单据」「待处理」两份现有的 result（`pearnly_cowork.assistant.guided.pending.result`、`pearnly_erp_portal.express.guide_pending.result`）
回的是同一形状，C3 并进本函数（记录 docs/work/2026-11-25-cc-form-result.md）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

import frappe

from pearnly_core.assistant.guide import reasons

ENTERED, HELD, FAILED = "entered", "held", "failed"
CARRY = ("files", "next", "marks", "questions", "wait", "total")
# 站点内部编号（Cowork 识别单 CWH-…、ERP 的 P: / S: / I:、哈希）不上卡（同前端 `isInternalId`，主会话 11-25）
INTERNAL = re.compile(r"^(CWH-|[PSI]:)|^[0-9a-f]{24,}$", re.IGNORECASE)


def _count(value) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _why(done: Mapping[str, Any], text_key: str, code_key: str) -> str:
    return str(done.get(text_key) or "").strip() or reasons.text(str(done.get(code_key) or ""))


def result(ctx: Mapping[str, Any], draft: str = "") -> Optional[Dict[str, Any]]:
    """确定回包 → 结果卡（表单一行：状态词 · 名字 / 原因或事实状态 / 文件）。回包不是字典（没有确定过）回 None。"""
    done = ctx.get("done")
    if not isinstance(done, Mapping):
        return None
    status = str(done.get("status") or ENTERED)
    out: Dict[str, Any] = dict(done)   # 产品回包原样带上（页面只画认得的字段），下面只定状态、名字、原因
    for key in CARRY:
        if out.get(key) in (None, "", []):
            out.pop(key, None)
    label = str(done.get("label") or done.get("docnum") or "").strip()
    out.pop("docnum", None)
    if label and not INTERNAL.search(label):
        out["docnum"] = label
    lines = []
    if "deleted" in done:
        deleted, skipped = _count(done.get("deleted")), _count(done.get("skipped"))
        skip_why = _why(done, "skip_reason_text", "skip_reason")
        if not deleted:
            status = HELD
            lines.append(skip_why or _why(done, "reason_text", "reason"))
        else:
            lines.append(frappe._("intake.delete.done").format(deleted))
            if skipped:
                lines.append(skip_why)
    elif status in (FAILED, HELD):
        lines.append(_why(done, "reason_text", "reason") or (reasons.text(reasons.FAILED) if status == FAILED else ""))
    else:
        lines.append(str(done.get("reason_text") or "").strip())
    if status == ENTERED:
        lines.append(str(done.get("state_text") or "").strip())
    text = " · ".join(line for line in lines if line)
    if text:
        out["reason_text"] = text
    out["status"] = status
    return out


__all__ = ["CARRY", "ENTERED", "FAILED", "HELD", "result"]
