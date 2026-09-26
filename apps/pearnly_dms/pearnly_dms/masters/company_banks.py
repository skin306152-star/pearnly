# -*- coding: utf-8 -*-
"""五类付款银行目录的读取与**提交前身份校验**。

为什么这件事必须做对：**订车单上的银行是我们 POST 上去的**。主档里那个账户已经被
删/改名了，还拿会话里的旧值提交，钱就指向一个不存在的账户 —— 这种事不能靠运气。

五类目录（各自独立的原生 typeahead，**ID 与范围都可能不同**，不能互相顶替）：

| 目录 | 用在哪 |
|---|---|
| `company_banks`（`txtbanknametfmon`） | 转账**到账**方 —— 公司收款账户，**永远按实时目录 id 校验** |
| `source_banks`（`txtbanknametffrom`） | 转账**来源**银行 |
| `cheque_banks` / `cashier_banks` / `card_banks` | 支票 / 现金支票 / 刷卡的银行 |

后四类在「目录权威为空」时允许这一笔用手工填的银行名称（挂 `MANUAL_BANK_FLAG`，
只豁免 id 槽位，名称与其它原生字段照旧必填）；目录非空则必须命中，
**已删除的旧选项必被 `ERR_DMS_MASTER_UNMATCHED` 拦下**。收款账户不在此列 ——
收款账户**永不手工**。

照搬老产品 `services/erp/mrerp_dms_company_banks.py`（线上基准 `501589f0`），
只改调用名（`adapter.client()` / `adapter.page`）与类名约定。
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.payments import MANUAL_BANK_FLAG

# 五类付款银行目录：键 → DMS 原生 select/elem 名。
PAYMENT_BANK_MASTERS = {
    "company_banks": "txtbanknametfmon",
    "source_banks": "txtbanknametffrom",
    "cheque_banks": "txtbanknamecheque",
    "cashier_banks": "txtbanknamecashiercq",
    "card_banks": "txtbanknamecddbc",
}

# 目录权威为空时**允许手工填名称**的那几类（收款账户 company_banks 不在其中）。
MANUAL_BANK_KEYS = ("source_banks", "cheque_banks", "cashier_banks", "card_banks")

# 支付渠道 → 它用的银行目录。
PAYMENT_CHANNEL_BANKS = {
    "cheque": "cheque_banks",
    "cashier_cheque": "cashier_banks",
    "card": "card_banks",
}


def bank_row_name(row: list) -> str:
    """银行目录行的显示名；缺名回落代码。"""
    if len(row) > 2 and str(row[2] or "").strip():
        return str(row[2]).strip()
    return str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""


def manual_bank_allowed_for_rows(key: str, rows: Any) -> bool:
    """这一类目录**权威为空**（拿到空列表，不是取数失败）→ 允许手工填名称。"""
    return key in MANUAL_BANK_KEYS and isinstance(rows, list) and not rows


def resolve_bank_identity(
    rows: Any, key: str, bank_id: Any = "", bank_name: Any = ""
) -> Dict[str, Any] | None:
    """把这一笔选的银行解到实时目录上。`rows is None`（取数失败）→ None。

    · 目录非空：给了 id 就按 id 命中；没给 id 就按名称**唯一**命中（多命中不放行）。
    · 目录权威为空：允许手工条目（id 留空，**不伪造目录 id**）。
    """
    if rows is None:
        return None
    empty_directory = manual_bank_allowed_for_rows(key, rows)
    rows = [row for row in rows if row and row[0] is not None]
    wanted_id = str(bank_id or "").strip()
    wanted_name = str(bank_name or "").strip()
    if rows:
        if wanted_id:
            row = next((item for item in rows if str(item[0]).strip() == wanted_id), None)
        else:
            hits = (
                [item for item in rows if bank_row_name(item).casefold() == wanted_name.casefold()]
                if wanted_name
                else []
            )
            row = hits[0] if len(hits) == 1 else None
        if row is None:
            return None
        return {"id": str(row[0]).strip(), "name": bank_row_name(row), "manual": False}
    if not empty_directory or not wanted_name:
        return None
    return {"id": "", "name": wanted_name, "manual": True}


def apply_bank_identity(extra: dict, key: str, rows: Any, *, id_key: str, name_key: str) -> None:
    """按实时目录落这笔付款的银行身份；命中不了（含已删除的旧选项）→ `ERR_DMS_MASTER_UNMATCHED`。

    目录权威为空 → 保留用户手工填的名称、清掉 id，并挂 `MANUAL_BANK_FLAG`
    （提交校验据此只豁免 id 槽位；名称缺失由完整性校验报缺字段，
    而不是在这里被当成"选项不在目录里"）。
    """
    if manual_bank_allowed_for_rows(key, rows):
        extra[id_key] = ""
        extra[MANUAL_BANK_FLAG] = "1"
        return
    identity = resolve_bank_identity(rows, key, extra.get(id_key), extra.get(name_key))
    if identity is None:
        raise DmsClientError(
            "selected bank is not in the current DMS list", "ERR_DMS_MASTER_UNMATCHED"
        )
    assign_bank_identity(extra, identity, id_key=id_key, name_key=name_key)


def assign_bank_identity(extra: dict, identity: dict, *, id_key: str, name_key: str) -> None:
    """把解出来的身份落进 extra：命中目录写回目录 id/名称并**清掉手工标记**；
    手工条目保留用户名称、id 留空（不伪造目录 id）。"""
    extra[id_key] = identity["id"]
    extra[name_key] = identity["name"]
    if identity["manual"]:
        extra[MANUAL_BANK_FLAG] = "1"
    else:
        extra.pop(MANUAL_BANK_FLAG, None)


def company_bank_label(row: list) -> str:
    """银行行 `[id, code, name, branch, account]` 的展示值；后两列可能为空。"""
    code = str(row[1]).strip() if len(row) > 1 else ""
    name = str(row[2]).strip() if len(row) > 2 else ""
    branch = _bank_detail(row[3] if len(row) > 3 else "")
    account = _bank_detail(row[4] if len(row) > 4 else "")
    bank = (
        f"{code} · {name}" if code and name and code.casefold() != name.casefold() else name or code
    )
    return " · ".join(value for value in (bank, account, branch) if value) or str(row[0])


def _bank_detail(value: Any) -> str:
    """DMS 用 `00` 当空账号/空分行的占位 —— **绝不**把它当真实数据展示或提交。"""
    text = str(value or "").strip()
    return "" if text == "00" else text


def _bank_code_sort_key(row: list, original_index: int) -> tuple:
    code = str(row[1] or "").strip() if len(row) > 1 else ""
    row_id = str(row[0] or "").strip() if row else ""
    if code.isdecimal():
        return (0, int(code), code.casefold(), row_id)
    # 有些租户用助记码（SCB/KBANK）。DMS 没给这些定义数字序，
    # 所以**保留它自己的返回顺序**，不自作主张排序。
    return (1, original_index, "", row_id)


def sort_bank_rows(rows: Iterable[list]) -> List[list]:
    """按银行代码升序 —— 与原生界面一致（数字码在前，助记码保持原序）。"""
    materialized = [list(row) for row in rows or []]
    indexed = enumerate(materialized)
    return [
        row
        for index, row in sorted(indexed, key=lambda pair: _bank_code_sort_key(pair[1], pair[0]))
    ]


def normalize_company_bank_rows(rows: Iterable[Any]) -> List[list]:
    """归一 DMS typeahead 行；兼容旧 DOM 字典结果，**丢弃没有 DMS id 的脏行**
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
    return sort_bank_rows(out)


def fetch_company_banks(
    adapter: Any, *, client: Any = None, timeout_ms: int = 10000
) -> List[list]:
    """读收款银行目录；**不能假设银行行含公司收款账户**。"""
    return _fetch_banks(adapter, "txtbanknametfmon", client=client)


def fetch_source_banks(adapter: Any, *, client: Any = None, timeout_ms: int = 10000) -> List[list]:
    """汇款银行用**独立**的原生目录，它的 ID 和范围可能不同于收款银行。"""
    return _fetch_banks(adapter, "txtbanknametffrom", client=client)


def fetch_payment_bank_masters(adapter: Any, *, client: Any = None) -> dict:
    """五类付款银行目录一次取齐。给了权威只读 client 就走它 —— 销售账号常看不到银行
    全表，少这一次分叉就等于少一次按字段的重复登录。"""
    return {
        key: _fetch_banks(adapter, elem, client=client)
        for key, elem in PAYMENT_BANK_MASTERS.items()
    }


def _fetch_banks(adapter: Any, elemname: str, *, client: Any = None) -> List[list]:
    """读一类银行目录；**失败重试一次**再报错。

    重试的理由：这个下拉在主档刚变更或会话刚建立时可能还没就绪，一次抖动就报错会让
    操作员以为"数据错了"。重试仍失败就抛 `ERR_DMS_MASTER_UNAVAILABLE`，并附截图路径
    —— 这类失败光看日志分不清"DMS 慢"还是"选择器变了"。
    """
    failure = None
    for _ in range(2):
        try:
            reader = client or adapter.client()
            rows = reader._bshsd_all(elemname, page_size=200)
            if rows is not None:
                if elemname == 'txtbanknametfmon':
                    from pearnly_dms.client.master_status import active_company_banks
                    rows = active_company_banks(reader, rows)
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


def company_bank_payment_extra(row: list, existing: dict | None = None) -> Dict[str, str]:
    """公司银行主档行 → 订车单转账目的地字段。

    目录里缺账号/分行（DMS 用 `00` 占位）时，**保留已经明确填过的收款资料**，
    不要把它清成空 —— 那等于把用户填好的账号抹掉。
    """
    existing = existing or {}
    return {
        "dst_id": str(row[0]),
        "dst": company_bank_label(row),
        "dst_bank_id": str(row[0]),
        "dst_bank_name": str(row[2]).strip() if len(row) > 2 else "",
        "dst_branch_name": _bank_detail(row[3] if len(row) > 3 else "")
        or str(existing.get("dst_branch_name") or "").strip(),
        "dst_account_no": _bank_detail(row[4] if len(row) > 4 else "")
        or str(existing.get("dst_account_no") or "").strip(),
    }


def validate_company_bank_payments(
    adapter: Any, payments: Iterable[dict], *, client: Any = None,
    catalogues: Optional[dict] = None,
) -> List[dict]:
    """提交前核对银行身份与完整转账资料；**不把银行编号当作公司账号**。

    `client` 为权威只读会话上的 DmsClient 时，银行目录也读权威视图。
    收款账户（`company_banks`）**永远**按实时目录 id 校验；其余四类目录权威为空时
    允许这一笔用手工银行名称（目录非空则仍要求命中，已删除的旧选项必被
    `ERR_DMS_MASTER_UNMATCHED` 拦下）。最后统一过一遍完整的收款资料校验。
    """
    from pearnly_dms.contracts.payments import validate_payment_completeness

    payments = list(payments or [])
    by_key = {}

    def bank_rows(key):
        if key not in by_key:
            if catalogues is not None:
                # 本地完整快照里的同一份目录（收集时就是 `_fetch_banks` 读的、已规范化）。
                rows = catalogues.get(key)
                if not isinstance(rows, list):
                    raise DmsClientError(f"bank catalogue {key!r} absent from snapshot",
                                         "ERR_DMS_MASTER_UNAVAILABLE")
                by_key[key] = rows
            else:
                by_key[key] = _fetch_banks(adapter, PAYMENT_BANK_MASTERS[key], client=client)
        return by_key[key]

    def current_bank(key, bank_id):
        row = next(
            (
                item
                for item in bank_rows(key)
                if str(item[0]).strip() == str(bank_id or "").strip()
            ),
            None,
        )
        if row is None:
            raise DmsClientError(
                "selected bank is not in the current DMS list", "ERR_DMS_MASTER_UNMATCHED"
            )
        return row

    validated = []
    for payment in payments:
        current = dict(payment)
        channel = current.get("channel")
        extra = dict(current.get("extra") or {})
        if channel == "transfer":
            row = current_bank("company_banks", extra.get("dst_id"))
            extra.update(company_bank_payment_extra(row, extra))
        elif channel in PAYMENT_CHANNEL_BANKS:
            key = PAYMENT_CHANNEL_BANKS[channel]
            apply_bank_identity(extra, key, bank_rows(key), id_key="bank_id", name_key="bank_name")
        current["extra"] = extra
        validated.append(current)
    validate_payment_completeness(validated)
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
    folder = Path(tempfile.gettempdir()) / "pearnly-dms-failures"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"company-bank-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    try:
        adapter.page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return "unavailable"
