"""公司主档的单据文件夹（ADR-020）：一家公司在每台电脑上一个文件夹，全所共用。

公司主档在 `companies` 域；本模块不导入它（`companies` 依赖本域，反过来会成环），公司编号在这里只是不透明的串。

- 绑定只是指针：读这个文件夹的成员连接（`reader`）、根目录编号（`root_id`）、根目录下的相对路径（`folder`），外加绑定
  时的显示路径、网络路径、电脑名与谁绑的，存在私有表 `Cowork Company Folder`（只经本模块写，守卫同另三张表）。
  一行一个（公司, 读者, 读者电脑）：`binding_key = sha256(json([company, reader, reader_device]))`，同一键再绑就是替换。
  `reader_device` 预留给多电脑方案（D1–D9）回填，现在为空。
- 授权边界不变：新授权只能在 CC 桌面版里经 Windows 原生选择框加（网页从不传路径，`pick_folder` 交回的绑定由页面原样
  转来）；读文件时服务端照旧对照电脑上报的根目录（`fetch`），电脑再对照本机的根目录。绑定时不要求根目录已经上报：
  没上报就是「等待电脑」，读不到任何东西。
- 文件夹状态不存，每次读时按那台电脑最近一次同步现算（`contracts.folder_state`）。
- `roots_in_use`：这台电脑还被用着的根目录（读它的任务，停用的也算；任何公司的文件夹），文件夹同步回复里带给
  CC 桌面版，它只收回经公司添加、连续 1 小时不在这里的根目录。
- 定时任务不跟着公司文件夹改（ADR-020：换文件夹会让任务按「处理已有文件」重跑，重复识别、重复扣费）。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from pearnly_cowork.folder_intake import agent, contracts
from pearnly_cowork.folder_intake.access import COMPANY_FOLDER, RULE, write

FIELDS = ["name", "company", "reader", "reader_device", "binding_key", "root_id", "folder", "label", "share", "device",
          "bound_by", "bound_at"]
COMPANY_MAX = 140
PER_COMPANY = 20  # 一家公司最多绑这么多台电脑


def _error(code):
    frappe.throw(_(code))


def binding_key(company, reader, reader_device="") -> str:
    return hashlib.sha256(json.dumps([company, reader, reader_device or ""], ensure_ascii=False).encode()).hexdigest()


def _company(value) -> str:
    text = str(value or "").strip()
    if not text or len(text) > COMPANY_MAX:
        _error("cowork.folder.invalid")
    return text


def _clean(binding):
    """校验绑定的形状（CC 桌面版 `pick_folder` 交回、页面原样转来的）；返回要存的值。"""
    from pearnly_cowork.folder_intake import rules  # rules 导入面大，只在真绑定时用

    if isinstance(binding, str):
        try:
            binding = json.loads(binding)
        except ValueError:
            _error("cowork.folder.invalid")
    if not isinstance(binding, dict):
        _error("cowork.folder.invalid")
    rules._require()
    doc = rules._connection(binding.get("reader"))  # 录入权限 + 连接使用授权 + 支持的适配器 + 未换绑
    try:
        root = contracts.root_id(binding.get("root_id"))
        sub = contracts.folder(binding.get("folder"))
        label = contracts.text(binding.get("label"), maximum=255, optional=True)
    except contracts.ContractError as error:
        _error(error.code)
    share = str(binding.get("share") or "").strip()
    return {"reader": doc.name, "root_id": root, "folder": sub, "label": label,
            "share": share if share.startswith("\\\\") and len(share) <= 500 else "",
            "device": contracts.device(binding.get("device"))}


def bind(company, binding, user) -> dict:
    """写入或替换这家公司在这台电脑（读者）上的文件夹；回这一行此刻的样子（`view` 的一项）。调用方已核过公司存在、
    当前用户能录入。"""
    company = _company(company)
    values = _clean(binding)
    key = binding_key(company, values["reader"], "")
    if frappe.db.exists(COMPANY_FOLDER, key):
        doc = frappe.get_doc(COMPANY_FOLDER, key, for_update=True)
    else:
        if frappe.db.count(COMPANY_FOLDER, {"company": company}) >= PER_COMPANY:
            _error("cowork.folder.invalid")
        doc = frappe.new_doc(COMPANY_FOLDER)
        doc.update({"binding_key": key, "company": company, "reader_device": ""})
    doc.update({**values, "bound_by": user, "bound_at": now_datetime()})
    write(doc)
    return next((row for row in view(company) if row["reader"] == values["reader"]), {})


def unbind(company, reader) -> bool:
    """移除这家公司在这台电脑（读者）上的文件夹；没有回 False。根目录授权、定时任务都不动。"""
    key = binding_key(_company(company), str(reader or ""), "")
    if not frappe.db.exists(COMPANY_FOLDER, key):
        return False
    frappe.db.delete(COMPANY_FOLDER, {"name": key})
    return True


def forget(company) -> None:
    """删公司时：这家公司在所有电脑上的文件夹绑定一起删。"""
    frappe.db.delete(COMPANY_FOLDER, {"company": _company(company)})


def rows(company) -> list:
    return frappe.get_all(COMPANY_FOLDER, filters={"company": _company(company)}, fields=FIELDS,
                          order_by="bound_at asc", limit_page_length=PER_COMPANY)


def _bound_seconds(row) -> float:
    if not row.get("bound_at"):
        return float("inf")
    return (now_datetime() - get_datetime(row["bound_at"])).total_seconds()


def view(company, states=None) -> list:
    """这家公司各台电脑上的文件夹：`[{reader, reader_device, device, label, root_id, folder, share, state, bound_by,
    bound_at, reported}]`。`reported` 是这台电脑为这个根目录上报的下级文件夹名（找月份子文件夹用，`None` = 没报）。
    每台电脑读一次最近同步的情况（`states` 可传入请求内缓存）。"""
    states = {} if states is None else states
    result = []
    for row in rows(company):
        if row.reader not in states:
            states[row.reader] = agent.read(row.reader)
        state = states[row.reader]
        roots = state.get("roots") or []
        reported = next((item for item in roots if isinstance(item, dict) and item.get("id") == row.root_id), None)
        result.append({
            "reader": row.reader, "reader_device": row.reader_device or "", "root_id": row.root_id,
            "folder": row.folder or "", "share": row.share or "",
            "label": contracts.folder_label(roots, row.root_id, row.folder or "", row.label or ""),
            "device": state.get("device") or row.device or "",
            "state": contracts.folder_state(agent.fresh(state), roots, row.root_id, row.folder or "",
                                            _bound_seconds(row)),
            "bound_by": row.bound_by or "", "bound_at": str(row.bound_at or ""),
            "stored_label": row.label or "", "stored_device": row.device or "",
            "reported": list(reported.get("folders") or []) if reported and "folders" in reported else None})
    return result


def roots_in_use(reader) -> list:
    """这台电脑（读者连接）还被用着的根目录：读它的定时任务（停用的也算）与任何公司的文件夹。不按用户过滤。"""
    found = frappe.db.sql(f"""SELECT DISTINCT root_id FROM `tab{RULE}` WHERE IFNULL(NULLIF(reader, ''), connection) = %s
        UNION SELECT DISTINCT root_id FROM `tab{COMPANY_FOLDER}` WHERE reader = %s""", (reader, reader))
    return sorted({row[0] for row in found if row and row[0]})
