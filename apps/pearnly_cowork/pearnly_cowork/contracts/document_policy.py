"""哪些单据录不录（用户 2026-10-07：「规则不应该写死，无法判断有争议的能让用户做选择」）。纯函数，不连库。

- 口径类（各家记账习惯不同）：转账凭条、预扣税凭证 50 ทวิ、银行还款收据。识别只标类别；这个租户选一次
  「录入 / 不录入」，之后同类自动套用。选「不录入」的，识别存档时直接按非发票处理；没选过的推送前停下来等人选。
- 疑点类（单张拿不准）：盖作废章、水电「通知 + 收据」同页里的通知联、报关单页。默认不推送，会计看过可放行这一张。

类别来自识别读数：转账凭条是票种 ``payment_evidence``，其余是 ``document_flag``。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

POLICY_CLASSES = ("payment_evidence", "withholding_certificate", "loan_repayment")
SUSPECT_FLAGS = ("cancelled", "billing_notice", "customs_declaration")
FLAGS = ("withholding_certificate", "loan_repayment") + SUSPECT_FLAGS
ENTER, SKIP = "enter", "skip"
VALUES = (ENTER, SKIP)
NEEDED_PREFIX = "document_policy_needed_"
SKIPPED_PREFIX = "document_policy_skip_"
SUSPECT_PREFIX = "document_suspect_"
WARNING_PREFIX = "document_policy:"


def document_class(fields: Mapping[str, Any]) -> str:
    """口径类类别；不是口径类回空。"""
    fields = fields if isinstance(fields, Mapping) else {}
    flag = str(fields.get("document_flag") or "")
    if flag in POLICY_CLASSES:
        return flag
    return "payment_evidence" if fields.get("document_type") == "payment_evidence" else ""


def suspect_flag(fields: Mapping[str, Any]) -> str:
    flag = str((fields if isinstance(fields, Mapping) else {}).get("document_flag") or "")
    return flag if flag in SUSPECT_FLAGS else ""


def normalize(raw: Any) -> Dict[str, str]:
    """存着的口径 → 只留认得的类别与取值。"""
    return {key: value for key, value in (raw if isinstance(raw, Mapping) else {}).items()
            if key in POLICY_CLASSES and value in VALUES}


def push_block(fields: Mapping[str, Any], policy: Mapping[str, str], *, release_suspect: bool = False) -> Optional[str]:
    """推送前的门槛 → 原因码（复核页按码显示文案与按钮），能推回 ``None``。"""
    kind = document_class(fields)
    if kind and policy.get(kind) == SKIP:
        return SKIPPED_PREFIX + kind
    if kind and policy.get(kind) != ENTER:
        return NEEDED_PREFIX + kind
    flag = suspect_flag(fields)
    if flag and not release_suspect:
        return SUSPECT_PREFIX + flag
    return None


def apply_to_pages(pages, policy: Mapping[str, str]) -> None:
    """识别存档前（逐页展开的读数，就地改）：口径类按这个租户的选择定这一页算不算票。

    选了「不录入」→ 记成非发票；选了「录入」或还没选 → 算票（还没选的推送前会停下来问）。模型自己判成
    非发票的口径类页（例如原文标题认出的预扣税凭证）也按这个选择来，同一类单据不因读法不同而结果不同。"""
    for page in pages:
        fields = page.get("fields") if isinstance(page, dict) else None
        if not isinstance(fields, dict):
            continue
        kind = document_class(fields)
        if not kind:
            continue
        skip = policy.get(kind) == SKIP
        if bool(fields.get("is_not_invoice")) != skip:
            fields["is_not_invoice"] = skip
            page.setdefault("_validation_warnings", []).append(f"{WARNING_PREFIX} {policy.get(kind) or 'ask'} {kind}")


__all__ = ["ENTER", "FLAGS", "NEEDED_PREFIX", "POLICY_CLASSES", "SKIP", "SKIPPED_PREFIX", "SUSPECT_FLAGS",
           "SUSPECT_PREFIX", "VALUES", "apply_to_pages", "document_class", "normalize", "push_block", "suspect_flag"]
