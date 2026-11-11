"""核账本：我们写过的单此刻在账本里还在不在（见包说明）。"""
import gzip
import json
from datetime import datetime, timezone

import frappe
from frappe import _

HOOK = "pearnly_ledger_check"
EXPRESS, MRERP = "express", "mrerp"
LEDGERS = (EXPRESS, MRERP)
PRESENT, GONE, UNKNOWN = "present", "gone", "unknown"
STATES = (PRESENT, GONE, UNKNOWN)
ITEMS_MAX = 50  # 一次最多核几张（多了由调用方分批）；同 CC 桌面版 `express_doc_check.ITEMS_MAX`
WAIT_MAX = 60   # 最多等几秒（主会话 11-10）：等不到按「核不了」照样问用户
ITEM_KEYS = ("doc_id", "docnum", "direction", "date", "menu")
TEXT_MAX = 140


def _path(ledger: str) -> str:
    found = frappe.get_hooks(HOOK) or {}
    values = found.get(ledger) if isinstance(found, dict) else None
    values = values if isinstance(values, list) else [values]
    values = [value for value in values if isinstance(value, str) and value]
    return values[-1] if values else ""


def _items(items) -> list:
    """调用方给的单：``[{doc_id, docnum, direction, date?, menu?}]``，去掉多余键；不合规格是程序错，照抛。"""
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= ITEMS_MAX:
        frappe.throw(_("Invalid request"))
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            frappe.throw(_("Invalid request"))
        row = {key: str(item.get(key) or "").strip()[:TEXT_MAX] for key in ITEM_KEYS if item.get(key) is not None}
        if not row.get("doc_id") or not row.get("docnum") or row.get("direction") not in ("purchase", "sales") \
                or row["doc_id"] in seen:
            frappe.throw(_("Invalid request"))
        seen.add(row["doc_id"])
        out.append(row)
    return out


def parse_result(content) -> dict:
    """CC 桌面版 ``express_doc_check`` 回的 gzip JSON ``{"items": [{doc_id, docnum, state}]}`` → ``{doc_id: {state, docnum}}``；
    读不了回空（共用入口整批记 unknown）。两个产品的通道共用。"""
    try:
        data = json.loads(gzip.decompress(content).decode("utf-8"))
    except (OSError, ValueError, TypeError, EOFError):
        return {}
    rows = data.get("items") if isinstance(data, dict) else None
    return {str(row["doc_id"]): {"state": row.get("state"), "docnum": row.get("docnum")}
            for row in rows if isinstance(row, dict) and row.get("doc_id")} if isinstance(rows, list) else {}


def request_params(account_path, account_root, items) -> dict:
    """``express_doc_check`` 的参数（同 CC 桌面版 `express_doc_check._params`）。"""
    return {"ledger": EXPRESS, "account_set": str(account_path or ""), "account_root": str(account_root or ""),
            "items": [{key: item[key] for key in ("doc_id", "docnum", "direction")} for item in items]}


def check_present(connection, account_set_key, items, *, ledger=EXPRESS, wait=WAIT_MAX) -> dict:
    """``items`` 这几张单此刻在 ``connection`` 的 ``account_set_key`` 那份账本里还在不在：
    ``{doc_id: {"state": present | gone | unknown, "docnum", "checked_at": "YYYY-MM-DD HH:MM:SS"（UTC）}}``，每张都有一项。

    - present：账本里还在、没作废 → 写前复述里照实说，用户在对话里答「跳过 / 再录」；
    - gone：删了或作废 → 当没录过照录、不问；
    - unknown：电脑不在线 / CC 桌面版太旧 / 读不到 / 等超时 / 锁拿不到 → 照样问，照实说「暂时核不了」。

    会提交当前事务（建请求要让电脑看得到），等结果时每次重读：调用前自己的写入要已经提交。"""
    if ledger not in LEDGERS:
        frappe.throw(_("Invalid request"))
    rows = _items(items)
    wait = max(0, min(int(wait or 0), WAIT_MAX))
    found = {}
    path = _path(ledger)
    if path:
        try:
            found = frappe.get_attr(path)(connection, account_set_key, rows, ledger=ledger, wait=wait) or {}
        except Exception:
            frappe.log_error(title="Ledger check failed")
            found = {}
    at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    out = {}
    for row in rows:
        got = found.get(row["doc_id"]) if isinstance(found, dict) else None
        got = got if isinstance(got, dict) else {}
        state = got.get("state") if got.get("state") in STATES else UNKNOWN
        out[row["doc_id"]] = {"state": state, "docnum": str(got.get("docnum") or row["docnum"]), "checked_at": at}
    return out
