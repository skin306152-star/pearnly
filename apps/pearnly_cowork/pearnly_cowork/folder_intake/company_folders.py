"""公司主档的单据文件夹（ADR-020）：一家公司在每台电脑上一个文件夹，全所共用。

公司主档在 `companies` 域；本模块不导入它（`companies` 依赖本域，反过来会成环），公司编号在这里只是不透明的串。

- 绑定只是指针：读这个文件夹的成员连接（`reader`）、根目录编号（`root_id`）、根目录下的相对路径（`folder`），外加绑定
  时的显示路径、网络路径、电脑名与谁绑的，存在私有表 `Cowork Company Folder`（只经本模块写，守卫同另三张表）。
  一行一个（公司, 读者, 读者电脑）：`binding_key = sha256(json([company, reader, reader_device]))`，同一（公司, 读者,
  读者电脑）再绑就是替换。读者电脑（ADR-021 第 4 批，`readers`）：桥交回的绑定只带成员连接，按根目录认是这条连接上的
  哪台电脑（根目录编号是那台电脑本机路径的摘要，CC 桌面版加根目录后立刻快速同步）；部署前绑的由补丁回填。按字段找，
  不按键找：电脑改挂（`readers.moved`）后键不变、字段变了。
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

from pearnly_cowork.folder_intake import agent, contracts, readers
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
    try:
        hint, connection = contracts.parse_reader(binding.get("reader"))
    except contracts.ContractError as error:
        _error(error.code)
    doc = rules._connection(connection)  # 录入权限 + 连接使用授权 + 支持的适配器 + 未换绑
    try:
        root = contracts.root_id(binding.get("root_id"))
        sub = contracts.folder(binding.get("folder"))
        label = contracts.text(binding.get("label"), maximum=255, optional=True)
    except contracts.ContractError as error:
        _error(error.code)
    share = str(binding.get("share") or "").strip()
    # 桥以后可能直接带电脑编号（第 5 批），就用它（要还在这条连接上）；只带连接的按根目录认
    device, _connection = readers.resolve(contracts.reader_ref(binding.get("reader_device") or hint, doc.name), root)
    return {"reader": doc.name, "reader_device": device, "root_id": root, "folder": sub, "label": label,
            "share": share if share.startswith("\\\\") and len(share) <= 500 else "",
            "device": contracts.device(binding.get("device"))}


def bind(company, binding, user) -> dict:
    """写入或替换这家公司在这台电脑（读者）上的文件夹；回这一行此刻的样子（`view` 的一项）。调用方已核过公司存在、
    当前用户能录入。"""
    company = _company(company)
    values = _clean(binding)
    name = _existing(company, values["reader"], values["reader_device"])
    if name:
        doc = frappe.get_doc(COMPANY_FOLDER, name, for_update=True)
    else:
        if frappe.db.count(COMPANY_FOLDER, {"company": company}) >= PER_COMPANY:
            _error("cowork.folder.invalid")
        doc = frappe.new_doc(COMPANY_FOLDER)
        doc.update({"binding_key": binding_key(company, values["reader"], values["reader_device"]),
                    "company": company})
    doc.update({**values, "bound_by": user, "bound_at": now_datetime()})
    write(doc)
    return next((row for row in view(company) if (row["reader"], row["reader_device"])
                 == (values["reader"], values["reader_device"])), {})


def _existing(company, reader, reader_device):
    """这家公司在这台电脑这个成员上的那一行：先按电脑找，再找还没记电脑的老行（补丁认不出电脑时留下的）。"""
    for device in dict.fromkeys((reader_device or "", "")):
        found = frappe.get_all(COMPANY_FOLDER, filters={"company": company, "reader": reader, "reader_device": device},
                               pluck="name", limit_page_length=1)
        if found:
            return found[0]
    return None


def unbind(company, reader, reader_device=None) -> bool:
    """移除这家公司在这台电脑（读者）上的文件夹；没有回 False。根目录授权、定时任务都不动。``reader`` 可以是读者编号
    （「电脑|连接」）；只给连接（第 6 批之前的页面）时这家公司在这条连接上只有一行才删，好几台电脑各有一行时不猜。"""
    try:
        device, connection = contracts.parse_reader(reader)
    except contracts.ContractError:
        return False
    device = device or str(reader_device or "")
    filters = {"company": _company(company), "reader": connection}
    if device:
        filters["reader_device"] = device
    found = frappe.get_all(COMPANY_FOLDER, filters=filters, pluck="name", limit_page_length=2)
    if len(found) != 1:
        return False
    frappe.db.delete(COMPANY_FOLDER, {"name": found[0]})
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
    """这家公司各台电脑上的文件夹：`[{reader, reader_device, reader_id, device, label, root_id, folder, share, state,
    bound_by, bound_at, reported}]`。`reader` 仍是成员连接（第 6 批之前的页面按它认「本机这一行」），`reader_id` 是
    读者编号（电脑|连接）。`reported` 是这台电脑为这个根目录上报的下级文件夹名（找月份子文件夹用，`None` = 没报）。
    每台电脑读一次最近同步的情况（`states` 可传入请求内缓存，按读者编号）。"""
    states = {} if states is None else states
    result = []
    for row in rows(company):
        reader = readers.ref(row)
        if reader not in states:
            states[reader] = readers.state(*readers.of(row))
        state = states[reader]
        roots = state.get("roots") or []
        reported = next((item for item in roots if isinstance(item, dict) and item.get("id") == row.root_id), None)
        result.append({
            "reader": row.reader, "reader_device": row.reader_device or "", "reader_id": reader,
            "root_id": row.root_id,
            "folder": row.folder or "", "share": row.share or "",
            "label": contracts.folder_label(roots, row.root_id, row.folder or "", row.label or ""),
            "device": state.get("device") or row.device or "",
            "state": contracts.folder_state(agent.fresh(state), roots, row.root_id, row.folder or "",
                                            _bound_seconds(row)),
            "bound_by": row.bound_by or "", "bound_at": str(row.bound_at or ""),
            "stored_label": row.label or "", "stored_device": row.device or "",
            "reported": list(reported.get("folders") or []) if reported and "folders" in reported else None})
    return result


def roots_in_use(reader, device="") -> list:
    """这台电脑这个成员（读者）还被用着的根目录：它读的定时任务（停用的也算）与任何公司的文件夹。不按用户过滤。
    还没记电脑的老行（空）也算这条连接上每台电脑的：宁可晚收回，不把别人在用的收掉。"""
    found = frappe.db.sql(f"""SELECT DISTINCT root_id FROM `tab{RULE}` WHERE IFNULL(NULLIF(reader, ''), connection) = %s
        AND IFNULL(reader_device, '') IN (%s, '')
        UNION SELECT DISTINCT root_id FROM `tab{COMPANY_FOLDER}` WHERE reader = %s
        AND IFNULL(reader_device, '') IN (%s, '')""", (reader, device or "", reader, device or ""))
    return sorted({row[0] for row in found if row and row[0]})
