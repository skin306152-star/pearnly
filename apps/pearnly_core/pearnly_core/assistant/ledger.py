"""本对话账套（A 线 11-12，R2-5：右栏显示 T.8，CC 却按票据日期另判写哪本、问了答了没记成目标，卡死）。

用户已给定的上下文是权威输入（用户 11-11「对话框都选了目标帐套，还要问写入哪个帐套干嘛」）：对话的公司 + 月份 + 分店定出
「本对话账套」，存在会话表 `project_ledger`（产品给的不透明编号，空 = 未定）。右栏、给 CC 的说明、识别缺省目标、候选、写入闸、
代录全部只认它；算出 0 本或多本才问一次、记在会话上。

- 产品在 provider（hooks `pearnly_assistant_projects`，同 `projects`）可选声明 ``LEDGER = True`` 与
  ``ledgers(ref, period, scope, user) -> {"options": [{"id", "label", "covers"}], "default": "<id 或空>"}``：这家（该分店）可选的
  账套，覆盖本月的在前（``covers``）；恰好能定一本才有默认。没声明的产品（DMS）、没挂公司的对话、``ledgers`` 回 None 的公司
  （这家不用本对话账套，例如 ERP 不接 Express 的公司）这里一律回 None。
  provider 出错记日志、当作没有：不挡发话。
- :func:`current`（只读版 :func:`resolved`，读接口用）：存的仍在可选里就用存的（用户选过的不被冲掉）；否则按默认补算，``store`` 时存下（不改最后活动时间），
  没有默认就是未定（``needed``）。老对话发话时（`projects.note`）补算一次。
- :func:`offered`：新建页（还没有会话）按公司 + 月份 + 分店读默认与可选；选的随建会话交来，:func:`initial` 核对后存。
- :func:`kept`：改月份 / 分店（`sessions.move`）时存什么：给了 ``wanted`` 就要在新的可选里；没给，存的那本仍可选就留，否则重算。
- :func:`choose`：页面（右栏下拉经 `sessions.move`）与 CC 工具 ``pearnly_conversation_ledger``（:func:`tool_choose`，只认用户在
  本对话最近一张提问卡上选的那本，同 `sessions.file_chosen`）共用；改了推 `pearnly.project` move（带 ``ledger``）。
- :func:`note`：给 CC 的说明段（英文、只给模型），由 `projects.note` 接在产品公司说明后面；账套变了说明就变、哈希变、重带。
- 依赖方向：`sessions` → `projects` → 本模块 → `sidebar`；不导入 `sessions`、`projects`（provider 按同一个 hook 自己取）。
"""
import importlib

import frappe
from frappe import _

from pearnly_core.assistant import config, context, contracts, sidebar

SESSION = context.SESSION
HOOK = "pearnly_assistant_projects"  # 同 `projects.HOOK`
FIELD = "project_ledger"
ID_MAX = 400  # 会话表字段长度（Cowork 是「连接|账套键」，账套键是 Express 的路径）
OPTIONS_MAX = 50
SET, NEEDED = "set", "needed"
# 设不上的原因（只给模型）
NOT_AVAILABLE = "not_available"      # 这个产品没有本对话账套 / 对话没挂公司
NOT_CHOSEN = "not_chosen_by_user"    # 不是用户在最近一张提问卡上选的
NOT_AN_OPTION = "not_an_option"      # 不在这家这个月可选的账套里


def _provider():
    names = frappe.get_hooks(HOOK) or []
    return importlib.import_module(names[0]) if names else None


def declared(found=None) -> bool:
    """这个产品有没有本对话账套（provider 声明了 ``LEDGER`` 与 ``ledgers``）。"""
    found = found if found is not None else _provider()
    return bool(found and getattr(found, "LEDGER", False) and callable(getattr(found, "ledgers", None)))


def _ledger_id(value) -> str:
    text = str(value or "").strip()
    return text if text and len(text) <= ID_MAX and text.isprintable() else ""


def options_of(project, period, scope, user, found=None) -> dict | None:
    """``{"options": [{"id", "label", "covers"}], "default": id 或 ""}``；产品没声明、没挂公司、provider 出错回 None。
    默认不在可选里的当作没有默认；编号重复的只留第一条。"""
    found = found if found is not None else _provider()
    if not project or not declared(found):
        return None
    try:
        raw = found.ledgers(project, period or "", scope or "", user)
    except Exception:
        frappe.log_error(title="Assistant ledger options failed")
        return None
    if raw is None:
        return None  # 这家不用本对话账套（ERP 不接 Express 的公司：MR.ERP 账本、纯 ERP 公司），同产品没声明
    options, seen = [], set()
    for item in raw.get("options") or ():
        ledger = _ledger_id((item or {}).get("id"))
        if not ledger or ledger in seen:
            continue
        seen.add(ledger)
        label = str(item.get("label") or "").strip()
        if not label:
            continue  # 没有名称的不列：账套编号是内部编号，不当名称给人看、给 CC 复述（A 线 11-15）
        options.append({"id": ledger, "label": label, "covers": bool(item.get("covers"))})
        if len(options) >= OPTIONS_MAX:
            break
    default = _ledger_id(raw.get("default"))
    return {"options": options, "default": default if default in seen else ""}


def canonical(project, value, found=None) -> str:
    """存下的编号此刻的写法：provider 声明了 ``canonical_ledger(project, value)`` 就经它映射（Cowork 11-12 以前的
    「连接|键」→ 与电脑无关的编号，A 线 11-12），没声明、出错照原样。"""
    text = str(value or "")
    found = found if found is not None else _provider()
    mapping = getattr(found, "canonical_ledger", None) if found else None
    if not text or not project or not callable(mapping):
        return text
    try:
        return _ledger_id(mapping(project, text)) or text
    except Exception:
        frappe.log_error(title="Assistant ledger mapping failed")
        return text


def same(project, left, right) -> bool:
    """两个本对话账套编号是不是同一本（写入计划记的与此刻的；旧编号经 :func:`canonical` 映射后再比）。"""
    left, right = str(left or ""), str(right or "")
    if left == right:
        return True
    found = _provider()
    return bool(left and right) and canonical(project, left, found) == canonical(project, right, found)


def _view(found, chosen) -> dict:
    label = next((item["label"] for item in found["options"] if item["id"] == chosen), "")
    return {"status": SET if chosen else NEEDED, "id": chosen, "label": label, "options": found["options"]}


def resolved(row) -> dict | None:
    """这段对话此刻的账套，只读不存（读接口用）：``{"status": "set" | "needed", "id", "label", "options"}``；产品没声明、
    没挂公司回 None。存的失效（账套从目录消失、改了月份）按默认算。"""
    project = row.get("project") or ""
    found = options_of(project, row.get("period") or "", row.get("project_scope") or "",
                       row.get("user") or frappe.session.user)
    if found is None:
        return None
    stored = str(row.get(FIELD) or "")
    ids = {item["id"] for item in found["options"]}
    if stored and stored not in ids:
        stored = canonical(project, stored)  # 旧编号读时映射一次（`current` 存下新编号）
    chosen = stored if stored in ids else found["default"]
    return _view(found, chosen)


def current(row, store=True) -> dict | None:
    """同 :func:`resolved`；``store`` 时把补算的结果存下（不改最后活动时间）。"""
    found = resolved(row)
    if found is None:
        return None
    if store and found["id"] != str(row.get(FIELD) or "") and row.get("name"):
        frappe.db.set_value(SESSION, row.get("name"), FIELD, found["id"], update_modified=False)
        row[FIELD] = found["id"]
    return found


def initial(project, period, scope, user, wanted=None) -> str:
    """新对话 / 新代录会话开在这家这个月时先存哪本：新建页选了（``wanted``，测试机1 11-12：第一句之前就能选）要在这家这个月
    可选的里，不在按请求错误拒绝（同 :func:`kept`）；没选能定就是默认，否则空。"""
    found = options_of(project, period, scope, user)
    if wanted is not None and str(wanted).strip():
        ledger = _ledger_id(wanted)
        if not found or ledger not in {item["id"] for item in found["options"]}:
            raise contracts.ContractError("invalid ledger")
        return ledger
    return found["default"] if found else ""


def offered(project, period, scope, user) -> dict:
    """新建页要的（还没有会话，`sessions.ledger_options`，只读）：``{"default", "options"}``；这家没有本对话账套（产品没声明、
    provider 出错、这家不用）时都空。调用方先核过本人能用这家（`projects.placement`）。"""
    found = options_of(project, period, scope, user)
    return {"default": found["default"], "options": found["options"]} if found else {"default": "", "options": []}


def kept(row, project, period, scope, wanted=None) -> str | None:
    """改月份 / 分店时存什么（`sessions.move`）：产品没声明回 None（不写这一列）。``wanted`` 给了要在新的可选里，不在按请求错误
    拒绝；没给，存的那本仍在新的可选里就保留（用户选过的不被冲掉），否则按新默认。"""
    found = options_of(project, period, scope, row.get("user") or frappe.session.user)
    if found is None:
        # 产品没声明、对话在「最近」、provider 出错：这一列不动（出错不冲掉用户选过的）
        if wanted:
            raise contracts.ContractError("invalid ledger")
        return None
    ids = {item["id"] for item in found["options"]}
    if wanted is not None and str(wanted).strip():
        ledger = _ledger_id(wanted)
        if ledger not in ids:
            raise contracts.ContractError("invalid ledger")
        return ledger
    stored = str(row.get(FIELD) or "")
    return stored if stored in ids else found["default"]


def _row(session_id, user=None):
    sid = contracts.session_id(session_id)
    return frappe.db.get_value(SESSION, {"session_id": sid, "user": user or frappe.session.user,
                                         "engine_code": config.engine_code()},
                               ["name", "session_id", "user", "project", "period", "project_scope", FIELD,
                                "operator_task"], as_dict=True)


def _session() -> str:
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def of_session(session_id=None) -> dict | None:
    """本人这段对话的账套（:func:`current` 的样子，补算的存下）；没给编号取 CC 此刻执行工具的那段对话。不在对话里、不是本人的、
    产品没声明、没挂公司回 None。"""
    sid = str(session_id or _session())
    if not sid:
        return None
    try:
        row = _row(sid)
    except contracts.ContractError:
        return None
    return current(row) if row else None


def of_task(task) -> dict | None:
    """代录任务的会话（``operator_task``）的账套；任务没有会话回 None。代录交单在任务主人的请求里执行。"""
    if not task:
        return None
    row = frappe.db.get_value(SESSION, {"operator_task": str(task)},
                              ["name", "session_id", "user", "project", "period", "project_scope", FIELD,
                               "operator_task"], as_dict=True)
    return current(row) if row else None


def view(session_id) -> dict:
    """右栏下拉要的（`api.ledgers`，GET，只读不存）：``{"current": 编号或空, "options": [...]}``；没有本对话账套时 options 为空。"""
    row = _row(session_id)
    if not row:
        frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
    found = resolved(row)
    return {"current": found["id"], "options": found["options"]} if found else {"current": "", "options": []}


def announce(row, ledger) -> None:
    sidebar.project(row.get("user") or frappe.session.user, "move", session=row.get("session_id"),
                    project=row.get("project") or "", period=row.get("period") or "",
                    scope=row.get("project_scope") or "", ledger=ledger)


def choose(session_id, ledger_id, by="page") -> dict:
    """把这段对话的账套设成 ``ledger_id``（要在这家这个月可选的里）。``by="cc"``：CC 工具设的，只认用户在本对话最近一张提问卡上
    选的那本（编号或名称，`ask.names_one_of`）。回 ``{"status": "set", "ledger", "label"}`` 或 ``{"status": "refused", "reason"}``。
    不改最后活动时间。"""
    try:
        row = _row(session_id)
    except contracts.ContractError:
        row = None
    if not row:
        frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
    found = resolved(row)
    if found is None:
        return {"status": "refused", "reason": NOT_AVAILABLE}
    wanted = str(ledger_id or "").strip()
    picked = next((item for item in found["options"] if item["id"] == wanted), None) \
        or next((item for item in found["options"] if item["label"] == wanted), None)
    if not picked:
        return {"status": "refused", "reason": NOT_AN_OPTION}
    if by == "cc":
        from pearnly_core.assistant import ask, toolrun

        if not ask.names_one_of(toolrun.last_answers(row.session_id), (picked["id"], picked["label"])):
            return {"status": "refused", "reason": NOT_CHOSEN}
    if str(row.get(FIELD) or "") != picked["id"]:
        frappe.db.set_value(SESSION, row.name, FIELD, picked["id"], update_modified=False)
        row[FIELD] = picked["id"]
    announce(row, picked["id"])
    return {"status": SET, "ledger": picked["id"], "label": picked["label"]}


def tool_choose(tool_input) -> str:
    """CC 工具 ``pearnly_conversation_ledger``。"""
    import json

    session = _session()
    if not session:
        return json.dumps({"status": "refused", "reason": "no_conversation"})
    return json.dumps(choose(session, (tool_input or {}).get("ledger"), by="cc"), ensure_ascii=False)


# 给 CC 的说明（只给模型，不经 _()）
SET_NOTE = ("<conversation_account_set>This conversation's account set is {label}. Every write in this "
            "conversation goes into it: recognizing, pushing, read-backs and drafts use it unless the user names "
            "another. Never choose an account set by the document date and never ask which account set to write to; "
            "the server refuses any other account set. The user changes it in the conversation's side panel; if the "
            "user asks here to switch, ask with ask_user (option labels exactly the names below) and set it with "
            "pearnly_conversation_ledger.{others}</conversation_account_set>")
NEEDED_NOTE = ("<conversation_account_set>This conversation's account set is not decided yet. Before the first write, "
               "ask the user once with ask_user which one to write to (one question, each option label exactly one of "
               "these names; at most 4, Other lets them type another), then set it with pearnly_conversation_ledger "
               "(ledger = its name exactly as listed); after that every write goes into it and you do not ask again. "
               "Account sets: "
               "{options}</conversation_account_set>")
NONE_NOTE = ("<conversation_account_set>This company has no account set this conversation can write to, so nothing "
             "can be written yet.</conversation_account_set>")


def _listed(options) -> str:
    return "; ".join(f"{item['label']}{' (covers this month)' if item['covers'] else ''}"
                     for item in options)


def note(found) -> str:
    """:func:`current` 的结果给 CC 的说明段；None（没有本对话账套）回空串。"""
    if not found:
        return ""
    if found["status"] == SET:
        others = [item for item in found["options"] if item["id"] != found["id"]]
        return SET_NOTE.format(label=found["label"],
                               others=f" Other account sets: {_listed(others)}." if others else "")
    if not found["options"]:
        return NONE_NOTE
    return NEEDED_NOTE.format(options=_listed(found["options"]))


__all__ = ["FIELD", "NEEDED", "NOT_AN_OPTION", "NOT_AVAILABLE", "NOT_CHOSEN", "SET", "canonical", "choose", "current", "declared",
           "initial", "kept", "note", "of_session", "of_task", "offered", "options_of", "resolved", "same", "tool_choose",
           "view"]
