"""Pure receipt presentation and scoped deduplication, never an authorization layer.

All rows must be loaded and authorized by a native service. No log/DB RPC exists
yet. A parsed receipt or ERP number is not independent external readback.
"""

from copy import deepcopy
from datetime import datetime
import json
from typing import Any, Dict, Optional

_EMPTY: Dict[str, str] = {
    "external_doc_no": "",
    "external_doc_id": "",
    "external_url": "",
    "external_doc_hint": "",
}

def _result(
    doc_no: str = "",
    doc_id: str = "",
    url: str = "",
    hint: str = "",
) -> Dict[str, str]:
    return {
        "external_doc_no": (doc_no or "").strip(),
        "external_doc_id": (doc_id or "").strip(),
        "external_url": (url or "").strip(),
        "external_doc_hint": (hint or "").strip(),
    }

def request_payload(request_body: Any) -> Dict[str, Any]:
    """从 erp_push_logs.request_body 取我们发给小助手的那份载荷。

    落库是 jsonb,取回来可能已是 dict 也可能是串;老记录还有一批把字段摊在顶层、没有
    payload 这一层。四个派生器都要走这两步,各写一遍就是四份对「载荷长什么样」的假设 ——
    改了封装形状只会静默降级(卡片空一格、导出少一列),没有一处会报错。
    """
    body = _coerce_body(request_body)
    payload = body.get("payload")
    return payload if isinstance(payload, dict) else body

def _coerce_body(response_body: Any) -> Dict[str, Any]:
    """把 response_body 统一转成 dict。

    response_body 可能是:已是 dict / JSON 字符串 / 非 JSON 文本 / None。
    任何无法解析成 dict 的情况都返回 {} —— 上层派生器据此返回全空,不崩。
    """
    if response_body is None:
        return {}
    if isinstance(response_body, dict):
        return response_body
    if isinstance(response_body, (bytes, bytearray)):
        try:
            response_body = response_body.decode("utf-8", "replace")
        except Exception:
            return {}
    if isinstance(response_body, str):
        s = response_body.strip()
        if not s or s[0] not in "{[":
            return {}
        try:
            parsed = json.loads(s)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}

def _derive_mrerp(body: Dict[str, Any], status: str) -> Dict[str, str]:
    """MR.ERP · 从 response_body.mrerp_bill_no 映射 external_doc_no。

    - external_doc_no = mrerp_bill_no
    - external_doc_id = 同 external_doc_no(MR.ERP 没有独立内部 ID)
    - external_url    = "" (MR.ERP 没有可直达的单据深链)
    - external_doc_hint = "mrerp_search" (前端提示去 销售赊账/ขายเชื่อ-ในประเทศ → List All 搜)
    """
    bill_no = body.get("mrerp_bill_no") or ""
    bill_no = str(bill_no).strip()
    if not bill_no:
        # 成功但 MR.ERP 没回单号 —— 返回全空,前端据 status=success 显示"未返回单号"。
        return _EMPTY.copy()
    return _result(
        doc_no=bill_no,
        doc_id=bill_no,
        url="",
        hint="mrerp_search",
    )

def _derive_default(body: Dict[str, Any], status: str) -> Dict[str, str]:
    """通用派生器 · 认 response_body 里常见的单号/链接键。

    覆盖未注册的 adapter(含未来通用 webhook 对接的各种 ERP),让它们
    只要在响应里回标准键名就能零改动显示单号。
    """
    doc_no = (
        body.get("external_doc_no")
        or body.get("doc_no")
        or body.get("document_number")
        or body.get("number")
        or ""
    )
    doc_id = body.get("external_doc_id") or body.get("doc_id") or body.get("id") or ""
    url = body.get("external_url") or body.get("url") or ""
    return _result(doc_no=str(doc_no), doc_id=str(doc_id), url=str(url))

def _derive_express(body: Dict[str, Any], status: str) -> Dict[str, str]:
    """Express · response_body.express_docnum → external_doc_no。

    小助手 DBF 写完回读 Express 生成的单号(如 IV681220-001 / RR…),ack 时回 express_docnum。
    通用派生器不认这个键 → 单号不显示("未生成 ERP 单号"),故 Express 专属映射。
    - external_doc_no = express_docnum(给用户看 + 复制)
    - external_url    = "" (Express 桌面软件无深链)
    - external_doc_hint = "express_search"(前端提示去对应进/销项报表搜该单号)
    """
    docnum = str(body.get("express_docnum") or "").strip()
    if not docnum:
        return _EMPTY.copy()
    return _result(doc_no=docnum, doc_id=docnum, url="", hint="express_search")

def derive_external_ref(
    adapter: Optional[str],
    response_body: Any,
    status: Optional[str] = None,
) -> Dict[str, str]:
    """日志 API 层调用入口。

    返回固定 4 键 dict(见模块 docstring)。永不抛异常。

    Args:
        adapter: erp_endpoints.adapter (mrerp/mrerp_dms/webhook/...);端点已删时可能为 None。
        response_body: erp_push_logs.response_body(JSON 字符串 / dict / 文本 / None)。
        status: erp_push_logs.status('success'/'failed'/...);仅用于派生器内部判断,
                不改变状态机,也不参与"是否显示缺失提示"(那是前端职责)。
    """
    try:
        body = _coerce_body(response_body)
        key = (adapter or "").strip().lower()
        deriver = _DERIVERS.get(key, _derive_default)
        result = deriver(body, status or "")
        # 防御:派生器万一返回了缺键的 dict,补齐成固定形状。
        merged = _EMPTY.copy()
        if isinstance(result, dict):
            for k in merged:
                v = result.get(k)
                if v:
                    merged[k] = str(v)
        return merged
    except Exception:
        return _EMPTY.copy()

_DERIVERS = {"mrerp": _derive_mrerp, "express": _derive_express}

coerce_body = _coerce_body


class ReceiptError(ValueError):
    pass


def _rows(rows):
    if rows is None:
        # Unlike the old catch-all SQL fallback, a failed lookup is not proof
        # that the original invoice has never been posted.
        raise ReceiptError("push.receipt_lookup_unavailable")
    return list(rows)


def _order(row):
    created = row.get("created_at")
    if not isinstance(created, datetime) or created.tzinfo is None or created.utcoffset() is None:
        raise ReceiptError("push.receipt_timestamp_invalid")
    return created, str(row["id"])


def _newest(rows):
    return sorted(_rows(rows), key=_order, reverse=True)


def reservation_account(row):
    body = coerce_body(row.get("request_body"))
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    intent = body.get("target_intent") if isinstance(body.get("target_intent"), dict) else {}
    value = next((value for value in (body.get("account_set"), meta.get("account_set"),
                                      intent.get("account_set")) if value is not None), "")
    return str(value).strip(" ").lower()


def reservation_decision(rows, *, tenant_id, endpoint_id, history_id, account_set):
    """Source priority: successful receipt, active reservation, unknown, then new.

    The DB reader MUST run under an endpoint/binding lock. This pure choice
    provides neither concurrency protection nor authorization by itself.
    """
    if not all(str(v or "").strip() for v in (tenant_id, endpoint_id, history_id, account_set)):
        raise ReceiptError("push.reservation_scope_required")
    rows = [r for r in _newest(rows) if r.get("tenant_id") == tenant_id
            and r.get("endpoint_id") == endpoint_id and r.get("history_id") == history_id
            and reservation_account(r) == account_set.casefold()]
    for row in rows:
        if row.get("status") in {"success", "skipped_dup"}:
            return {"dispatch": False, "reason": "already_success", "row": deepcopy(row)}
    for row in rows:
        if (row.get("status") in {"pending", "retrying"} or row.get("lease_owner") is not None
                or (row.get("status") == "failed" and row.get("next_retry_at") is not None)):
            return {"dispatch": False, "reason": "active", "row": deepcopy(row)}
    for row in rows:
        if row.get("status") == "manual" and row.get("error_msg") == "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN":
            return {"dispatch": False, "reason": "unknown", "row": deepcopy(row)}
    return {"dispatch": True, "reason": "new", "row": None}


def legacy_success(rows, *, history_id, endpoint_id, user_id, invoice_no=None,
                   seller_name=None, account_set=None):
    """Old legacy dedup: history first, then invoice+seller, exact account string.

    This deliberately differs from the catalog-reservation account casefold.
    The scoped native adapter must supply authorized rows, including any old
    user-to-Site mapping. Missing account is not a safe native admission default.
    """
    if not endpoint_id:
        return None
    eligible = [r for r in _newest(rows) if r.get("endpoint_id") == endpoint_id
                and r.get("user_id") == str(user_id) and r.get("status") == "success"
                and (not account_set or coerce_body(r.get("request_body")).get("account_set", "") == str(account_set))]
    if history_id:
        for row in eligible:
            if row.get("history_id") == history_id:
                return deepcopy(row)
    if invoice_no:
        for row in eligible:
            if row.get("invoice_no") == invoice_no and (row.get("seller_name") or "") == (seller_name or ""):
                return deepcopy(row)
    return None


def current_receipts(rows, *, status_filter=None):
    """Keep latest per history/endpoint BEFORE display filtering, as old SQL does.

    Orphan rows do not collapse together. This UI projection is NEVER a source
    for dedup: an older success remains authoritative even after a new failure.
    """
    result, seen = [], set()
    for row in _newest(rows):
        key = (("pair", row["history_id"], row["endpoint_id"])
               if row.get("history_id") is not None and row.get("endpoint_id") is not None
               else ("solo", row["id"]))
        if key in seen:
            continue
        seen.add(key)
        if status_filter == "success" and row.get("status") != "success":
            continue
        if status_filter == "retrying" and not (row.get("status") == "failed" and row.get("next_retry_at") is not None):
            continue
        if status_filter == "failed" and not (row.get("status") == "failed" and row.get("next_retry_at") is None):
            continue
        result.append(deepcopy(row))
    return result


def prior_document(rows, *, tenant_id, endpoint_id, history_id, account_set):
    """Read an original's last successful number from authoritative scoped rows.

    Rows have history_tenant_id from their History join, not nullable log tenant.
    Native callers must resolve history_id from immutable Source/Batch lineage
    and reauthorize that original. Editable fields.history_id is not accepted.
    Unlike the old query, match endpoint AND account to avoid cross-book numbers.
    None rows means lookup failed and raises; [] means a successful empty query.
    """
    if not all(str(v or "").strip() for v in (tenant_id, endpoint_id, history_id, account_set)):
        raise ReceiptError("push.reservation_scope_required")
    for row in _newest(rows):
        if (row.get("history_tenant_id") != tenant_id or row.get("endpoint_id") != endpoint_id
                or row.get("history_id") != history_id or row.get("status") != "success"
                or reservation_account(row) != account_set.casefold()):
            continue
        body = coerce_body(row.get("response_body"))
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        return str(body.get("express_docnum") or meta.get("docnum") or "").strip() or None
    return None
