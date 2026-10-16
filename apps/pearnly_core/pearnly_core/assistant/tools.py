"""智能体工具：声明、汇总与派发（ADR-009 / ADR-019）。

- 每个工具是一条声明 `{"name", "label", "description", "input_schema", "handler"}`，说明和实现放在一处，
  `label` 是对话里那一行步骤显示的英文原文（译文进声明它的 App 的术语表），
  智能体配置（给模型看的工具表）与服务端派发都从这同一份生成，不会两边对不上。
- 共用工具在 `CORE_TOOLS`；各产品在自己 `hooks.py` 声明
  `pearnly_assistant_tools = ["<app>.<module>.TOOLS"]`（指向本产品的声明列表）。
- 改全公司共用设置、定时安排的工具声明 `"approval"`：`True` 每次都要用户在对话里点「允许」才执行；
  也可以是参数名列表，带了其中任一个（即要写）才要（ADR-019，用户 2026-10-10：经授权后代为操作）。
  卡片上的问句是 `"ask"`；问法随参数变时另写 `"ask_when": {参数名: 问句}`，给了该参数就用那句。
  出卡片前要先核对的另写 `"refuse": "<函数路径>"`（函数(参数) → 交回模型的拒绝结果文本或 None）：核出要拒绝的不出卡片、
  不执行工具，直接把那段结果交回模型（`refusal`；2026-10-16：存公司规则时模型说给用户的对方与单据对不上，不该先让人点「允许」）。
- 进行中的说法（对话底部的工作状态行、正在执行的那一步，如「正在识别单据」）另在 `pearnly_assistant_step_names`
  声明 `{工具名: 英文原文}`（共用工具在 `CORE_STEP_NAMES`），译文同样进声明它的 App 的术语表；没声明的显示 `label`。
- 工具一律在本站服务端、以当前登录用户身份执行：权限、租户、套账与数据范围沿用各产品现有服务的
  校验，工具只调已有服务，不绕过它们读库。业务拒绝用 `frappe.throw`，作为 `is_error` 结果交回模型。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import config


def whoami(_tool_input) -> str:
    user = frappe.session.user
    return json.dumps({
        "user": user,
        "full_name": frappe.utils.get_fullname(user),
        "product": config.product(),
        "company": frappe.conf.get("pearnly_company_name") or "",
        "language": frappe.local.lang or "",
    }, ensure_ascii=False)


def tax_id_lookup(tool_input) -> str:
    """税务局公开登记信息（同 ERP 录对方档、pearnly.com 税号工具）：缓存 7 天，查不到给原因，不抛错。"""
    from pearnly_core.revenue_department import service

    result = service.lookup(tool_input.get("tax_id"), tool_input.get("branch") or 0)
    return json.dumps(result, ensure_ascii=False, default=str)


CORE_TOOLS = [
    {"name": "pearnly_whoami",
     "label": "Checked your account",
     "description": "Return who the signed-in Pearnly user is: user id, full name, product, company and language. "
                    "Call it when you need to address the user or check which product and company you are working in.",
     "input_schema": {"type": "object", "properties": {}},
     "handler": "pearnly_core.assistant.tools.whoami"},
    {"name": "pearnly_tax_id_lookup",
     "label": "Looked up a tax ID",
     "description": "Look up a Thai 13-digit tax ID in the Revenue Department's public register: registered name, "
                    "head office or branch, address, VAT registration date. Use it whenever the user asks who a tax ID "
                    "belongs to or wants a counterparty's official name or address. branch is the branch number "
                    "(0 = head office). error not_vat_registered means the ID exists but is not VAT-registered; "
                    "rd_unreachable means the Revenue Department did not answer, so say so and suggest trying later.",
     "input_schema": {"type": "object", "properties": {
         "tax_id": {"type": "string"}, "branch": {"type": "integer", "minimum": 0}}, "required": ["tax_id"]},
     "handler": "pearnly_core.assistant.tools.tax_id_lookup"},
]


# 共用工具进行中的说法（见文件头）
CORE_STEP_NAMES = {
    "pearnly_whoami": "Checking your account",
    "pearnly_tax_id_lookup": "Looking up a tax ID",
}


def declared() -> list[dict]:
    found = {tool["name"]: tool for tool in CORE_TOOLS}
    for path in frappe.get_hooks("pearnly_assistant_tools") or []:
        for tool in frappe.get_attr(path) or []:
            found[tool["name"]] = tool
    return [found[name] for name in sorted(found)]


def labels() -> dict[str, str]:
    """对话里步骤行显示的名字（按当前用户语言）；没写 label 的工具显示原名。"""
    return {t["name"]: _(t["label"]) if t.get("label") else t["name"] for t in declared()}


def step_names() -> dict[str, str]:
    """各工具进行中的说法（英文原文）：共用的加上各产品 `pearnly_assistant_step_names` 声明的，只留已声明的工具。"""
    found = dict(CORE_STEP_NAMES)
    for path in frappe.get_hooks("pearnly_assistant_step_names") or []:
        found.update(frappe.get_attr(path) or {})
    names = {t["name"] for t in declared()}
    return {name: text for name, text in found.items() if name in names and text}


def steps() -> dict[str, dict[str, str]]:
    """对话页的步骤名（按当前用户语言）：`done` 做完的说法（同 `labels`），`running` 进行中的说法，`ask` 要点「允许」的工具
    卡片上那句问话（`ask`，没写用步骤名）——点了「拒绝」的那一步照它写成没做的样子，不写成做完的说法（第 8 轮测试机：
    拒绝存公司规则后，步骤仍写「记下了公司选择」）。"""
    return {"done": labels(), "running": {name: _(text) for name, text in step_names().items()},
            "ask": {t["name"]: _(t.get("ask") or t.get("label") or t["name"]) for t in declared() if t.get("approval")}}


def _given(values: dict, key: str) -> bool:
    """这次调用给了这个参数（False / 0 也算给了；空串、空表不算）。"""
    return values.get(key) not in (None, "", {}, [])


def needs_approval(name: str, tool_input) -> bool:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    gate = (tool or {}).get("approval")
    if isinstance(gate, (list, tuple)):
        values = tool_input if isinstance(tool_input, dict) else {}
        return any(_given(values, key) for key in gate)
    return bool(gate)


# 卡片只显示前几行，其余合成一行「还有 N 项」（对话页 approval-bar，卡片规矩：4–5 项、卡内不滚动，用户 2026-10-13）：
# 这里照实给全，N 才数得对；上限只挡异常大的列表。
PREVIEW_ROWS = 100


def approval_view(name: str, tool_input) -> dict:
    """等「允许」时卡片上显示的：这一步要做什么（`ask_when` 里给了参数的那句，否则 `ask`，没写就用步骤名）和要改哪几项（`preview`：
    [{field, before, after}]，由工具的 `preview` 按当前设置现算；没写或算不出就列参数）。不写任何东西。"""
    tool = next((t for t in declared() if t["name"] == str(name or "")), None) or {}
    values = tool_input if isinstance(tool_input, dict) else {}
    rows = None
    if tool.get("preview"):
        try:
            rows = frappe.get_attr(tool["preview"])(values)
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Assistant approval preview failed")
    if rows is None:
        rows = [{"field": key, "before": "", "after": value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}
                for key, value in values.items()]
    # 问句跟着这次改的内容走：`ask_when` 是 {参数: 问句}，给了哪个参数就问哪句（按声明顺序取第一个），
    # 都没给才用 `ask`。例：定时任务只改提醒时刻，不该问「启用还是停用」（2026-10-13）。
    ask = next((text for key, text in (tool.get("ask_when") or {}).items() if _given(values, key)), None)
    ask = ask or tool.get("ask") or tool.get("label") or str(name or "")
    return {"ask": _(ask), "preview": rows[:PREVIEW_ROWS]}


def refusal(name: str, tool_input) -> str | None:
    """要「允许」的工具出卡片前的核对（工具声明的 `refuse`）：要拒绝回交给模型的结果文本（按出错交回），否则 None。
    只读、不写；核对本身出错按不拒绝处理（照常出卡片，工具执行时自己会再核一遍）。"""
    tool = next((t for t in declared() if t["name"] == str(name or "")), None) or {}
    if not tool.get("refuse"):
        return None
    try:
        text = frappe.get_attr(tool["refuse"])(tool_input if isinstance(tool_input, dict) else {})
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Assistant approval check failed")
        return None
    return str(text) if text else None


REFUSED = ("not_saved", "not_started", "not_exported", "failed")


def _refused(text: str) -> bool:
    """业务上没办成（工具回 status not_saved 等）也按出错交回：对话里那一步显示失败，不只靠模型转述。"""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, dict) and data.get("status") in REFUSED


def run(name: str, tool_input) -> tuple[str, bool]:
    tool = next((t for t in declared() if t["name"] == str(name or "")), None)
    if not tool:
        return _("This tool is not available on this site."), True
    try:
        text = str(frappe.get_attr(tool["handler"])(tool_input if isinstance(tool_input, dict) else {}))
        return text, _refused(text)
    except (frappe.ValidationError, frappe.PermissionError) as err:
        frappe.db.rollback()
        return str(err) or _("The request was refused."), True
