"""产品怎么接代录（多产品共用，ADR-009）：产品在自己的 ``hooks.py`` 里声明 ``pearnly_express_operator``，共用层只读它。

    pearnly_express_operator = {
        "allowed": "<点路径>",   # (user, company) -> bool：谁能对这家公司发起代录（各用自己的权限：Cowork 收单权限 + 能用这条
                                 # Express 连接；ERP 单据写权限 + 本人可见公司）
        "sources": "<点路径>",   # (company, period, offset, limit) -> {"documents": [{"doc_id", "kind", "date", "party", "ref",
                                 # "total"}], "next_offset"}：可代录的单据（Cowork 识别草稿、ERP 采购 / 销售单据），服务端分页
        "precheck": "<点路径>",  # (company, doc_ids) -> {"documents": [{"doc_id", "state": ready / choose / waiting / blocked,
                                 # "reason", "message", "needed": [{kind, subject, label, options}]}]}：按产品自己的准备逐张判
                                 # （与直写同一条路，不排写账件），要人选的一次问完
        "intake": "<点路径>",    # 可选：(company, filename, content, request_key) -> {"accepted", ...}：资料区上传一张，交识别并归到这家
                                 # 公司（Cowork）；没声明的产品（ERP：单据在 ERP 里开好）资料区不给上传
        "regroup": "<点路径>",   # 可选：(company, doc_id, group) -> {"ok"}：资料区拖到别的组
        "submit": "<点路径>",    # (company, doc_ids, batch, computer, answers) -> [{"doc_id", "status", "reason"}]：先记下预检里
                                 # 人定的（answers = {doc_id: [{kind, subject, value, label}]}），再按产品自己的准备出直写同一份
                                 # 载荷，经 ``contracts.for_operator`` 加代录标记，交进产品自己的写账队列（选定的电脑写）
        "resolve": "<点路径>",   # 可选：(company, doc_id, entered, docnum) -> {"ok"}：上次推送结果不明的单，人在 Express 里核对后
                                 # 标「已入账（单号）/ 没有入账」（同产品复核页的核对那条路）；没声明的产品回 unsupported
        "status": "<点路径>",    # (company, doc_ids) -> [{"doc_id", "state": saved / queued / not_done / cancelled / unknown,
                                 # "docnum", "reason", "ref", "party"}]：这些单在站点上现在怎样（CC 重启后恢复出来的任务以它为准
                                 # 收尾；单号、对方给页面把单据写成「单号 · 对方」）
    }
回执照产品原来的写账回执走（Cowork 推送记录、ERP 单据的 express_state），``meta.operator`` 带作废单号、空单头、自动处理的事。

没声明的站点（DMS、控制面）：一律不能发起。一个站点只装一个产品，取最后声明的那一份。批次表与接口随共用件那一步接（评估 §7）。
"""
import frappe

HOOK = "pearnly_express_operator"


def _path(key: str) -> str:
    found = frappe.get_hooks(HOOK) or {}
    values = found.get(key) if isinstance(found, dict) else None
    values = values if isinstance(values, list) else [values]
    values = [value for value in values if isinstance(value, str) and value]
    return values[-1] if values else ""


def enabled() -> bool:
    return bool(_path("allowed") and _path("sources"))


def allowed(user, company) -> bool:
    path = _path("allowed")
    return bool(path and user and user not in ("Guest", "Administrator") and company
                and frappe.get_attr(path)(user, company))


def sources(company, period, offset: int, limit: int) -> dict:
    path = _path("sources")
    return frappe.get_attr(path)(company, period, offset, limit) if path else {"documents": [], "next_offset": None}


def precheck(company, doc_ids) -> dict:
    path = _path("precheck")
    if not path:
        return {"documents": [{"doc_id": doc_id, "state": "ready"} for doc_id in doc_ids]}
    return frappe.get_attr(path)(company, list(doc_ids))


def intake(company, filename: str, content: bytes, request_key: str) -> dict:
    path = _path("intake")
    if not path:
        return {"accepted": False, "code": "unsupported"}
    return frappe.get_attr(path)(company, filename, content, request_key)


def regroup(company, doc_id: str, group: str) -> dict:
    path = _path("regroup")
    return frappe.get_attr(path)(company, doc_id, group) if path else {"ok": False, "reason": "unsupported"}


def submit(company, doc_ids, batch: dict, computer: str, answers=None) -> list:
    path = _path("submit")
    return list(frappe.get_attr(path)(company, list(doc_ids), batch, computer, answers or {})) if path else []


STATUS_STATES = ("saved", "queued", "not_done", "cancelled", "unknown")


def status(company, doc_ids) -> list:
    """这些单在站点上的真实状态（以站点为准）；产品没声明时一律 unknown（页面照旧显示「等待」，不收尾）。"""
    path = _path("status")
    if not path:
        return [{"doc_id": d, "state": "unknown", "docnum": "", "reason": ""} for d in doc_ids]
    rows = list(frappe.get_attr(path)(company, list(doc_ids)))
    return [{**row, "state": row.get("state") if row.get("state") in STATUS_STATES else "unknown"} for row in rows]


def resolve(company, doc_id: str, entered: bool, docnum: str = "") -> dict:
    """上次推送结果不明的单，人核对后标已入账 / 没入账（产品自己的核对那条路）。"""
    path = _path("resolve")
    return frappe.get_attr(path)(company, doc_id, entered, docnum) if path else {"ok": False, "reason": "unsupported"}
