"""手排顺序合成一份（统一对话框架 14.8，主会话 11-04 审定 U-Q6）：代录侧栏原来另存在个人排列 ``pearnly_cc_layout`` 的
``operator`` 项（``order/companies``、``order/pinned``、``order/group:<公司>``，按任务号）；代录任务现在就是会话、进同一个
会话列表，按 ``任务号 → 会话编号`` 换过去并进 ``order``（直写的在前，代录的接在后面，各自的先后不变），再删 ``operator`` 项。
推翻 11-03「任务号不进对话的手排顺序」：进的是会话编号。要在补建会话（v0_0_9）之后跑。幂等：没有 ``operator`` 项的不动。"""
import json

import frappe

KEY = "pearnly_cc_layout"
TASK = "Pearnly Express Operator Task"
PREFIX = "order/"
GROUP = "group:"


def _ids(value) -> list:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _merge(first, second) -> list:
    return list(dict.fromkeys(first + second))


def merge(prefs: dict, session_of: dict) -> dict:
    """一份个人排列：``operator`` 里 ``order/`` 开头的几项按 ``session_of``（任务号 → 会话编号）换过去并进 ``order``，删掉
    ``operator``。换不到的任务号（任务删了、没补上会话）丢掉。纯函数。"""
    operator = prefs.get("operator")
    if not isinstance(operator, dict):
        return prefs
    order = dict(prefs.get("order")) if isinstance(prefs.get("order"), dict) else {}

    def mapped(value):
        return [session_of[key] for key in _ids(value) if key in session_of]

    for field, value in operator.items():
        if not field.startswith(PREFIX):
            continue
        name = field[len(PREFIX):]
        if name == "companies":
            order["companies"] = _merge(_ids(order.get("companies")), _ids(value))
        elif name == "pinned":
            order["pinned"] = _merge(_ids(order.get("pinned")), mapped(value))
        elif name.startswith(GROUP) and isinstance(value, dict):
            mine = order.get(name) if isinstance(order.get(name), dict) else {}
            keys = _merge(_ids(mine.get("keys")), mapped(value.get("keys")))
            if not keys:
                continue
            at = max(int(mine.get("at") or 0), int(value.get("at") or 0))
            merged = {"keys": keys, "at": at}
            if mine.get("top"):
                merged["top"] = mine["top"]  # 直写那一段的「取消置顶回到组顶」照旧；代录的接在后面，不再有这一段
            if mine.get("saved") or value.get("saved"):
                merged["saved"] = max(int(mine.get("saved") or 0), int(value.get("saved") or 0))
            order[name] = merged
    out = {part: value for part, value in prefs.items() if part != "operator"}
    if order:
        out["order"] = order
    return out


def execute():
    if not frappe.db.table_exists(TASK):
        return
    from pearnly_core.assistant import prefs as layout

    rows = frappe.get_all("DefaultValue", filters={"defkey": KEY}, fields=["name", "parent", "defvalue"],
                          limit_page_length=0)  # bounded: 一次性迁移，每人一行
    for row in rows:
        try:
            value = json.loads(row.defvalue or "{}")
        except ValueError:
            continue
        if not isinstance(value, dict) or "operator" not in value:
            continue
        tasks = frappe.get_all(TASK, filters={"owner": row.parent, "session_id": ["is", "set"]},
                               fields=["name", "session_id"], limit_page_length=0)  # bounded: 一个人的任务
        merged = merge(value, {task.name: task.session_id for task in tasks})
        if isinstance(merged.get("order"), dict):
            layout._trim_groups(merged["order"])
        frappe.db.set_value("DefaultValue", row.name, "defvalue",
                            json.dumps(merged, ensure_ascii=False, separators=(",", ":")), update_modified=False)
    frappe.clear_cache()
