"""ERP 单据 → MR.ERP 导入直写的规范载荷，与导入结论 → 单据账本栏（纯函数：不连库、不依赖 Frappe、不做 IO）。

规范载荷与 Cowork 冻结进推送账本的同一个形状（`pearnly_cowork.push.application.service._prepare_mrerp`）::

    {"histories": [票面], "mappings": {"accounts": [], "clients": [], "_own_tax_id": 本公司税号,
                                      "_mrerp_menus": {单据类型: 菜单号}, "_mrerp_defaults": {主档字段: 代码}}}

票面是 Cowork 的拍平票面（`express_rules.push.history.flatten_history_for_mrerp` 出的那种）里 MR.ERP 规则读到的键，由 ERP 交给
Express 写账规则的单据（`express.contracts.document`）换来：本方 = ERP 公司，对方 = 供应商 / 客户；方向明确给（采购 / 销售）；
付款按单据（采购只有赊购，现购停下，同 Cowork 的「MR.ERP purchases can only be entered on credit.」）；采购货 / 费按单据种类。
``id`` 用单据标识（单号 + 建单时刻，`express.push.ref`）的摘要：MR.ERP 单号的序号取它末 8 位十六进制（`derive_mrerp_invoice_no`），
同一张单重推得同一个号（导入「单号已存在」时回读认领），不同单不撞。
"""
from __future__ import annotations

import hashlib
import json

LEDGER_MRERP = "mrerp"
# 单据上的账本原因码（``ledger_reason``）：MR.ERP 这一条自己的
NOT_CONFIGURED = "mrerp.not_configured"        # 公司没选 MR.ERP 账套 / 连接没网址
NO_LOGIN = "mrerp.login_missing"               # 连接还没在 CC 桌面版里授权登录
PURCHASE_CASH = "mrerp.purchase_cash"          # 现购：MR.ERP 导入只有赊购
ACCOUNT_BUSY = "mrerp.account_busy"            # 这个 MR.ERP 账号正被代录占着：等账号（不算失败）
IMPORTING = "mrerp.importing"                  # 已领、正在导入（进程死掉过期后转结果不明）
UNKNOWN = "mrerp.result_unknown"               # 导入受理了却查不到 / 发出后出错：可能已落库，不自动重推
CHOICE = "choice_needed:mrerp"                 # 要人选一次导入菜单 / 主档默认值（以 `express.contracts.CHOICE` 开头）
# 每分钟会自己好的（代录放开账号）
RETRY_REASONS = (ACCOUNT_BUSY,)
IMPORTING_MINUTES = 15  # 导入任务最多 600 秒；超过这么久还在「正在导入」就是进程死了
KINDS = ("purchase", "sales")
EXPENSE_KINDS = ("expense", "service")  # 采购单种类 → 赊购费用（其余赊购货品）


def history_id(ref: str) -> str:
    return "ERP-" + hashlib.sha256(str(ref or "").encode("utf-8")).hexdigest()[:24]


def history(body: dict, *, company_name: str, doc_kind: str = "") -> dict:
    """ERP 单据（`express.contracts.document` 的形状）→ MR.ERP 规则读的拍平票面。"""
    direction = body["direction"]
    own = {"name": str(company_name or "").strip(), "tax": body["company"]["tax_id"]}
    party = {"name": body["party"]["name"], "tax": body["party"]["tax_id"]}
    seller, buyer = (party, own) if direction == "purchase" else (own, party)
    items = [{"name": item["name"], "qty": item["qty"], "unit_price": item["price"], "amount": item["amount"]}
             for item in body["items"]]
    fields = {"invoice_number": body["number"], "date": body["date"], "subtotal": body["subtotal"], "vat": body["vat"],
              "total_amount": body["total"], "direction": direction, "items": items,
              "seller_name": seller["name"], "seller_tax": seller["tax"],
              "buyer_name": buyer["name"], "buyer_tax": buyer["tax"],
              "posting_payment_manual": "cash" if body["paid"] else "credit"}
    if direction == "purchase":
        fields["posting_item_type_manual"] = "expense" if doc_kind in EXPENSE_KINDS else "goods"
    return {"id": history_id(body["ref"]), "invoice_no": body["number"], "invoice_date": body["date"],
            "total_amount": body["total"], "direction": direction, "fields": fields}


def payload(flat: dict, *, own_tax_id: str, menus: dict, defaults: dict) -> dict:
    """规范载荷（纯 JSON，同 Cowork 冻结进账本前的 ``json.dumps(..., default=str)``）。"""
    mappings = {"accounts": [], "clients": []}
    if own_tax_id:
        mappings["_own_tax_id"] = own_tax_id
    mappings["_mrerp_menus"] = dict(menus or {})
    mappings["_mrerp_defaults"] = dict(defaults or {})
    return json.loads(json.dumps({"histories": [flat], "mappings": mappings}, ensure_ascii=False, default=str))


def blocked(body: dict) -> str:
    """导入前就定得下的停下原因（空串 = 可以导入）。"""
    if body["direction"] == "purchase" and body["paid"]:
        return PURCHASE_CASH
    return ""


# 导入结论（`pearnly_core.mrerp.push.execute.execute`）里要人选一次的原因码
CHOICE_CODES = ("ERR_MRERP_MENU_CHOICE_REQUIRED", "ERR_MRERP_MENU_CHANGED",
                "ERR_MRERP_DEFAULT_CHOICE_REQUIRED", "ERR_MRERP_DEFAULT_CHANGED")


def settle(outcome: dict) -> dict:
    """导入结论 → 单据账本栏的值（状态码同 `express.contracts`：written / held / failed）。"""
    status, reason = outcome.get("status"), str(outcome.get("reason") or "")
    if status == "success":
        return {"ledger_state": "written", "ledger_docnum": str(outcome.get("docnum") or "")[:140], "ledger_reason": ""}
    if status == "unknown":
        return {"ledger_state": "held", "ledger_reason": f"{UNKNOWN}: {reason}"[:500]}
    if any(code in reason for code in CHOICE_CODES):
        return {"ledger_state": "held", "ledger_reason": CHOICE}
    return {"ledger_state": "failed", "ledger_reason": reason[:500]}


def needed(menu_view: dict, master_view: dict, direction: str) -> list:
    """此刻要人选的项（单据上停下等人选，同 `express` 的 ``ledger_needed`` 形状 ``{kind, subject, options}``）：
    ``mrerp_menu`` / 单据类型、``mrerp_default`` / 主档字段；只看这个方向会用到的（`pearnly_core.mrerp.needs`）。"""
    from pearnly_core.mrerp import needs

    picked = needs.needed({**(menu_view or {}), "defaults": master_view or {}}, direction)
    out = []
    for kind in picked["menus"]:
        options = ((menu_view or {}).get("kinds") or {}).get(kind, {}).get("options") or []
        out.append({"kind": "mrerp_menu", "subject": kind,
                    "options": [{"code": str(o["idmenu"]), "name": o["label"]} for o in options]})
    for field in picked["defaults"]:
        options = ((master_view or {}).get("fields") or {}).get(field, {}).get("options") or []
        out.append({"kind": "mrerp_default", "subject": field,
                    "options": [{"code": o["code"], "name": o["name"]} for o in options]})
    return out


__all__ = ["ACCOUNT_BUSY", "CHOICE", "IMPORTING", "LEDGER_MRERP", "NOT_CONFIGURED", "NO_LOGIN", "PURCHASE_CASH",
           "UNKNOWN", "blocked", "history", "history_id", "needed", "payload", "settle"]
