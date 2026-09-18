# -*- coding: utf-8 -*-
"""DMS 公司银行主档的读取，以及订车单"转账目的地"的提交前校验。

为什么要专门校验：**订车单上的转账目的地是我们 POST 上去的**。如果主档里那个账户
已经被删/改了，拿会话里的旧值提交，钱就指向一个已不存在的账户 —— 这种事不能靠运气。

照搬老产品 `services/erp/mrerp_dms_company_banks.py`。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from pearnly_dms.dms_client_base import DmsClientError


def company_bank_label(row: list) -> str:
    """公司银行行 `[id, code, name, branch, account]` 的稳定展示值。"""
    code = str(row[1]).strip() if len(row) > 1 else ""
    name = str(row[2]).strip() if len(row) > 2 else ""
    branch = str(row[3]).strip() if len(row) > 3 else ""
    account = str(row[4]).strip() if len(row) > 4 else ""
    bank = (
        f"{code} · {name}" if code and name and code.casefold() != name.casefold() else name or code
    )
    return " · ".join(value for value in (bank, account, branch) if value) or str(row[0])


def normalize_company_bank_rows(rows: Iterable[Any]) -> List[list]:
    """归一 DMS typeahead 行；兼容旧的 dict 形态结果，并**丢弃没有 DMS id 的脏行**
    （没有 id 就没法指向账户，留着只会让它出现在下拉里）。"""
    out = []
    for item in rows:
        if isinstance(item, dict):
            bank_id = str(item.get("id") or "").strip()
            details = [str(value or "").strip() for value in item.get("details") or []]
        else:
            values = list(item or [])
            bank_id = str(values[0] if values else "").strip()
            details = [str(value or "").strip() for value in values[1:]]
        if not bank_id:
            continue
        out.append([bank_id, *(details + ["", "", "", ""])[:4]])
    return out


def fetch_company_banks(adapter: Any, *, timeout_ms: int = 10000) -> List[list]:
    """从订车单实际使用的银行 typeahead 读完整公司账户；**失败重试一次**再报错。

    重试的理由：这个下拉在主档刚变更或会话刚建立时可能还没就绪，一次抖动就报错会让
    操作员以为"数据错了"。重试仍失败就抛 `ERR_DMS_MASTER_UNAVAILABLE`，并附截图路径
    —— 这类失败光看日志分不清"DMS 慢"还是"选择器变了"。
    """
    failure = None
    for _ in range(2):
        try:
            rows = adapter.client()._bshsd_all("txtbanknametfmon", page_size=200)
            if rows is not None:
                return normalize_company_bank_rows(rows)
            failure = RuntimeError("DMS bank typeahead returned no result")
        except Exception as exc:
            failure = exc
    screenshot = _failure_screenshot(adapter)
    raise DmsClientError(
        "company bank master did not become ready"
        f"; screenshot={screenshot}; cause={type(failure).__name__}",
        "ERR_DMS_MASTER_UNAVAILABLE",
    )


def company_bank_payment_extra(row: list) -> Dict[str, str]:
    """公司银行主档行 → 订车单转账目的地字段。"""
    return {
        "dst_id": str(row[0]),
        "dst": company_bank_label(row),
        "dst_bank_id": str(row[0]),
        "dst_bank_name": str(row[2]).strip() if len(row) > 2 else "",
        "dst_branch_name": str(row[3]).strip() if len(row) > 3 else "",
        "dst_account_no": str(row[4]).strip() if len(row) > 4 else "",
    }


def validate_company_bank_payments(adapter: Any, payments: Iterable[dict]) -> List[dict]:
    """提交前确认每笔转账仍指向**现存**的公司银行，并用主档当前名称覆盖会话旧值。

    没有转账就原样返回（不为了"顺便校验"去多打一次接口）。
    """
    payments = list(payments or [])
    if not any(payment.get("channel") == "transfer" for payment in payments):
        return [dict(payment) for payment in payments]
    rows = fetch_company_banks(adapter)
    by_id = {str(row[0]): row for row in rows}
    validated = []
    for payment in payments:
        current = dict(payment)
        if current.get("channel") == "transfer":
            extra = dict(current.get("extra") or {})
            row = by_id.get(str(extra.get("dst_id") or ""))
            if row is None:
                # 已选公司银行不在当前 live 主档：主档已变更，**不许拿会话旧值提交**。
                raise DmsClientError(
                    "selected company bank is no longer available",
                    "ERR_DMS_MASTER_UNMATCHED",
                )
            extra.update(company_bank_payment_extra(row))
            current["extra"] = extra
        validated.append(current)
    return validated


def _failure_screenshot(adapter: Any) -> str:
    """失败现场留一张图 —— 排障时"当时长什么样"比什么都值钱。"""
    session = getattr(adapter, "_session", None)
    path = (
        session.screenshot("company-bank-master-failed", scenario="company bank list not ready")
        if session is not None
        else None
    )
    if path:
        return str(path)
    folder = Path("/tmp/pearnly-dms-failures")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fallback = folder / f"company-bank-{time.strftime('%Y%m%d-%H%M%S')}.png"
        adapter.page.screenshot(path=str(fallback), full_page=True)
        return str(fallback)
    except Exception:
        return "unavailable"
