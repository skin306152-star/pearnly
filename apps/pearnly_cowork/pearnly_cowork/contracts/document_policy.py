"""哪些单据录不录由这个租户自己选（用户 2026-10-07：「规则不应该写死，无法判断有争议的能让用户做选择」）。纯函数，不连库。

口径类（各家记账习惯不同）：转账凭条、预扣税凭证 50 ทวิ。识别只认类别；这个租户选一次「录入 / 不录入」，之后同类
自动套用。选「不录入」的，识别存档时直接按非发票处理；没选过的推送前停下来等人选。

类别来自识别读数，不为它多调模型或加长提示词（用户 2026-10-07 选零成本）：转账凭条是票种 ``payment_evidence``；
预扣税凭证是原文标题认出的 ``document_flag``（`ocr.engine.runner.mark_title`）。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

POLICY_CLASSES = ("payment_evidence", "withholding_certificate")
ENTER, SKIP = "enter", "skip"
VALUES = (ENTER, SKIP)
NEEDED_PREFIX = "document_policy_needed_"
SKIPPED_PREFIX = "document_policy_skip_"
WARNING_PREFIX = "document_policy:"


def document_class(fields: Mapping[str, Any]) -> str:
    """口径类类别；不是口径类回空。"""
    fields = fields if isinstance(fields, Mapping) else {}
    flag = str(fields.get("document_flag") or "")
    if flag in POLICY_CLASSES:
        return flag
    return "payment_evidence" if fields.get("document_type") == "payment_evidence" else ""


def normalize(raw: Any) -> Dict[str, str]:
    """存着的口径 → 只留认得的类别与取值。"""
    return {key: value for key, value in (raw if isinstance(raw, Mapping) else {}).items()
            if key in POLICY_CLASSES and value in VALUES}


def push_block(fields: Mapping[str, Any], policy: Mapping[str, str]) -> Optional[str]:
    """推送前的门槛 → 原因码（复核页按码显示文案与按钮），能推回 ``None``。"""
    kind = document_class(fields)
    if kind and policy.get(kind) == SKIP:
        return SKIPPED_PREFIX + kind
    if kind and policy.get(kind) != ENTER:
        return NEEDED_PREFIX + kind
    return None


def apply_to_pages(pages, policy: Mapping[str, str]) -> None:
    """识别存档前（逐页展开的读数，就地改）：口径类按这个租户的选择定这一页算不算票。

    选了「不录入」→ 记成非发票；选了「录入」或还没选 → 算票（还没选的推送前会停下来问）。原文标题认出的预扣税凭证
    在引擎里已判成非发票，这里同样按选择来，同一类单据不因读法不同而结果不同。"""
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


def skipped_certificates(pages, policy: Mapping[str, str]) -> list:
    """这个租户选了「不录入」的预扣税凭证页：[(在读数里的序号, 页)]。只存一条参考记录给报税草稿读
    （`history.certificates`，用户 2026-10-13），不成票、不进录入待办、不推送、不扣费。"""
    if policy.get("withholding_certificate") != SKIP:
        return []
    found = []
    for position, page in enumerate(pages or []):
        fields = page.get("fields") if isinstance(page, dict) else None
        if isinstance(fields, dict) and document_class(fields) == "withholding_certificate":
            found.append((position, page))
    return found


__all__ = ["ENTER", "NEEDED_PREFIX", "POLICY_CLASSES", "SKIP", "SKIPPED_PREFIX", "VALUES", "apply_to_pages",
           "document_class", "normalize", "push_block", "skipped_certificates"]
